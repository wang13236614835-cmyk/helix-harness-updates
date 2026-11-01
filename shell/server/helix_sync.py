"""Bounded portable tree transfer. Callers must authorize project access and hold
all project writer locks through apply. Imported history is not identity proof.
"""
import copy
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import stat
import tempfile
import threading
import time
import zipfile
from pathlib import Path

MAX_TOTAL = 256 * 1024 * 1024
MAX_TREE = 12 * 1024 * 1024 * 1024
MAX_FILES = 20000
MAX_MANIFEST = 16 * 1024 * 1024
SCHEMA = 'helix.portable-tree.v1'
PENDING = {}
LOCK = threading.RLock()
MANIFEST = 'helix-state.json'


class SyncError(ValueError):
    pass


def _safe_rel(rel):
    if not isinstance(rel, str) or not rel or len(rel.encode('utf-8')) > 900:
        raise SyncError('Invalid relative path')
    parts = rel.split('/')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', 'CLOCK$'} | {'COM'+str(n) for n in range(10)} | {'LPT'+str(n) for n in range(10)}
    if any(not p or p in ('.', '..') or len(p) > 255 or p[-1:] in (' ', '.') or
           any(ord(c) < 32 or c in '\\:<>"|?*' for c in p) or
           p.split('.')[0].upper() in reserved for p in parts):
        raise SyncError('Unsafe relative path: ' + rel)
    return rel


def _pid(pid):
    if not isinstance(pid, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', pid):
        raise SyncError('Invalid project id')
    return pid


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _load(path, limit=MAX_MANIFEST):
    if path.stat().st_size > limit:
        raise SyncError('Metadata exceeds limit')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _regular(path):
    # Reject every link/junction component, not just the final file.
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
            raise SyncError('Linked source or destination is forbidden: ' + str(item))
    if not stat.S_ISREG(path.stat().st_mode):
        raise SyncError('Expected regular file: ' + str(path))


def _files(root):
    if root.is_symlink() or (hasattr(root, 'is_junction') and root.is_junction()):
        raise SyncError('Linked project root')
    result = []
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            p = Path(base) / name
            if p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()):
                raise SyncError('Linked directory: ' + str(p))
        for name in files:
            p = Path(base) / name
            _regular(p)
            result.append(p)
            if len(result) > MAX_FILES:
                raise SyncError('Too many files')
    return sorted(result)


def _sha(path):
    _regular(path)
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def revision(P, pid):
    root = Path(P.pdir(_pid(pid)))
    if not (root / 'project.json').is_file():
        raise SyncError('Project does not exist')
    h = hashlib.sha256()
    # Preserve the previous revision algorithm for bridge conflict baselines.
    for p in _files(root):
        h.update(p.relative_to(root).as_posix().encode())
        with p.open('rb') as f:
            for b in iter(lambda: f.read(1024 * 1024), b''):
                h.update(b)
    return h.hexdigest()


def _canonical(manifest):
    return hashlib.sha256(_json({k: manifest[k] for k in ('schema', 'project_id', 'entries', 'bindings')})).hexdigest()


def _pointer_get(obj, pointer):
    cur = obj
    for part in pointer.strip('/').split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    return cur


def _pointer_set(obj, pointer, value):
    bits = pointer.strip('/').split('/')
    cur = obj
    for part in bits[:-1]:
        part = part.replace('~1', '/').replace('~0', '~')
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    key = bits[-1].replace('~1', '/').replace('~0', '~')
    cur[int(key) if isinstance(cur, list) else key] = value


def _escape(key):
    return str(key).replace('~', '~0').replace('/', '~1')


def export_tree(P, pid, output_dir):
    """Copy only project files, explicit witnesses/assets and manifest-listed library.
    Does not recursively copy external source repositories. Originals stay unchanged.
    Output must not exist. Missing referenced files abort, never silently omit.
    """
    pid = _pid(pid)
    root = Path(P.pdir(pid)).resolve()
    out = Path(output_dir).absolute()
    if out.exists() or out.resolve().is_relative_to(root):
        raise SyncError('Export destination must be new and outside project')
    project = _load(root / 'project.json')
    if project.get('id') != pid:
        raise SyncError('Project id mismatch')
    before = revision(P, pid)
    out.mkdir(parents=True, mode=0o700)
    entries = {}
    sources = {}
    bindings = []
    total = 0

    def put(rel, source=None, raw=None):
        nonlocal total
        rel = _safe_rel(rel)
        if rel == MANIFEST or rel.casefold() in {x.casefold() for x in entries}:
            raise SyncError('Duplicate/reserved export path: ' + rel)
        size = source.stat().st_size if source is not None else len(raw)
        if total + size > MAX_TREE or len(entries) >= MAX_FILES:
            raise SyncError('Portable tree exceeds limit')
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        h = hashlib.sha256()
        if source is not None:
            _regular(source)
            before_stat = source.stat()
            with source.open('rb') as src, dest.open('xb') as dst:
                for chunk in iter(lambda: src.read(1024 * 1024), b''):
                    dst.write(chunk)
                    h.update(chunk)
            after = source.stat()
            if (before_stat.st_size, before_stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise SyncError('Source changed while exporting: ' + str(source))
            sources[str(source)] = (after.st_size, after.st_mtime_ns)
        else:
            dest.write_bytes(raw)
            h.update(raw)
        if dest.stat().st_size != size:
            raise SyncError('Export file size changed')
        os.chmod(dest, 0o600)
        entries[rel] = {'path': rel, 'bytes': size, 'sha256': h.hexdigest()}
        total += size
        return rel

    def bind(pointer, rel):
        _safe_rel(rel)
        _pointer_set(project, pointer, rel)
        bindings.append({'pointer': pointer, 'path': rel})

    try:
        for p in _files(root):
            rel = p.relative_to(root).as_posix()
            if rel == 'project.json':
                continue
            if rel.startswith('_portable/') or rel.startswith('_sync_history/'):
                raise SyncError('Re-export of an imported portable tree requires canonicalization; original source export only')
            if rel == 'seat_claims.json':
                put('_sync_history/original-seat_claims.json', source=p)
                put(rel, raw=b'{}')
            else:
                put(rel, source=p)
        put('_sync_history/original-project.json', source=root / 'project.json')
        source = project.get('source') or {}
        library_value = source.get('library')
        if library_value:
            library = Path(library_value)
            lib_manifest = _load(library / 'manifest.json')
            put('_portable/library/manifest.json', source=library / 'manifest.json')
            for row in lib_manifest.get('files', []):
                rel = _safe_rel(row['path'])
                put('_portable/library/' + rel, source=library / rel)
            bind('/source/library', '_portable/library')
        for i, witness in enumerate(source.get('witnesses') or []):
            path = Path(witness['path'])
            rel = '_portable/witnesses/' + str(i) + '/' + _safe_rel(path.name)
            put(rel, source=path)
            bind('/source/witnesses/' + str(i) + '/path', rel)
        # Roots are discovery hints, not an instruction to clone entire repositories.
        # Disable their original machine paths; all explicit materials are above/below.
        if source.get('roots'):
            source['roots'] = {}
        team = project.get('local_team') or {}
        roots = team.get('source_roots') or {}
        for asset in team.get('assets') or []:
            key = asset['root']
            rel = _safe_rel(asset['path'])
            source_path = Path(roots[key]) / rel
            target = '_portable/assets/' + _safe_rel(key) + '/' + rel
            if target not in entries:
                put(target, source=source_path)
        for key in list(roots):
            prefix = '_portable/assets/' + _safe_rel(key)
            if any(x.startswith(prefix + '/') for x in entries):
                bind('/local_team/source_roots/' + _escape(key), prefix)
            else:
                roots[key] = ''
        for seat in team.get('seats') or []:
            seat['person_id'] = ''
        project['sync_import'] = {'schema': 'helix.import-provenance.v1',
                                  'source_revision': before, 'identity_linkage_required': True,
                                  'history_status': 'IMPORTED_UNVERIFIED_HISTORY_NOT_LIVE_AUTHORIZATION'}
        put('project.json', raw=_json(project))
        # Native ledger/events/signatures remain byte-for-byte preserved.
        for path, stamp in sources.items():
            st = Path(path).stat()
            if (st.st_size, st.st_mtime_ns) != stamp:
                raise SyncError('Source changed during export: ' + path)
        if revision(P, pid) != before:
            raise SyncError('Project changed during export')
        manifest = {'schema': SCHEMA, 'project_id': pid, 'name': project.get('name', pid),
                    'revision': before, 'source_revision': before,
                    'entries': sorted(entries.values(), key=lambda row: row['path']),
                    'bindings': bindings, 'total_bytes': total}
        manifest['portable_revision'] = _canonical(manifest)
        (out / MANIFEST).write_bytes(_json(manifest))
        os.chmod(out / MANIFEST, 0o600)
        return manifest
    except Exception:
        shutil.rmtree(out)
        raise


export_directory = export_tree


def _validate_manifest(manifest):
    m = copy.deepcopy(manifest)
    if m.get('schema') != SCHEMA:
        raise SyncError('Unsupported snapshot schema')
    _pid(m.get('project_id'))
    for key in ('revision', 'source_revision', 'portable_revision'):
        if not re.fullmatch('[0-9a-f]{64}', str(m.get(key, ''))):
            raise SyncError('Invalid revision')
    if m['revision'] != m['source_revision']:
        raise SyncError('Source revision mismatch')
    rows = m.get('entries')
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_FILES:
        raise SyncError('Invalid entry count')
    names = set()
    total = 0
    for row in rows:
        rel = _safe_rel(row['path'])
        if rel == MANIFEST or rel.casefold() in names:
            raise SyncError('Duplicate/reserved path')
        names.add(rel.casefold())
        if type(row['bytes']) is not int or row['bytes'] < 0:
            raise SyncError('Invalid file size')
        if not re.fullmatch('[0-9a-f]{64}', str(row['sha256'])):
            raise SyncError('Invalid hash')
        total += row['bytes']
    for name in names:
        bits = name.split('/')
        if any('/'.join(bits[:n]) in names for n in range(1, len(bits))):
            raise SyncError('File/directory path collision')
    if 'project.json' not in names or total != m.get('total_bytes') or total > MAX_TREE:
        raise SyncError('Invalid total or missing project')
    bindings = m.get('bindings')
    if not isinstance(bindings, list) or len(bindings) > MAX_FILES:
        raise SyncError('Invalid bindings')
    pointers = set()
    for b in bindings:
        pointer, rel = b['pointer'], _safe_rel(b['path'])
        if not isinstance(pointer, str) or pointer in pointers or not (
            pointer == '/source/library' or re.fullmatch(r'/source/witnesses/[0-9]+/path', pointer)
            or re.fullmatch(r'/local_team/source_roots/[^/]+', pointer)):
            raise SyncError('Invalid metadata binding')
        if rel.casefold() not in names and not any(n.startswith(rel.casefold() + '/') for n in names):
            raise SyncError('Binding target absent')
        pointers.add(pointer)
    if _canonical(m) != m['portable_revision']:
        raise SyncError('Portable manifest hash mismatch')
    return m


def _validate_tree(root, manifest):
    m = _validate_manifest(manifest)
    files = _files(root)
    actual = {p.relative_to(root).as_posix() for p in files}
    expected = {r['path'] for r in m['entries']} | {MANIFEST}
    if actual != expected:
        raise SyncError('Tree contents differ from manifest')
    for row in m['entries']:
        p = root / row['path']
        if p.stat().st_size != row['bytes'] or _sha(p) != row['sha256']:
            raise SyncError('File integrity check failed: ' + row['path'])
    p = _load(root / 'project.json')
    if p.get('id') != m['project_id']:
        raise SyncError('Project definition id mismatch')
    for b in m['bindings']:
        if _pointer_get(p, b['pointer']) != b['path']:
            raise SyncError('Metadata binding mismatch')
    # All live source access must be bound inside the imported tree.
    required = set()
    source = p.get('source') or {}
    if source.get('roots'):
        raise SyncError('Foreign discovery roots forbidden')
    if source.get('library'):
        required.add('/source/library')
    for i, w in enumerate(source.get('witnesses') or []):
        required.add('/source/witnesses/' + str(i) + '/path')
    team = p.get('local_team') or {}
    for key, val in (team.get('source_roots') or {}).items():
        if val:
            required.add('/local_team/source_roots/' + _escape(key))
    if not required.issubset({b['pointer'] for b in m['bindings']}):
        raise SyncError('Unbound source reference')
    for a in team.get('assets') or []:
        _safe_rel(a['path'])
        prefix = (team.get('source_roots') or {}).get(a['root'], '')
        if prefix + '/' + a['path'] not in actual:
            raise SyncError('Missing asset')
    return m, p


def _work(P, pid):
    root = Path(P.pdir(_pid(pid))).absolute()
    work = root.parent.parent / '.helix-sync'
    for p in (root, root.parent, work):
        if p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()):
            raise SyncError('Linked storage root forbidden')
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root, work


def stage_directory(P, staged_dir, manifest, owner, label=''):
    if not isinstance(owner, str) or not owner:
        raise SyncError('Owner required')
    src = Path(staged_dir).absolute()
    if _load(src / MANIFEST) != manifest:
        raise SyncError('Disk and supplied manifest differ')
    m, _ = _validate_tree(src, manifest)
    _, work = _work(P, m['project_id'])
    with LOCK:
        for token, r in list(PENDING.items()):
            if r['expires'] < time.time():
                if r.get('tree') and Path(r['tree']).exists():
                    shutil.rmtree(r['tree'])
                del PENDING[token]
        if len(PENDING) >= 10:
            raise SyncError('Too many pending imports')
        token = secrets.token_hex(16)
        tree = work / ('stage-' + token)
        shutil.copytree(src, tree)
        try:
            _validate_tree(tree, m)
        except Exception:
            shutil.rmtree(tree)
            raise
        PENDING[token] = {'owner': owner, 'tree': str(tree), 'manifest': m,
                          'expires': time.time() + 24 * 3600}
    return {'token': token, 'project_id': m['project_id'], 'name': m['name'],
            'revision': m['source_revision'], 'source_revision': m['source_revision'],
            'portable_revision': m['portable_revision'], 'file_count': len(m['entries']),
            'bytes': m['total_bytes'], 'label': label, 'entries': [r['path'] for r in m['entries'][:200]]}


def apply(P, token, expected_revision, target_pid, *, owner, create_only=False):
    """Caller must hold its project/ledger writer lock, and authorize target access.
    The module lock serializes imports only. Backups are retained outside projects.
    """
    with LOCK:
        r = PENDING.get(token)
        if not r or r['owner'] != owner or not owner or r['expires'] < time.time():
            raise SyncError('Import expired or owned by another actor')
        m = r['manifest']
        pid = _pid(target_pid)
        if pid != m['project_id']:
            raise SyncError('Source/target project id mismatch')
        request_key = (pid, str(expected_revision), bool(create_only))
        if r.get('result'):
            if r['request_key'] != request_key:
                raise SyncError('Retry parameters changed')
            return dict(r['result'])
        root, work = _work(P, pid)
        tree = Path(r['tree'])
        _, project = _validate_tree(tree, m)
        root.parent.mkdir(parents=True, exist_ok=True)
        existing = root.exists()
        if existing:
            if create_only or not (root / 'project.json').is_file():
                raise SyncError('Target exists')
            if not expected_revision or revision(P, pid) != expected_revision:
                raise SyncError('Target changed since preview')
        elif expected_revision:
            raise SyncError('Target removed since preview')
        for b in m['bindings']:
            _pointer_set(project, b['pointer'], str(root / b['path']))
        for seat in (project.get('local_team') or {}).get('seats') or []:
            seat['person_id'] = ''
        project['sync_import'] = {'schema': 'helix.import-provenance.v1',
                                 'source_revision': m['source_revision'],
                                 'portable_revision': m['portable_revision'],
                                 'identity_linkage_required': True,
                                 'history_status': 'IMPORTED_UNVERIFIED_HISTORY_NOT_LIVE_AUTHORIZATION'}
        candidate = Path(tempfile.mkdtemp(prefix='apply-', dir=work))
        backup = None
        try:
            shutil.copytree(tree, candidate, dirs_exist_ok=True)
            (candidate / MANIFEST).unlink()
            (candidate / 'project.json').write_bytes(_json(project))
            if (candidate / 'seat_claims.json').exists():
                # export_tree already stored the original under original-seat_claims;
                # apply() only resets live claims, so no second copy is needed.
                (candidate / 'seat_claims.json').write_bytes(b'{}')
            for f in _files(candidate):
                os.chmod(f, 0o600)
            for base, dirs, _ in os.walk(candidate):
                os.chmod(base, 0o700)
            # Recheck immediately before replacement while caller's writer lock is held.
            if existing and revision(P, pid) != expected_revision:
                raise SyncError('Target changed during validation')
            if not existing and root.exists():
                raise SyncError('Target appeared during validation')
            if existing:
                backup = work / ('backup-' + pid + '-' + secrets.token_hex(12))
                root.rename(backup)
            try:
                candidate.rename(root)
            except Exception:
                if backup is not None:
                    backup.rename(root)
                raise
            result = {'id': pid, 'revision': revision(P, pid), 'created': not existing,
                      'source_revision': m['source_revision'], 'portable_revision': m['portable_revision']}
            if backup:
                result['backup'] = str(backup)
            r['result'] = result
            r['request_key'] = request_key
            return dict(result)
        finally:
            if candidate.exists():
                shutil.rmtree(candidate)


def import_directory(P, staged_dir, expected_revision, target_pid, *, owner, create_only=False):
    m = _load(Path(staged_dir) / MANIFEST)
    preview = stage_directory(P, staged_dir, m, owner)
    return apply(P, preview['token'], expected_revision, target_pid, owner=owner, create_only=create_only)


def snapshot(P, pid):
    pid = _pid(pid)
    root_dir = Path(P.pdir(pid))
    if root_dir.is_dir() and sum(f.stat().st_size for f in root_dir.rglob('*') if f.is_file()) > MAX_TOTAL:
        raise SyncError('Use streamed directory transport for this project')
    # File-backed export first; refuse oversized state before creating an in-memory ZIP.
    with tempfile.TemporaryDirectory(prefix='helix-export-') as tmp:
        root = Path(tmp) / 'tree'
        m = export_tree(P, pid, root)
        if m['total_bytes'] > MAX_TOTAL:
            raise SyncError('Use streamed directory transport for this project')
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            for p in _files(root):
                z.write(p, p.relative_to(root).as_posix())
        if out.tell() > MAX_TOTAL:
            raise SyncError('Compressed snapshot exceeds limit')
        return out.getvalue()


def _zip_extract(raw, dest):
    if len(raw) > MAX_TOTAL:
        raise SyncError('Archive exceeds limit')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        infos = z.infolist()
        if len(infos) > MAX_FILES + 1:
            raise SyncError('Too many archive entries')
        seen = set()
        total = 0
        for item in infos:
            rel = _safe_rel(item.filename)
            mode = item.external_attr >> 16
            if item.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
                raise SyncError('Archive must contain regular files only')
            if rel.casefold() in seen:
                raise SyncError('Duplicate archive path')
            seen.add(rel.casefold())
            total += item.file_size
            if total > MAX_TOTAL:
                raise SyncError('Expanded archive exceeds limit')
        for item in infos:
            target = dest / item.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(item) as src, target.open('xb') as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
    return _validate_tree(dest, _load(dest / MANIFEST))[0]


def inspect(raw):
    with tempfile.TemporaryDirectory(prefix='helix-inspect-') as tmp:
        m = _zip_extract(raw, Path(tmp))
    return {'project_id': m['project_id'], 'name': m['name'], 'revision': m['source_revision'],
            'source_revision': m['source_revision'], 'portable_revision': m['portable_revision'],
            'entry_count': len(m['entries']), 'bytes': m['total_bytes'],
            'entries': [r['path'] for r in m['entries']]}


def stage(raw, owner, label=''):
    # Legacy bridge call has no P. Retain validated temporary tree until apply.
    if not owner:
        raise SyncError('Owner required')
    tree = Path(tempfile.mkdtemp(prefix='helix-stage-'))
    try:
        m = _zip_extract(raw, tree)
        with LOCK:
            if len(PENDING) >= 10:
                raise SyncError('Too many pending imports')
            token = secrets.token_hex(16)
            PENDING[token] = {'owner': owner, 'tree': str(tree), 'manifest': m,
                              'expires': time.time() + 24 * 3600}
        return {'token': token, 'project_id': m['project_id'], 'name': m['name'],
                'revision': m['source_revision'], 'source_revision': m['source_revision'],
                'portable_revision': m['portable_revision'], 'file_count': len(m['entries']),
                'bytes': m['total_bytes'], 'label': label}
    except Exception:
        shutil.rmtree(tree)
        raise
