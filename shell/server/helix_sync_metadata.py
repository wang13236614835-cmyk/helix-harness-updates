"""Bounded, same-project metadata deltas; never export or copy project trees.

Callers must authenticate a lead and hold IDENT.store_lock for preview/apply.
Tasks are sparse patches keyed by existing task_id (no additions or removals).
Status/progress and live identity/source fields cannot be changed through this API.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
from datetime import date

import helix_sync as SYNC

MAX_REQUEST = 2_000_000
MAX_PENDING = 32
TTL = 1800
PENDING = {}
LOCK = threading.RLock()
PROJECT_FIELDS = {'name', 'question', 'direction', 'weeks', 'tasks', 'research_progress'}
TASK_FIELDS = {'week', 'start', 'end', 'track', 'deliverable', 'acceptance',
               'owner', 'cross_reviewer', 'duty', 'estimated_minutes', 'human_work'}
TASK_READONLY = {'status', 'execution_mode', 'workspace_slot', 'task_kind',
                 'support_slots', 'evidence_refs', 'progress', 'depends_on'}
ASSIGNMENTS = {'owner', 'cross_reviewer', 'duty'}


class MetadataError(SYNC.SyncError):
    status = 400


class Conflict(MetadataError):
    status = 409


def _require(condition, message):
    if not condition:
        raise MetadataError(message)


def _object(value, allowed, label):
    _require(isinstance(value, dict), label + ' must be an object')
    _require(not set(value) - allowed, 'Unknown ' + label + ' fields')


def _same(a, b):
    return SYNC._json(a) == SYNC._json(b)


def _bounded(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise MetadataError('Invalid JSON request') from exc
    _require(len(raw) <= MAX_REQUEST, 'Metadata request exceeds 2MB')


def _text(value, label, limit=10000, empty=True):
    _require(isinstance(value, str) and len(value) <= limit and '\x00' not in value,
             'Invalid ' + label)
    _require(empty or bool(value.strip()), label + ' cannot be empty')


def _day(value):
    _text(value, 'date', 10)
    if value:
        try:
            _require(date.fromisoformat(value).isoformat() == value, 'Invalid ISO date')
        except ValueError as exc:
            raise MetadataError('Invalid ISO date') from exc


def _dates(row):
    for key in ('start', 'end'):
        if key in row:
            _day(row[key])
    if row.get('start') and row.get('end'):
        _require(row['start'] <= row['end'], 'End precedes start')


def _path(root, rel):
    SYNC._safe_rel(rel)
    _require(rel == 'research-progress.json' or bool(re.fullmatch(
        r'updates/[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.(?:md|json)', rel)),
        'Only updates/<filename>.md/json and research-progress.json are permitted')
    path = root / rel
    for item in (path, *path.parents):
        _require(not item.is_symlink() and not (hasattr(item, 'is_junction') and item.is_junction()),
                 'Linked metadata destination is forbidden')
    if path.exists():
        SYNC._regular(path)
        _require(path.stat().st_size <= MAX_REQUEST, 'Existing summary exceeds 2MB')
    return path


def _atomic(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix='.metadata-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def _record(path, obj):
    _atomic(path, SYNC._json(obj))


def _root(P, pid):
    root, work = SYNC._work(P, pid)
    _require((root / 'project.json').is_file(), 'Existing project required')
    SYNC._regular(root / 'project.json')
    return root, work


def _owner(owner):
    _require(isinstance(owner, str) and 0 < len(owner) <= 256, 'Authenticated owner required')


def _revision(value):
    _require(isinstance(value, str) and bool(re.fullmatch('[0-9a-f]{64}', value)),
             'Whole-project expected_revision required')


def _locked_tasks(root):
    locked = set()
    history = root / 'helix'
    if not history.exists():
        return locked
    for path in SYNC._files(history):
        if path.name != 'ledger.jsonl':
            continue
        with path.open('r', encoding='utf-8-sig') as stream:
            while True:
                line = stream.readline(MAX_REQUEST + 1)
                if not line:
                    break
                _require(len(line) <= MAX_REQUEST, 'Oversized ledger event; assignment refused')
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    _require(isinstance(event, dict), 'Invalid ledger event')
                    payload = event.get('payload') or {}
                    _require(isinstance(payload, dict), 'Invalid ledger payload')
                    if payload.get('task_id'):
                        locked.add(str(payload['task_id']))
                except (ValueError, TypeError) as exc:
                    raise MetadataError('Unreadable ledger; assignment refused') from exc
    return locked


def _candidate(root, pid, patch):
    _object(patch, PROJECT_FIELDS, 'project patch')
    old = SYNC._load(root / 'project.json')
    _require(old.get('id') == pid, 'Source/target project id mismatch')
    result = copy.deepcopy(old)
    for key in ('name', 'question', 'direction'):
        if key in patch:
            _text(patch[key], key, 120 if key == 'name' else 10000, empty=key != 'name')
            result[key] = patch[key]
    if 'research_progress' in patch:
        _require(isinstance(patch['research_progress'], dict), 'research_progress must be an object')
        result['research_progress'] = copy.deepcopy(patch['research_progress'])
    if 'weeks' in patch:
        weeks = patch['weeks']
        _require(isinstance(weeks, list) and 1 <= len(weeks) <= 26, 'Invalid weeks')
        seen = set()
        prior = {w['week']: w for w in old.get('weeks', [])}
        cleaned = []
        for row in weeks:
            _object(row, {'week', 'start', 'end', 'theme'}, 'week')
            number = row.get('week')
            _require(type(number) is int and 1 <= number <= 26 and number not in seen, 'Invalid week identity')
            seen.add(number)
            merged = {**copy.deepcopy(prior.get(number, {})), **row}
            _dates(merged)
            if 'theme' in merged:
                _text(merged['theme'], 'week theme', 1000)
            cleaned.append(merged)
        _require(seen == set(range(1, len(weeks) + 1)), 'Weeks must be consecutive')
        result['weeks'] = cleaned
    tasks = result.get('tasks', [])
    _require(isinstance(tasks, list), 'Invalid stored tasks')
    by_id = {t['task_id']: t for t in tasks}
    _require(len(by_id) == len(tasks), 'Duplicate stored task identities')
    assignment_changes = set()
    if 'tasks' in patch:
        rows = patch['tasks']
        _require(isinstance(rows, list) and len(rows) <= 2000, 'Invalid task patches')
        seen = set()
        for row in rows:
            _object(row, TASK_FIELDS | TASK_READONLY | {'task_id'}, 'task')
            tid = row.get('task_id')
            _require(isinstance(tid, str) and tid in by_id and tid not in seen, 'Unknown/duplicate task identity')
            seen.add(tid)
            task = by_id[tid]
            for key, value in row.items():
                if key == 'task_id':
                    continue
                if key in TASK_READONLY:
                    _require(key in task and _same(task[key], value), 'Task progress/identity must remain unchanged')
                    continue
                if key in ASSIGNMENTS and not _same(task.get(key), value):
                    assignment_changes.add(tid)
                if key == 'week':
                    _require(type(value) is int, 'Invalid task week')
                elif key == 'estimated_minutes':
                    _require(type(value) is int and 0 <= value <= 600, 'Invalid estimate')
                else:
                    _text(value, 'task ' + key, 10000)
                task[key] = copy.deepcopy(value)
            _dates(task)
    if 'weeks' in patch or 'tasks' in patch:
        week_ids = {w['week'] for w in result.get('weeks', [])}
        _require(all(t.get('week') in week_ids for t in tasks), 'Task references missing week')
    if assignment_changes:
        locked = _locked_tasks(root)
        members = {m['name'] for m in old.get('members', []) if m.get('active', True)}
        duties = {d['key'] for d in old.get('duties', [])}
        originals = {t['task_id']: t for t in old.get('tasks', [])}
        for tid in assignment_changes:
            prev, task = originals[tid], by_id[tid]
            _require(tid not in locked and str(prev.get('status') or '').upper() in
                     ('', 'PENDING', 'TODO', 'NOT_STARTED'), 'Task with work history cannot be reassigned')
            _require(not prev.get('progress'), 'Task with progress cannot be reassigned')
            _require(task.get('owner') in members and task.get('cross_reviewer') in members and
                     task['owner'] != task['cross_reviewer'], 'Assignments require distinct existing active members')
            _require(not task.get('duty') or task['duty'] in duties, 'Unknown duty')
            # Seat-linked tasks use local_team identity mapping, never a metadata reassignment.
            _require(not prev.get('workspace_slot'), 'Seat-linked task assignments are protected')
    return result


def _files(root, values):
    _require(isinstance(values, dict) and len(values) <= 64, 'Invalid summary files')
    out, seen = {}, set()
    for rel, text in values.items():
        _path(root, rel)
        _require(rel.casefold() not in seen, 'Duplicate summary path')
        seen.add(rel.casefold())
        _text(text, 'summary file', MAX_REQUEST)
        if rel.endswith('.json'):
            try:
                obj = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (ValueError, RecursionError) as exc:
                raise MetadataError('Invalid research-progress.json') from exc
            _require(isinstance(obj, dict), 'Research summary JSON must be an object')
        out[rel] = text.encode('utf-8')
    return out


def _rollback(root, folder, journal):
    for row in reversed(journal['files']):
        rel = row['path']
        target = root / rel if rel == 'project.json' else _path(root, rel)
        if target.exists():
            SYNC._regular(target)
        _require(bool(re.fullmatch('[0-9]+\\.bak', row['backup'])), 'Invalid transaction backup')
        if row['existed']:
            backup = folder / row['backup']
            SYNC._regular(backup)
            _atomic(target, backup.read_bytes())
        else:
            target.unlink(missing_ok=True)
    for rel in journal.get('new_dirs', []):
        path = root / rel
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    journal['state'] = 'rolled_back'
    _record(folder / 'transaction.json', journal)


def _recover(root, work, pid):
    # A crash before the committed journal marker is always rolled back first.
    for folder in sorted(work.glob('metadata-' + pid + '-*')):
        if not re.fullmatch(re.escape('metadata-' + pid + '-') + '[0-9a-f]{32}', folder.name):
            continue
        if not (folder / 'transaction.json').exists():
            continue  # Backup preparation cannot have changed project files yet.
        SYNC._regular(folder / 'transaction.json')
        journal = SYNC._load(folder / 'transaction.json')
        if journal.get('state') == 'writing':
            _rollback(root, folder, journal)


def preview(P, body, *, owner):
    _owner(owner)
    _bounded(body)
    _object(body, {'source_project', 'target', 'expected_revision', 'patch', 'files'}, 'preview')
    pid = SYNC._pid(body.get('source_project'))
    _require(pid == body.get('target'), 'Source/target project id mismatch')
    expected = body.get('expected_revision')
    _revision(expected)
    with LOCK:
        root, work = _root(P, pid)
        _recover(root, work, pid)
        if SYNC.revision(P, pid) != expected:
            raise Conflict('Target changed since baseline')
        patch = body.get('patch', {})
        candidate = _candidate(root, pid, patch)
        files = _files(root, body.get('files', {}))
        changed = [rel for rel, raw in files.items() if not (root / rel).exists() or (root / rel).read_bytes() != raw]
        if not _same(SYNC._load(root / 'project.json'), candidate):
            changed.insert(0, 'project.json')
        _require(bool(changed), 'No metadata changes')
        now = time.time()
        for token in list(PENDING):
            if PENDING[token]['expires'] < now:
                del PENDING[token]
        _require(len(PENDING) < MAX_PENDING, 'Too many pending metadata previews')
        token = secrets.token_hex(16)
        PENDING[token] = {'owner': owner, 'project': pid, 'root': str(root),
                          'expected_revision': expected, 'patch': copy.deepcopy(patch),
                          'files': files, 'changed': changed, 'expires': now + TTL}
        return {'token': token, 'project_id': pid, 'expected_revision': expected,
                'changed': changed, 'patch': copy.deepcopy(patch),
                'files': {rel: {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                          for rel, raw in files.items()},
                'bytes': sum(len(raw) for raw in files.values()) + len(SYNC._json(patch)),
                'scope': 'metadata-only; task identities/status, ledger, sources and live identity preserved'}


def apply(P, body, *, owner):
    _owner(owner)
    _bounded(body)
    _object(body, {'token', 'source_project', 'target', 'expected_revision', 'confirm'}, 'apply')
    _require(body.get('confirm') is True, 'Explicit metadata confirmation required')
    pid = SYNC._pid(body.get('source_project'))
    _require(pid == body.get('target'), 'Source/target project id mismatch')
    expected, token = body.get('expected_revision'), body.get('token')
    _revision(expected)
    _require(isinstance(token, str) and bool(re.fullmatch('[0-9a-f]{32}', token)), 'Invalid preview token')
    with LOCK:
        root, work = _root(P, pid)
        _recover(root, work, pid)
        folder = work / ('metadata-' + pid + '-' + token)
        if folder.exists():
            journal = SYNC._load(folder / 'transaction.json')
            if journal.get('owner') != owner or journal.get('expected_revision') != expected:
                raise Conflict('Commit owner or retry parameters changed')
            if journal.get('state') == 'committed':
                return copy.deepcopy(journal['result'])
            raise Conflict('Previous transaction rolled back; preview again')
        row = PENDING.get(token)
        if not row or row['owner'] != owner or row['project'] != pid or row['root'] != str(root) or row['expires'] < time.time():
            raise Conflict('Preview expired or owned by another actor')
        if row['expected_revision'] != expected or SYNC.revision(P, pid) != expected:
            raise Conflict('Target changed since preview')
        candidate = _candidate(root, pid, row['patch'])
        values = dict(row['files'])
        values['project.json'] = SYNC._json(candidate)
        changed = {rel: values[rel] for rel in row['changed']}
        for rel in changed:
            if rel != 'project.json':
                _path(root, rel)
        folder.mkdir(mode=0o700)
        journal = {'owner': owner, 'project': pid, 'expected_revision': expected,
                   'state': 'prepared', 'files': [], 'new_dirs': []}
        try:
            for i, rel in enumerate(changed):
                path = root / rel
                existed = path.exists()
                backup = str(i) + '.bak'
                if existed:
                    SYNC._regular(path)
                    _atomic(folder / backup, path.read_bytes())
                elif path.parent != root and not path.parent.exists():
                    journal['new_dirs'] = ['updates']
                journal['files'].append({'path': rel, 'existed': existed, 'backup': backup})
            _record(folder / 'transaction.json', journal)
            if SYNC.revision(P, pid) != expected:
                raise Conflict('Target changed during validation')
            journal['state'] = 'writing'
            _record(folder / 'transaction.json', journal)
            for rel, raw in changed.items():
                _atomic(root / rel, raw)
            result = {'id': pid, 'revision': SYNC.revision(P, pid),
                      'previous_revision': expected, 'changed': list(changed),
                      'backup': folder.name, 'metadata_only': True}
            journal.update(state='committed', result=result)
            _record(folder / 'transaction.json', journal)
            return copy.deepcopy(result)
        except Exception:
            if journal['state'] in ('writing', 'committed'):
                _rollback(root, folder, journal)
            elif not (folder / 'transaction.json').exists():
                journal['state'] = 'rolled_back'
                _record(folder / 'transaction.json', journal)
            raise
