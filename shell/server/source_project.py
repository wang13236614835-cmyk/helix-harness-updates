"""Adopt any already-recognized research source as a normal managed project.

Source files remain read-only. This module never imports accounts, credentials,
ledger history, or approval decisions from a source directory.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading
from datetime import datetime, timezone

LOCK = threading.RLock()


def preview(snapshot, name: str, origin_id: str, library: Path) -> dict:
    sp, v4 = snapshot.sprint, snapshot.v4
    witnesses = [w for w in snapshot.as_dict()['witnesses'] if w.get('found')]
    if not witnesses or not sp.get('tasks'):
        raise ValueError('没有读取到项目任务表；先连接已有项目资料，再接入项目。')
    tasks = [{**t, 'track': t.get('track') or t.get('research_track') or ''} for t in sp['tasks']]
    weeks = [{**w, 'theme': w.get('theme') or w.get('track_label') or '第 %s 周' % w['week']} for w in sp.get('weeks') or []]
    # The compact research parser omits execution dependencies; keep them from the original task table.
    import projects
    for w in witnesses:
        if Path(w['path']).suffix.lower() != '.csv':
            continue
        try:
            raw = projects.parse_tasks(Path(w['path']).name, Path(w['path']).read_bytes())['rows']
            by_id = {t.get('task_id'): t for t in raw}
            for t in tasks:
                if t['task_id'] in by_id:
                    t.update({k: by_id[t['task_id']][k] for k in ('depends_on','duty') if k in by_id[t['task_id']]})
            break
        except ValueError:
            continue
    week_map = {w['week']: i + 1 for i, w in enumerate(weeks)}
    for t in tasks:
        t['week'] = week_map.get(t['week'], t['week'])
    source_key = hashlib.sha256(json.dumps([origin_id, sorted(w['path'] for w in witnesses)], ensure_ascii=False).encode()).hexdigest()
    return {'name': name or v4.get('target') or origin_id, 'origin_id': origin_id,
            'source_key': source_key, 'snapshot_hash': snapshot.snapshot_hash,
            'task_count': len(tasks), 'source_file_count': len(witnesses),
            'project': {'name': name or origin_id, 'target': v4.get('target') or '',
                        'question': v4.get('question') or '', 'status': v4.get('discovery_status') or '进行中',
                        'claim_ceiling': v4.get('claim_ceiling') or 'EXPLORATORY_ONLY',
                        'red_lines': sp.get('red_lines') or [], 'weeks': weeks, 'tasks': tasks,
                        'members': [{'name': n, 'role': 'both'} for n in sp.get('members') or []]},
            'witnesses': witnesses, 'library': str(library.resolve()) if (library / 'manifest.json').is_file() else ''}


def adopt(projects, proposal: dict, roots: dict, expected_hash: str) -> dict:
    if proposal['snapshot_hash'] != expected_hash:
        raise ValueError('来源已变化，请重新读取后接入。')
    with LOCK:
        for entry in projects.list_local():
            p = projects.load(entry['id'])
            if (p.get('source') or {}).get('key') == proposal['source_key']:
                return {'project': p, 'warnings': [], 'existing': True}
        p, warnings = projects.create(proposal['project'])
        # These paths come exclusively from the server's recognized sources, never HTTP input.
        p['source'] = {'kind': 'recognized', 'key': proposal['source_key'],
                       'origin_id': proposal['origin_id'], 'snapshot_hash': proposal['snapshot_hash'],
                       'roots': roots, 'library': proposal['library'],
                       'witnesses': [{'path': w['path'], 'label': w['label']} for w in proposal['witnesses']]}
        projects._write(projects.pdir(p['id']) / 'project.json', p)
        return {'project': p, 'warnings': warnings, 'existing': False}


def files(project: dict) -> list[dict]:
    rows = []
    for w in (project.get('source') or {}).get('witnesses') or []:
        p = Path(w['path'])
        try:
            stat = p.stat()
            if not p.is_file() or stat.st_size > 4 * 1024 * 1024:
                continue
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            continue
        rows.append({'name': p.name, 'stored': p.name, 'sha256': digest, 'bytes': stat.st_size,
                     'added_at': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                     'added_by': '', 'note': w['label'] + ' · 原件只读', 'source_path': str(p)})
    return rows


def file_path(project: dict, row: dict) -> Path | None:
    allowed = {str(Path(w['path'])) for w in (project.get('source') or {}).get('witnesses') or []}
    if row.get('source_path') not in allowed:
        return None
    p = Path(row['source_path'])
    return p if p.is_file() else None


def library(project: dict) -> Path | None:
    value = (project.get('source') or {}).get('library')
    return Path(value) if value else None
