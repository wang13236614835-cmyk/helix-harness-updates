"""Asynchronous, bounded-memory uploads for large Helix projects."""
import gzip
import hashlib
import json
import secrets
import shutil
import tarfile
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import helix_sync

CHUNK = 8 * 1024 * 1024
JOBS = {}
LOCK = threading.RLock()


def _public(record):
    keys = ('job', 'status', 'phase', 'sent', 'total', 'files', 'error', 'project', 'revision', 'preview', 'remote_revision')
    return {k: record[k] for k in keys if k in record}


def _save(record, **values):
    with LOCK:
        record.update(values)
        path = record['root'] / 'job.json'
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(_public(record), ensure_ascii=False), encoding='utf-8')
        temp.replace(path)


def get(actor, config, job):
    with LOCK:
        record = JOBS.get(job)
        if not record or record['owner'] != actor['id'] or record['origin'] != config['origin'] or record['session'] != hashlib.sha256(config['cookie'].encode()).hexdigest():
            raise ValueError('传输任务不存在或云账号已变化')
        return _public(record)


def start(S, actor, config, pid, request):
    with LOCK:
        for record in JOBS.values():
            if record['status'] not in ('ready', 'error'):
                if record['owner'] == actor['id'] and record['project'] == pid and record['origin'] == config['origin']:
                    return _public(record)
                raise ValueError('已有项目正在同步，请等待本次传输完成')
        if len(JOBS) > 16:
            for key in list(JOBS):
                if JOBS[key]['status'] in ('ready', 'error'):
                    JOBS.pop(key)
        job = secrets.token_hex(16)
        # Stage on the data volume under a deliberately short root: the system temp drive
        # lacks room for a multi-GB export, and a short prefix keeps deep project trees
        # under the classic 260-character Windows limit this process runs with.
        root = Path('D:/hs') / ('cache-' + pid)
        root.mkdir(parents=True, exist_ok=True)
        record = {'job': job, 'owner': actor['id'], 'origin': config['origin'], 'session': hashlib.sha256(config['cookie'].encode()).hexdigest(),
                  'root': root, 'status': 'running', 'phase': '正在整理项目与资料', 'sent': 0, 'total': 0, 'project': pid, 'created': time.time()}
        JOBS[job] = record
        _save(record)
        threading.Thread(target=_upload, args=(S, record, dict(config), request), daemon=True).start()
        return _public(record)


def _prepare(S, record):
    """Export and pack once per project revision; later retries reuse the same archive
    so the server-side digest stays identical and the upload can always resume."""
    root = record['root']
    pid = record['project']
    revision = helix_sync.revision(S.P, pid)
    cache_path = root / 'cache.json'
    archive = root / 'payload.tar.gz'
    try:
        cached = json.loads(cache_path.read_text(encoding='utf-8'))
    except Exception:
        cached = None
    if cached and cached.get('revision') == revision and archive.is_file() and archive.stat().st_size == cached.get('size'):
        _save(record, files=cached.get('files'), revision=revision, phase='复用已打包项目')
        return archive, cached['size'], cached['sha256']
    tree = root / 'content'
    shutil.rmtree(tree, ignore_errors=True)
    manifest = helix_sync.export_tree(S.P, pid, tree)
    _save(record, files=len(manifest['entries']), revision=manifest['source_revision'], phase='正在打包完整项目（含资料）')
    with open(archive, 'wb') as raw_out, gzip.GzipFile(fileobj=raw_out, mode='wb', compresslevel=1, mtime=0) as gz_out:
        with tarfile.open(fileobj=gz_out, mode='w', format=tarfile.PAX_FORMAT) as out:
            for entry in manifest['entries']:
                path = tree / entry['path']
                info = out.gettarinfo(str(path), arcname=entry['path'])
                info.mtime = 0
                info.mode = 0o600
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                with path.open('rb') as source:
                    out.addfile(info, source)
            # The manifest itself is not part of entries but must travel with the tree.
            state = tree / 'helix-state.json'
            info = out.gettarinfo(str(state), arcname='helix-state.json')
            info.mtime = 0
            info.mode = 0o600
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            with state.open('rb') as source:
                out.addfile(info, source)
    size = archive.stat().st_size
    digest = hashlib.sha256()
    with archive.open('rb') as source:
        for raw in iter(lambda: source.read(CHUNK), b''):
            digest.update(raw)
    cache_path.write_text(json.dumps({'revision': revision, 'size': size, 'sha256': digest.hexdigest(),
                                      'files': len(manifest['entries'])}), encoding='utf-8')
    return archive, size, digest.hexdigest()


def cleanup(pid):
    """Drop the cached package once the project is committed to the server."""
    with LOCK:
        shutil.rmtree(Path('D:/hs') / ('cache-' + str(pid)), ignore_errors=True)


def _upload(S, record, config, request):
    try:
        archive, size, sha = _prepare(S, record)
        # The archive is complete and deterministic: retry the transfer itself, resuming
        # from whatever the server already holds, before giving up.
        for attempt in range(8):
            try:
                _transfer(record, config, request, archive, size, sha)
                return
            except Exception as exc:
                if attempt == 7:
                    raise
                _save(record, phase='连接中断，正在续传（第 %d 次）' % (attempt + 2), error='')
                time.sleep(min(60, 5 * (attempt + 1)))
    except Exception as exc:
        _save(record, status='error', phase='同步未完成', error=str(exc))


def _transfer(record, config, request, archive, size, sha):
    if True:
        _save(record, phase='正在上传到爱维云', total=size)
        response = request(config['origin'], '/api/desktop-transfer/begin', {'size': size, 'sha256': sha}, config['cookie'])
        token = response['token']
        offset = int(response.get('received') or 0)  # resume an interrupted transfer
        _save(record, cloud_transfer=token, sent=offset)
        with archive.open('rb') as source:
            while offset < size:
                source.seek(offset)
                raw = source.read(CHUNK)
                try:
                    response = request(config['origin'], '/api/desktop-transfer/chunk?' + urlencode({'token': token, 'offset': offset}), raw, config['cookie'])
                    next_offset = response['received']
                except Exception:
                    # The chunk response may be lost while the server still wrote it:
                    # resync from the server's durable progress instead of guessing.
                    time.sleep(1)
                    response = request(config['origin'], '/api/desktop-transfer/status', {'token': token}, config['cookie'])
                    next_offset = response['received']
                    if next_offset < offset:
                        raise ValueError('服务器上传进度异常回退，请重新同步')
                    if next_offset == offset:
                        continue  # retry the same chunk on the next loop pass
                if not offset <= next_offset <= min(size, offset + len(raw)):
                    raise ValueError('服务器返回的上传进度无效')
                offset = next_offset
                _save(record, sent=offset)
        _save(record, phase='服务器正在验证文件完整性')
        request(config['origin'], '/api/desktop-transfer/finish', {'token': token}, config['cookie'])
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            response = request(config['origin'], '/api/desktop-transfer/status', {'token': token}, config['cookie'])
            if response['status'] == 'ready':
                preview = response['preview']
                _save(record, status='ready', phase='文件已校验，等待确认同步', preview=preview)
                return
            if response['status'] == 'error':
                raise ValueError(response.get('error') or '服务器文件验证失败')
            time.sleep(2)
        raise ValueError('服务器仍在验证，请检查传输状态')
