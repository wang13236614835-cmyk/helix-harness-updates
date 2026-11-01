"""Local-only Helix cloud bridge. Sessions never enter browser storage.
Transfers move full project state with revision checks; nothing overwrites
a side that changed since preview, and no accounts or credentials travel.
"""
import hashlib
import json
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie
from urllib.parse import urlsplit, urlencode, parse_qs
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

import helix_sync

DEFAULT_ORIGIN = 'https://helix.186-244-215-67.sslip.io'
LOCK = threading.RLock()
PENDING = {}
MAX_ARCHIVE = helix_sync.MAX_TOTAL


class RemoteError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def origin(value):
    u = urlsplit(str(value))
    allowed = u.scheme == 'https' or (os.environ.get('HELIX_TEST') == '1' and u.scheme == 'http' and u.hostname == '127.0.0.1')
    if not allowed or not u.hostname or u.username or u.password or u.path not in ('', '/') or u.query or u.fragment:
        raise RemoteError('服务器必须是完整 HTTPS 来源地址，不能包含路径、账号或查询参数')
    return f'{u.scheme}://{u.netloc}'


def request(base, path, body=None, cookie='', form=False, binary=False):
    base = origin(base)
    headers = {'Origin': base, 'X-Helix': '1', 'Accept': 'application/json'}
    if cookie:
        headers['Cookie'] = cookie
    if body is not None:
        if isinstance(body, bytes):
            headers['Content-Type'] = 'application/zip'
        elif form:
            body = urlencode(body).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        else:
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
    response_limit = 2_000_000 if path.startswith('/api/desktop-sync/metadata-') else MAX_ARCHIVE
    if path.startswith('/api/desktop-sync/metadata-') and body is not None and len(body) > response_limit:
        raise RemoteError('Metadata request exceeds 2MB')
    try:
        try:
            response = build_opener(ProxyHandler({}), NoRedirect()).open(Request(base + path, data=body, headers=headers), timeout=120)
        except HTTPError as exc:
            response = exc
        with response:
            data = response.read(response_limit + 1)
            if len(data) > response_limit:
                raise RemoteError('云端响应超过容量上限')
            status = response.status
            c = SimpleCookie(response.headers.get('Set-Cookie', ''))
            session = 'helix_cloud_session=' + c['helix_cloud_session'].value if 'helix_cloud_session' in c else ''
            if form and status == 303:
                return {'session': session}
            if status == 401:
                raise RemoteError('云登录已过期，请重新登录（' + path + '）')
            if status == 403:
                raise RemoteError('云端拒绝操作：请检查账号、邀请或权限')
            if status == 404:
                raise RemoteError('云端找不到这个项目，请刷新项目列表')
            if status == 409:
                raise RemoteError('云端项目已变化，请刷新后重新预览')
            if status >= 400:
                raise RemoteError(f'云端请求失败（HTTP {status}）')
            if binary:
                if not data.startswith(b'PK'):
                    raise RemoteError('云端没有返回项目状态包，请完成首次设置并重新登录')
                return data
            try:
                result = json.loads(data)
            except (ValueError, UnicodeError):
                raise RemoteError('云端需要首次设置，或登录信息不正确')
            if not isinstance(result, dict) or result.get('ok') is False:
                raise RemoteError(result.get('error', '云端拒绝操作') if isinstance(result, dict) else '云端响应格式不正确')
            return result
    except (URLError, TimeoutError, OSError) as exc:
        raise RemoteError('无法连接云服务器；操作结果不确定时请刷新项目列表，勿重复提交') from exc


def session_path(S, actor):
    return S.DATA / ('helix-remote-' + hashlib.sha256(actor['id'].encode()).hexdigest()[:24] + '.dpapi.json')


def load(S, actor):
    p = session_path(S, actor)
    if not p.exists():
        return {'origin': DEFAULT_ORIGIN, 'cookie': ''}
    from models import _Blob
    try:
        record = json.loads(p.read_text(encoding='utf-8'))
        if record['kind'] not in ('dpapi', 'fernet'):
            raise ValueError('unsafe storage')
        return json.loads(_Blob.unprotect(record['kind'], record['blob']))
    except Exception:
        raise RemoteError('本机云会话无法解密，请退出云账号后重新登录')


def save(S, actor, value):
    from models import _Blob
    kind, blob = _Blob.protect(json.dumps(value).encode())
    if kind not in ('dpapi', 'fernet'):
        raise RemoteError('此系统没有可用的安全会话存储')
    S.P._write(session_path(S, actor), {'kind': kind, 'blob': blob})


def handle(S, handler, action, body):
    if os.environ.get('HELIX_CLOUD_MODE') == '1':
        raise RemoteError('云连接只用于本机客户端')
    # Bind this privileged bridge to the actual local origin; no arbitrary forwarding.
    host = handler.headers.get('Host', '')
    expected = 'http://' + host
    if host not in (f'127.0.0.1:{handler.server.server_port}', f'localhost:{handler.server.server_port}') or handler.headers.get('Origin', expected) != expected:
        raise PermissionError('本机来源地址不匹配')
    actor = S.need_person(handler)
    with LOCK:
        if action == 'logout':
            try:
                c = load(S, actor)
                if c.get('cookie'):
                    request(c['origin'], '/logout', {}, c['cookie'], form=True)
            except RemoteError:
                pass
            finally:
                session_path(S, actor).unlink(missing_ok=True)
                for key in list(PENDING):
                    if PENDING[key]['owner'] == actor['id']:
                        PENDING.pop(key)
            return {'connected': False}
        c = load(S, actor)
        if action in ('login', 'register', 'setup'):
            base = origin(body.get('origin') or DEFAULT_ORIGIN)
            if action == 'login':
                fields = {k: str(body.get(k, '')) for k in ('username', 'password', 'portal')}
            elif action == 'setup':
                fields = {k: str(body.get(k, '')) for k in ('code', 'username', 'password', 'confirm')}
            else:
                fields = {k: str(body.get(k, '')) for k in ('name', 'password', 'confirm')}
                invite = str(body.get('invite', ''))
                if '://' in invite:
                    u = urlsplit(invite)
                    if origin(f'{u.scheme}://{u.netloc}') != base or u.path != '/register':
                        raise RemoteError('邀请链接不属于所选云服务器')
                    q = parse_qs(u.query)
                    fields.update({k: q[k][0] for k in ('invite', 'join') if k in q})
                else:
                    fields['invite'] = invite
            result = request(base, '/' + action, fields, form=True)
            if action == 'setup':
                result = request(base, '/login', {'username': fields['username'], 'password': fields['password'], 'portal': 'lead'}, form=True)
            if not result.get('session'):
                raise RemoteError('登录未完成，请检查首次设置和账号')
            c = {'origin': base, 'cookie': result['session']}
            save(S, actor, c)
        if action in ('status', 'login', 'register', 'setup'):
            if not c.get('cookie'):
                return {'connected': False, 'origin': c['origin']}
            try:
                info = request(c['origin'], '/api/auth/me', cookie=c['cookie'])
                listing = request(c['origin'], '/api/desktop/projects', cookie=c['cookie'])
                return {'connected': True, 'origin': c['origin'], 'account': info, 'projects': listing['projects']}
            except RemoteError as exc:
                return {'connected': False, 'origin': c['origin'], 'error': str(exc)}
        if not c.get('cookie'):
            raise RemoteError('请先登录云账号')
        if action == 'metadata-current-preview':
            import helix_sync_metadata as metadata
            S.need_admin(handler)
            if actor.get('role') != 'lead':
                raise PermissionError('Metadata sync requires a local lead')
            pid = helix_sync._pid(body.get('project'))
            project = S.P.load(pid)
            if not project:
                raise RemoteError('请选择本机正式项目')
            listing = request(c['origin'], '/api/desktop/projects', cookie=c['cookie'])
            target = next((p for p in listing['projects'] if p['id'] == pid), None)
            if not target:
                raise RemoteError('云端没有同编号项目，请先完成完整项目上传')
            patch = {key: project[key] for key in ('name', 'question', 'direction', 'weeks', 'research_progress') if key in project}
            patch['tasks'] = [{key: task[key] for key in ('task_id', *sorted(metadata.TASK_FIELDS)) if key in task} for task in project.get('tasks', [])]
            files = {}
            root = S.P.pdir(pid)
            summary = root / 'research-progress.json'
            reports = [summary] if summary.exists() else []
            updates = root / 'updates'
            if updates.exists():
                reports.extend(sorted(p for p in updates.iterdir() if p.is_file() and p.suffix in ('.md', '.json')))
            for path in reports:
                relative = path.relative_to(root).as_posix()
                metadata._path(root, relative)
                files[relative] = path.read_text(encoding='utf-8-sig')
            return handle(S, handler, 'metadata-preview', {'project': pid, 'target': pid, 'expected_revision': target['revision'], 'patch': patch, 'files': files})
        if action in ('metadata-preview', 'metadata-apply'):
            import helix_sync_metadata as metadata
            S.need_admin(handler)
            if actor.get('role') != 'lead':
                raise PermissionError('Metadata sync requires a local lead')
            try:
                metadata._bounded(body)
                session = hashlib.sha256(c['cookie'].encode()).hexdigest()
                if action == 'metadata-preview':
                    metadata._object(body, {'project', 'source_project', 'target', 'expected_revision', 'patch', 'files'}, 'metadata preview')
                    pid = helix_sync._pid(body.get('source_project') or body.get('project'))
                    target = body.get('target') or pid
                    if target != pid or (body.get('project') and body['project'] != pid) or not S.P.is_local(pid):
                        raise RemoteError('Metadata sync requires the existing same-id local project')
                    for key in list(PENDING):
                        if PENDING[key]['expires'] < time.time():
                            PENDING.pop(key)
                    if len(PENDING) >= 50:
                        raise RemoteError('Too many pending previews')
                    with S.IDENT.store_lock:
                        local_revision = helix_sync.revision(S.P, pid)
                    payload = {'source_project': pid, 'target': pid,
                               'expected_revision': body.get('expected_revision'),
                               'patch': body.get('patch', {}), 'files': body.get('files', {})}
                    metadata._bounded(payload)
                    preview = request(c['origin'], '/api/desktop-sync/metadata-preview', payload, c['cookie'])
                    if preview.get('project_id') != pid or preview.get('expected_revision') != payload['expected_revision']:
                        raise RemoteError('Cloud metadata preview does not match the request')
                    token = secrets.token_hex(16)
                    PENDING[token] = {'owner': actor['id'], 'origin': c['origin'], 'session': session,
                                      'direction': 'metadata', 'project': pid, 'target': pid,
                                      'revision': local_revision, 'preview': preview,
                                      'expected_revision': payload['expected_revision'],
                                      'expires': time.time() + 1800}
                    return {'transfer': token, 'direction': 'metadata', 'preview': preview,
                            'scope': preview.get('scope', 'metadata-only')}
                metadata._object(body, {'transfer', 'confirm'}, 'metadata apply')
                if body.get('confirm') is not True:
                    raise RemoteError('Explicit metadata confirmation required')
                row = PENDING.get(str(body.get('transfer') or ''))
                if not row or row['owner'] != actor['id'] or row['origin'] != c['origin'] or row['session'] != session or row['direction'] != 'metadata' or row['expires'] < time.time():
                    raise RemoteError('Metadata preview expired or session changed')
                if row.get('result'):
                    return row['result']
                with S.IDENT.store_lock:
                    if helix_sync.revision(S.P, row['project']) != row['revision']:
                        raise RemoteError('Local project changed since metadata preview')
                # No local actor id travels: the cloud binds its own authenticated lead.
                result = request(c['origin'], '/api/desktop-sync/metadata-apply', {
                    'token': row['preview']['token'], 'source_project': row['project'],
                    'target': row['target'], 'expected_revision': row['expected_revision'],
                    'confirm': True}, c['cookie'])
                row['result'] = result
                return result
            except metadata.MetadataError as exc:
                raise RemoteError(str(exc)) from exc
        if action == 'invite':
            return request(c['origin'], '/api/cloud/invite', {}, c['cookie'])
        if action == 'team':
            return request(c['origin'], '/api/people', cookie=c['cookie'])
        if action == 'upload-start':
            # Large projects stream asynchronously: start a bounded local job that
            # exports, packs and uploads the whole portable tree in 8MB chunks.
            S.need_admin(handler)
            import helix_remote_jobs
            pid = str(body.get('project') or '')
            if not S.P.is_local(pid):
                raise RemoteError('请选择本机项目')
            listing = request(c['origin'], '/api/desktop/projects', cookie=c['cookie'])
            row = next((x for x in listing['projects'] if x['id'] == pid), None)
            job = helix_remote_jobs.start(S, actor, c, pid, request)
            if row:
                # Baseline captured BEFORE the upload: commit refuses if the cloud
                # project changes underneath the transfer.
                with helix_remote_jobs.LOCK:
                    record = helix_remote_jobs.JOBS.get(job['job'])
                    if record is not None and not record.get('remote_revision'):
                        record['remote_revision'] = row['revision']
                        helix_remote_jobs._save(record)
                job = dict(job, remote_exists=True, remote_revision=row['revision'])
            return job
        if action == 'transfer-status':
            import helix_remote_jobs
            job = helix_remote_jobs.get(actor, c, str(body.get('job') or ''))
            if job.get('status') != 'ready':
                return job
            # Cloud finished validating: bind the staged preview to this bridge so the
            # user confirms exactly what the server verified, against the remote
            # revision captured when the upload began.
            pid = job['project']
            listing = request(c['origin'], '/api/desktop/projects', cookie=c['cookie'])
            row = next((x for x in listing['projects'] if x['id'] == pid), None)
            remote_revision = job.get('remote_revision') or (row['revision'] if row else '')
            if row and remote_revision and row['revision'] != remote_revision:
                return dict(job, status='error', error='云端项目在上传期间发生变化，请重新同步')
            token = secrets.token_hex(16)
            for key in list(PENDING):
                if PENDING[key]['expires'] < time.time():
                    PENDING.pop(key)
            if len(PENDING) >= 50:
                raise RemoteError('预览过多，请稍后重试')
            PENDING[token] = {'owner': actor['id'], 'origin': c['origin'],
                              'session': hashlib.sha256(c['cookie'].encode()).hexdigest(),
                              'direction': 'upload', 'project': pid, 'target': pid,
                              'revision': job['revision'], 'preview': job['preview'],
                              'exists': True, 'remote_exists': bool(row),
                              'remote_revision': remote_revision,
                              'expected_revision': remote_revision,
                              'expires': time.time() + 1800}
            return dict(job, transfer=token, direction='upload',
                        preview=job['preview'], remote_exists=bool(row),
                        scope='完整项目状态（计划、资料、任务进度、账本历史）；不含账号与凭据；两端变化即拒绝覆盖')
        if action == 'adopt':
            # Bind an already-verified cloud transfer to this project so the user can
            # confirm it. Used when the local job lost its session after the upload
            # completed (e.g. a dropped connection during verification polling).
            S.need_admin(handler)
            pid = str(body.get('project') or '')
            cloud_token = str(body.get('token') or '')
            if not S.P.is_local(pid):
                raise RemoteError('请选择本机项目')
            status = request(c['origin'], '/api/desktop-transfer/status', {'token': cloud_token}, c['cookie'])
            if status.get('status') != 'ready':
                raise RemoteError('服务器还没有校验完这个传输')
            preview = status.get('preview') or {}
            if preview.get('project_id') != pid:
                raise RemoteError('这个传输属于别的项目')
            if preview.get('source_revision') != helix_sync.revision(S.P, pid):
                raise RemoteError('本机项目在上传后发生变化，请重新同步')
            listing = request(c['origin'], '/api/desktop/projects', cookie=c['cookie'])
            row = next((x for x in listing['projects'] if x['id'] == pid), None)
            remote_revision = row['revision'] if row else ''
            token = secrets.token_hex(16)
            PENDING[token] = {'owner': actor['id'], 'origin': c['origin'],
                              'session': hashlib.sha256(c['cookie'].encode()).hexdigest(),
                              'direction': 'upload', 'project': pid, 'target': pid,
                              'revision': preview['source_revision'], 'preview': preview,
                              'exists': True, 'remote_exists': bool(row),
                              'remote_revision': remote_revision,
                              'expected_revision': remote_revision,
                              'expires': time.time() + 1800}
            return {'transfer': token, 'direction': 'upload', 'preview': preview,
                    'remote_exists': bool(row), 'adopted': True,
                    'scope': '完整项目状态（计划、资料、任务进度、账本历史）；不含账号与凭据；两端变化即拒绝覆盖'}
        if action == 'preview':
            direction = body.get('direction')
            pid = str(body.get('project') or '')
            if direction == 'upload':
                # Oversized projects cannot travel as one archive; redirect the UI to
                # the streamed job path instead of failing inside the dialog.
                listing = request(c['origin'], '/api/desktop/projects', cookie=c['cookie'])
                row = next((x for x in listing['projects'] if x['id'] == pid), None)
                needs_stream = False
                try:
                    helix_sync.snapshot(S.P, pid)
                except helix_sync.SyncError as exc:
                    if 'streamed directory transport' in str(exc):
                        needs_stream = True
                    else:
                        raise RemoteError(str(exc))
                if needs_stream:
                    return {'requires_transfer': True, 'project': pid,
                            'error': '这个项目较大，已改为后台分块传输：请点“开始大项目上传”。'}
                if not S.P.is_local(pid):
                    raise RemoteError('请选择本机项目')
            if direction == 'upload':
                before = helix_sync.revision(S.P, pid)
                raw = helix_sync.snapshot(S.P, pid)
                if helix_sync.revision(S.P, pid) != before:
                    raise RemoteError('本机项目在导出时变化，请重新预览')
                preview = request(c['origin'], '/api/desktop-sync/stage', raw, cookie=c['cookie'])
                revision = before
                target_pid = pid
            elif direction == 'download':
                raw = request(c['origin'], '/api/desktop/export?' + urlencode({'project': pid, 'revision': str(body.get('revision') or '')}), cookie=c['cookie'], binary=True)
                S.need_admin(handler)
                preview = helix_sync.stage(raw, actor['id'], '云端项目')
                # Trust the archive's own revision: it is the exact state the
                # server handed us, even when the caller passed no expected one.
                revision = preview.get('revision') or str(body.get('revision') or '')
                target_pid = pid
                local_exists = S.P.is_local(preview.get('project_id') or pid)
                local_revision = helix_sync.revision(S.P, preview.get('project_id') or pid) if local_exists else ''
            else:
                raise RemoteError('请选择上传或下载')
            token = secrets.token_hex(16)
            for key in list(PENDING):
                if PENDING[key]['expires'] < time.time():
                    PENDING.pop(key)
            if len(PENDING) >= 50:
                raise RemoteError('预览过多，请稍后重试')
            exists = local_exists if direction == 'download' else True
            remote_exists = False
            remote_revision = ''
            if direction == 'download':
                remote_revision = revision
            else:
                listing = request(c['origin'], '/api/desktop/projects', cookie=c['cookie'])
                row = next((x for x in listing['projects'] if x['id'] == pid), None)
                remote_exists = bool(row)
                remote_revision = row['revision'] if row else ''
                if row and row['revision'] == preview.get('revision'):
                    preview = dict(preview, unchanged=True)
            if direction == 'download' and exists:
                expected_revision = local_revision
            elif direction == 'upload':
                expected_revision = remote_revision
            else:
                expected_revision = ''
            PENDING[token] = {'owner': actor['id'], 'origin': c['origin'],
                              'session': hashlib.sha256(c['cookie'].encode()).hexdigest(),
                              'direction': direction, 'project': pid, 'target': target_pid,
                              'revision': revision, 'preview': preview, 'exists': exists,
                              'remote_exists': remote_exists, 'remote_revision': remote_revision,
                              'expected_revision': expected_revision,
                              'expires': time.time() + 1800}
            return {'transfer': token, 'direction': direction, 'preview': preview,
                    'exists': exists, 'remote_exists': remote_exists,
                    'differs': bool(preview.get('revision')) != (remote_revision if direction == 'upload' else revision),
                    'scope': '完整项目状态（计划、资料、任务进度、账本历史）；不含账号与凭据；两端变化即拒绝覆盖'}
        if action == 'commit':
            r = PENDING.get(str(body.get('transfer') or ''))
            if not r or r['owner'] != actor['id'] or r['origin'] != c['origin'] or r['session'] != hashlib.sha256(c['cookie'].encode()).hexdigest() or r['expires'] < time.time():
                raise RemoteError('预览已过期或账号已变化，请重新预览')
            if r.get('result'):
                return r['result']
            if body.get('confirm') is not True:
                raise RemoteError('请明确确认同步')
            S.need_admin(handler)
            p = r['preview']
            if r['direction'] == 'upload':
                if helix_sync.revision(S.P, r['project']) != r['revision']:
                    raise RemoteError('本机项目已变化，请重新预览')
                result = request(c['origin'], '/api/desktop-sync/apply', {'token': p['token'], 'expected_revision': r.get('remote_revision', ''), 'target': r['project'], 'create_only': not r.get('remote_exists', False), 'owner': actor['id']}, c['cookie'])
            else:
                listing = request(c['origin'], '/api/desktop/projects', cookie=c['cookie'])
                row = next((x for x in listing['projects'] if x['id'] == r['project']), None)
                if row and row['revision'] != r['revision']:
                    raise RemoteError('云项目已变化，请重新预览')
                with S.IDENT.store_lock:
                    result = helix_sync.apply(S.P, p['token'], r.get('expected_revision', ''), r['project'], owner=actor['id'], create_only=not r['exists'])
            r['result'] = result
            if r['direction'] == 'upload':
                try:
                    import helix_remote_jobs
                    helix_remote_jobs.cleanup(r['project'])
                except Exception:
                    pass
            return result
        raise RemoteError('未知云连接操作')
