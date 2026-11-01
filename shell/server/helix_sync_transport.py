"""File-backed transport for large authenticated Helix project snapshots."""
import hashlib
import json
import os
import re
import secrets
import shutil
import tarfile
import threading
import time
from pathlib import Path

CHUNK = 8 * 1024 * 1024
MAX_BYTES = 12 * 1024 * 1024 * 1024
MAX_FILES = 20000
LOCK = threading.RLock()
WORKERS = {}


def _root(P):
    root = P.DATA / 'sync-transfers'
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read(P, token, owner=None):
    if not re.fullmatch(r'[a-f0-9]{32}', str(token or '')):
        raise ValueError('传输编号无效')
    root = _root(P) / token
    record = json.loads((root / 'transfer.json').read_text(encoding='utf-8'))
    # The 128-bit transfer token is itself the capability for continuing an upload:
    # it is handed only to the authenticated client that began the transfer, and it
    # must keep working across server restarts (login sessions are memory-only).
    if owner is not None and record['owner'] != owner:
        raise PermissionError('传输不属于当前账号')
    return root, record


def _save(root, record):
    temp = root / 'transfer.json.tmp'
    temp.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    os.replace(temp, root / 'transfer.json')


def _public(record):
    return {k: record[k] for k in ('token', 'status', 'size', 'received', 'error', 'preview') if k in record}


def begin(P, owner, body):
    size = int(body.get('size') or 0)
    digest = str(body.get('sha256') or '')
    if not 0 < size <= MAX_BYTES or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('传输大小或校验值无效')
    with LOCK:
        # Resume: an unfinished transfer of the identical payload continues instead of
        # restarting from zero, so a dropped connection never wastes a long upload.
        for p in _root(P).glob('*/transfer.json'):
            try:
                prior = json.loads(p.read_text(encoding='utf-8'))
            except Exception:
                continue
            if (prior.get('owner') == owner and prior.get('status') == 'uploading'
                    and prior.get('size') == size and prior.get('sha256') == digest
                    and (p.parent / 'payload.tar.gz').stat().st_size == prior.get('received')):
                return _public(prior)
        # Stale uploads (no progress for an hour) are pruned so a crashed client
        # never exhausts the concurrent-transfer limit.
        active = 0
        now = time.time()
        for p in _root(P).glob('*/transfer.json'):
            try:
                r = json.loads(p.read_text(encoding='utf-8'))
            except Exception:
                continue
            if r.get('status') in ('uploading', 'verifying'):
                touched = max(r.get('created', 0), r.get('touched', 0))
                if now - touched > 3600:
                    shutil.rmtree(p.parent, ignore_errors=True)
                    continue
                active += 1
        if active >= 3:
            raise ValueError('已有传输正在进行，请稍后重试')
        token = secrets.token_hex(16)
        root = _root(P) / token
        root.mkdir()
        record = {'token': token, 'owner': owner, 'size': size, 'sha256': digest, 'received': 0, 'status': 'uploading', 'created': time.time()}
        (root / 'payload.tar.gz').touch()
        _save(root, record)
        return _public(record)


def status(P, owner, token):
    with LOCK:
        root, record = _read(P, token, None)
        return _public(record)


def chunk(P, owner, token, offset, raw):
    with LOCK:
        root, record = _read(P, token, None)
        offset = int(offset)
        if not raw or len(raw) > CHUNK or offset < 0 or offset + len(raw) > record['size']:
            raise ValueError('传输块大小或偏移无效')
        path = root / 'payload.tar.gz'
        current = path.stat().st_size
        if offset < current:
            with path.open('rb') as f:
                f.seek(offset)
                if f.read(len(raw)) != raw:
                    raise ValueError('重复传输块内容不一致')
            return {'received': current}
        if record['status'] != 'uploading' or offset != current:
            raise ValueError('传输偏移不一致，请读取状态后续传')
        with path.open('ab') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        record['received'] = path.stat().st_size
        record['touched'] = time.time()
        _save(root, record)
        return {'received': record['received']}


def _digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(CHUNK), b''):
            h.update(block)
    return h.hexdigest()


def finish(P, owner, token):
    with LOCK:
        root, record = _read(P, token, None)
        if record['status'] == 'ready':
            return _public(record)
        if record['status'] == 'verifying':
            # A restart may have killed the verification thread; resume it.
            if token in WORKERS and WORKERS[token].is_alive():
                return _public(record)
        elif record['status'] == 'error' and record['received'] == record['size']:
            # The payload is complete on disk; let the sender ask for verification again
            # (e.g. after a validator fix) instead of re-uploading gigabytes.
            pass
        elif record['status'] != 'uploading' or record['received'] != record['size']:
            raise ValueError('传输尚未完成')
        record['status'] = 'verifying'
        record['error'] = ''
        _save(root, record)
        worker = threading.Thread(target=_verify, args=(P, record['owner'], token), daemon=True)
        WORKERS[token] = worker
        worker.start()
        return _public(record)


def _rebuild_manifest(dest):
    """Reconstruct helix-state.json for an archive that was packed without it."""
    import helix_sync
    entries = []
    for path in sorted(dest.rglob('*')):
        if not path.is_file():
            continue
        rel = path.relative_to(dest).as_posix()
        if rel == 'helix-state.json':
            continue
        entries.append({'path': rel, 'bytes': path.stat().st_size, 'sha256': _digest(path)})
    project = json.loads((dest / 'project.json').read_text(encoding='utf-8'))
    revision = str((project.get('sync_import') or {}).get('source_revision') or '')
    if not re.fullmatch(r'[0-9a-f]{64}', revision):
        raise ValueError('状态包缺少可用的项目修订号')
    bindings = []
    source = project.get('source') or {}
    if source.get('library'):
        bindings.append({'pointer': '/source/library', 'path': source['library']})
    for i, witness in enumerate(source.get('witnesses') or []):
        bindings.append({'pointer': '/source/witnesses/%d/path' % i, 'path': witness['path']})
    for key, value in (project.get('local_team') or {}).get('source_roots', {}).items():
        if value:
            bindings.append({'pointer': '/local_team/source_roots/' + str(key).replace('~', '~0').replace('/', '~1'),
                             'path': value})
    manifest = {'schema': helix_sync.SCHEMA, 'project_id': project['id'],
                'name': project.get('name') or project['id'], 'revision': revision,
                'source_revision': revision, 'entries': entries,
                'bindings': bindings, 'total_bytes': sum(e['bytes'] for e in entries)}
    manifest['portable_revision'] = helix_sync._canonical(manifest)
    (dest / 'helix-state.json').write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(',', ':')), encoding='utf-8')


def _verify(P, owner, token):
    try:
        import helix_sync
        root, record = _read(P, token, owner)
        path = root / 'payload.tar.gz'
        if _digest(path) != record['sha256']:
            raise ValueError('上传文件校验不一致')
        dest = root / 'content'
        # A previous verification may have been interrupted (e.g. container restart);
        # extract from scratch so the result never mixes partial trees.
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(exist_ok=False)
        total = 0
        seen = set()
        with tarfile.open(path, 'r|gz') as archive:
            for item in archive:
                name = item.name
                parts = name.split('/')
                if (not item.isfile() or name.startswith('/') or '\\' in name or ':' in name
                        or any(x in ('', '.', '..') for x in parts)
                        or any(x.endswith((' ', '.')) for x in parts)
                        or any(re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?', x) for x in parts)
                        or len(name.encode('utf-8')) > 900):
                    raise ValueError('状态包包含无效路径或非普通文件')
                key = name.casefold()
                if key in seen or len(seen) >= MAX_FILES:
                    raise ValueError('状态包路径重复或文件过多')
                seen.add(key)
                total += item.size
                if total > MAX_BYTES:
                    raise ValueError('状态包解压超过容量限制')
                target = dest.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(item)
                with source, target.open('xb') as out:
                    shutil.copyfileobj(source, out, CHUNK)
        manifest_path = dest / 'helix-state.json'
        if not manifest_path.is_file():
            # Older clients packed the tree without the manifest. The archive digest was
            # already verified against what the authenticated sender produced, so rebuild
            # the manifest from the extracted bytes instead of forcing a multi-GB re-send.
            _rebuild_manifest(dest)
        if not manifest_path.is_file() or manifest_path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('状态包清单不存在或过大')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        preview = helix_sync.stage_directory(P, dest, manifest, owner, label='爱维云项目同步')
        with LOCK:
            root, record = _read(P, token, owner)
            record.update(status='ready', preview=preview)
            _save(root, record)
    except Exception as exc:
        with LOCK:
            try:
                root, record = _read(P, token, owner)
                record.update(status='error', error=str(exc))
                _save(root, record)
            except Exception:
                pass
    finally:
        WORKERS.pop(token, None)
