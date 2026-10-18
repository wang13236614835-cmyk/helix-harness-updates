"""Private GitHub Releases channel. Credentials stay in this installation's data directory."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_REPOSITORY = 'wang13236614835-cmyk/helix-harness-updates'
DEFAULT_FEED = 'github:' + DEFAULT_REPOSITORY
API_VERSION = '2026-03-10'


def _u():
    import updater
    return updater


def repository(value):
    value = str(value or '').strip()
    if value.startswith('github:'):
        value = value[7:]
    if value.startswith('https://github.com/'):
        value = value[len('https://github.com/'):].rstrip('/')
    if value.endswith('.git'):
        value = value[:-4]
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}', value):
        raise _u().UpdateError('请填写 GitHub 仓库链接或“用户名/仓库名”')
    if value.split('/')[1] in {'.', '..'}:
        raise _u().UpdateError('GitHub 仓库名称无效')
    return value


def _path():
    return _u().DATA / 'update/github.json'


def _config():
    return _u()._read_json(_path(), {})


def _secret(config):
    from models import _Blob
    rec = config.get('credential') or {}
    if not rec.get('blob'):
        return ''
    try:
        return _Blob.unprotect(rec['kind'], rec['blob']).decode('utf-8')
    except Exception:
        raise _u().UpdateError('GitHub 登录凭据无法解密，请重新连接') from None


def public_config():
    c = _config()
    return {'repository': c.get('repository') or DEFAULT_REPOSITORY,
            'connected': bool((c.get('credential') or {}).get('blob')),
            'private': c.get('private'), 'proxy': c.get('proxy') or '',
            'url': 'https://github.com/' + (c.get('repository') or DEFAULT_REPOSITORY)}


def saved_git_credential():
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never'}
    try:
        r = subprocess.run(['git', 'credential', 'fill'], input='protocol=https\nhost=github.com\n\n',
                           text=True, encoding='utf-8', capture_output=True, timeout=20, env=env,
                           creationflags=0x08000000 if os.name == 'nt' else 0)
        values = dict(line.split('=', 1) for line in r.stdout.splitlines() if '=' in line)
        if r.returncode == 0 and values.get('password'):
            return values['password']
    except (OSError, subprocess.TimeoutExpired):
        pass
    raise _u().UpdateError('本机没有可用的 GitHub 登录，请填入对此仓库有权限的访问令牌')


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p = urllib.parse.urlsplit(newurl)
        if p.scheme != 'https' or not (p.hostname in {'api.github.com', 'uploads.github.com', 'github.com'}
                                      or (p.hostname or '').endswith('.githubusercontent.com')):
            raise _u().UpdateError('GitHub 下载重定向地址无效')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected:
            redirected.remove_header('Authorization')
        return redirected


class Client:
    def __init__(self, token='', proxy=''):
        self.token, self.proxy = token, proxy

    def request(self, method, path, body=None, *, binary=False, upload=False, content_type=None, missing=False):
        if not path.startswith('/') or path.startswith('//'):
            raise _u().UpdateError('GitHub API 路径无效')
        host = 'uploads.github.com' if upload else 'api.github.com'
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        headers = {'User-Agent': 'HelixHarness-Updater', 'X-GitHub-Api-Version': API_VERSION,
                   'Accept': 'application/octet-stream' if binary else 'application/vnd.github+json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if data is not None:
            headers['Content-Type'] = content_type or 'application/json'
        # Only safe reads retry. A failed write is reconciled by its release tag/assets on the next attempt.
        attempts = 3 if method == 'GET' else 1
        configured = self.proxy or os.environ.get('HELIX_GITHUB_PROXY') or ''
        proxies = [configured] if configured else ['', 'DIRECT', 'http://127.0.0.1:7890']
        last = None
        for attempt in range(attempts):
            for proxy in proxies:
                handler = urllib.request.ProxyHandler({}) if proxy == 'DIRECT' else urllib.request.ProxyHandler({'https': proxy, 'http': proxy}) if proxy else urllib.request.ProxyHandler()
                opener = urllib.request.build_opener(handler, _Redirect())
                req = urllib.request.Request('https://' + host + path, method=method, data=data, headers=headers)
                try:
                    with opener.open(req, timeout=120 if binary or upload else 20) as response:
                        raw = response.read(_u().MAX_PACKAGE + 1)
                    if len(raw) > _u().MAX_PACKAGE:
                        raise _u().UpdateError('GitHub 下载内容过大')
                    return raw if binary else json.loads(raw or b'{}')
                except urllib.error.HTTPError as exc:
                    if missing and exc.code == 404:
                        return None
                    if exc.code in (401, 403, 404):
                        message = {401: 'GitHub 登录已失效，请重新连接', 403: 'GitHub 权限不足或请求被限流，请检查仓库权限并稍后重试',
                                   404: '无法读取此私有仓库，请先连接有仓库权限的 GitHub 账号'}[exc.code]
                        raise _u().UpdateError(message) from None
                    last = _u().UpdateError('GitHub 服务暂不可用（HTTP %s）' % exc.code)
                    if method != 'GET' or exc.code < 500 and exc.code != 429:
                        raise last from None
                    break
                except (OSError, urllib.error.URLError, TimeoutError):
                    last = _u().UpdateError('连接 GitHub 失败，请检查网络或代理；项目数据仍保存在本机')
                    if method != 'GET':
                        raise last from None
            if attempt + 1 < attempts:
                time.sleep(min(3, attempt + 1))
        raise last or _u().UpdateError('连接 GitHub 失败')


def configure(data):
    c = _config()
    old_repo = c.get('repository') or DEFAULT_REPOSITORY
    previous_feeds = _u().settings()['feeds']
    if data.get('forget'):
        _u()._write_json(_path(), {'repository': repository(data.get('repository') or c.get('repository') or DEFAULT_REPOSITORY)})
        return public_config()
    repo = repository(data.get('repository') or c.get('repository') or DEFAULT_REPOSITORY)
    token = str(data.get('token') or '').strip()
    if data.get('use_saved_login'):
        token = saved_git_credential()
    if not token:
        token = _secret(c)
    if not token:
        raise _u().UpdateError('私有仓库需要 GitHub 登录凭据，请使用本机已有登录或填写访问令牌')
    if any(ch.isspace() for ch in token) or len(token) > 1000:
        raise _u().UpdateError('GitHub 访问令牌格式无效')
    proxy = str(data.get('proxy', c.get('proxy', '')) or '').strip()
    if proxy and not re.fullmatch(r'https?://[^\s@/?#]+(?::\d+)?', proxy):
        raise _u().UpdateError('代理请填写不含账号密码的 http(s) 地址')
    info = Client(token, proxy).request('GET', '/repos/' + repo)
    if str(info.get('full_name', '')).casefold() != repo.casefold():
        raise _u().UpdateError('GitHub 返回的仓库与设置不一致')
    from models import _Blob
    kind, blob = _Blob.protect(token.encode())
    if kind not in {'dpapi', 'fernet'}:
        raise _u().UpdateError('系统加密存储不可用，GitHub 凭据未保存；Windows 请使用原用户运行，云端需配置加密密钥')
    _u()._write_json(_path(), {'repository': repo, 'private': bool(info.get('private')), 'proxy': proxy,
                             'credential': {'kind': kind, 'blob': blob}})
    if repo.casefold() != old_repo.casefold():
        old_feeds = {'github:' + old_repo, 'https://github.com/' + old_repo}
        _u().save_settings({'feeds': ['github:' + repo if feed in old_feeds else feed for feed in previous_feeds]})
    return public_config()


class GitHubFeed:
    def __init__(self, repo=None, client=None):
        c = _config()
        self.repo = repository(repo or c.get('repository') or DEFAULT_REPOSITORY)
        self.client = client or Client(_secret(c), c.get('proxy') or '')
        self.base = '/repos/' + self.repo

    def _asset(self, asset):
        aid = asset.get('id')
        if not isinstance(aid, int) or aid < 1:
            raise _u().UpdateError('GitHub 更新附件编号无效')
        return self.client.request('GET', self.base + '/releases/assets/' + str(aid), binary=True)

    def fetch(self):
        release = self.client.request('GET', self.base + '/releases/latest', missing=True)
        if release is None:
            # Distinguish an empty release channel from unavailable private-repository access.
            self.client.request('GET', self.base)
            return None
        if release.get('draft') or release.get('prerelease'):
            return None
        assets = {a['name']: a for a in release.get('assets', []) if a.get('state') == 'uploaded'}
        if 'helix-update.json' not in assets:
            raise _u().UpdateError('GitHub 最新版本缺少 Helix 更新清单')
        # Legacy 3.1 clients first receive the signed bridge via helix-update.json.
        # After restarting with this reader they receive the actual desktop app.
        manifest_name = 'helix-desktop-update.json' if 'helix-desktop-update.json' in assets else 'helix-update.json'
        manifest = json.loads(self._asset(assets[manifest_name]).decode('utf-8'))
        name = manifest.get('file', '')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+\.zip', name) or name not in assets:
            raise _u().UpdateError('GitHub 最新版本缺少程序更新包')
        return manifest, lambda: self._asset(assets[name])

    def publish(self, manifest, raw):
        _u().check_package(manifest, raw)
        tag = 'v' + manifest['version'] + '-' + manifest['build']
        tag_path = self.base + '/releases/tags/' + urllib.parse.quote(tag, safe='')
        release = self.client.request('GET', tag_path, missing=True)
        if release is None:
            release = self.client.request('POST', self.base + '/releases', {'tag_name': tag, 'name': 'Helix ' + manifest['version'],
                'body': manifest.get('notes') or 'Helix 程序更新（不含项目资料和账号）', 'draft': True, 'prerelease': False})
        rid = release.get('id')
        if not isinstance(rid, int) or rid < 1:
            raise _u().UpdateError('GitHub 发布编号无效')
        assets = {a['name']: a for a in release.get('assets', [])}
        contents = [(manifest['file'], raw, 'application/zip'),
                    ('helix-update.json', json.dumps(manifest, ensure_ascii=False, indent=2).encode(), 'application/json')]
        for name, content, mime in contents:
            if name in assets:
                if assets[name].get('state') != 'uploaded' or self._asset(assets[name]) != content:
                    raise _u().UpdateError('同名 GitHub 版本的附件不一致，请发布一个新构建；已有版本未被覆盖')
                continue
            if not release.get('draft', True):
                raise _u().UpdateError('已发布版本缺少附件，请发布一个新构建')
            path = self.base + '/releases/' + str(rid) + '/assets?name=' + urllib.parse.quote(name)
            self.client.request('POST', path, content, upload=True, content_type=mime)
        if release.get('draft'):
            release = self.client.request('PATCH', self.base + '/releases/' + str(rid), {'draft': False, 'make_latest': 'true'})
        return release.get('html_url') or 'https://github.com/' + self.repo + '/releases/tag/' + tag
