"""Helix 便携启动器 + 守护进程。

- 双击“启动Helix.bat”：若本目录的 Helix 未运行，则启动后台守护进程，再打开浏览器。
- 守护进程：服务崩溃自动重启（退避重试）；收到更新重启请求（退出码 75）时先应用已签名的更新，
  新版本 45 秒内不健康则自动回滚到原版本。
- “停止Helix.bat”：只停止当前解压目录启动的进程。
"""
from pathlib import Path
import argparse, hashlib, json, os, socket, subprocess, sys, time, urllib.request, webbrowser

ROOT = Path(__file__).resolve().parent
SHELL = ROOT / 'shell'
DATA = SHELL / 'data'
TAG = hashlib.sha256(str(SHELL.resolve()).casefold().encode()).hexdigest()[:16]
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
STATE = DATA / 'portable-server.json'
STOP = DATA / 'stop.flag'
NOWIN = 0x08000000 if os.name == 'nt' else 0
DETACH = (0x00000008 | 0x00000200 | NOWIN) if os.name == 'nt' else 0  # DETACHED_PROCESS | NEW_PROCESS_GROUP


def health(port):
    try:
        return json.loads(OP.open('http://127.0.0.1:%s/api/health' % port, timeout=1.5).read())
    except Exception:
        return {}


def free(port):
    try:
        with socket.socket() as s:
            s.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False


def listening(port):
    """端口上是否有程序在接受连接（比 bind 测试可靠：刚退出的旧进程留下的 TIME_WAIT 不算占用）。"""
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=.5):
            return True
    except OSError:
        return False


def cfg_path():
    return SHELL / 'helix.config.json'


def load_cfg():
    try:
        return json.loads(cfg_path().read_text(encoding='utf-8-sig'))
    except Exception:
        return {}


def save_port(port):
    cfg = load_cfg()
    if cfg.get('port') != port:
        cfg['port'] = port
        cfg_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')


def read_state():
    try:
        return json.loads(STATE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def write_state(**kw):
    s = read_state()
    s.update(kw)
    DATA.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s), encoding='utf-8')


def python_exe(window=False):
    if os.name == 'nt':
        py = ROOT / 'runtime/python' / ('pythonw.exe' if not window else 'python.exe')
        if py.is_file():
            return py
        py = ROOT / 'runtime/python/python.exe'
        if py.is_file():
            return py
    return Path(sys.executable)


def server_env(port):
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', 'HELIX_PORT': str(port), 'HELIX_RESEARCH_ONLY_BUNDLED': '1',
           'HELIX_SUPERVISED': '1', 'HELIX_INSTANCE_TAG': TAG}
    env['HELIX_SCIENCE_ROOT'] = str(ROOT / 'runtime/science')
    env['PYTHONPATH'] = str(ROOT / 'runtime/science')
    for k in ('PYTHONHOME', 'HELIX_STATE_DIR', 'HELIX_PROJECT_ID', 'HELIX_CLOUD_MODE', 'MASH_AIDD_RESEARCH_ROOTS', 'HELIX_WORKBENCH_APP', 'HELIX_LEDGER_ROOT'):
        env.pop(k, None)
    return env


def log(msg):
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        with (DATA / 'supervisor.log').open('a', encoding='utf-8') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + msg + '\n')
    except OSError:
        pass


def _updater():
    sys.path.insert(0, str(SHELL / 'server'))
    try:
        import updater
        return updater
    except Exception as exc:
        log('updater unavailable: %s' % exc)
        return None


def wait_healthy(port, proc, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if proc.poll() is not None:
            return False
        if health(port).get('instance_id') == TAG:
            return True
        time.sleep(.3)
    return False


def supervise(port):
    """守护循环：在这里运行的进程一直存活，直到 stop.flag 出现。"""
    STOP.unlink(missing_ok=True)
    write_state(supervisor_pid=os.getpid(), port=port, instance_id=TAG)
    crashes, upd = [], _updater()
    while not STOP.exists():
        had_desktop = (ROOT / 'Helix.exe').is_file()
        applied = False
        if upd:
            try:
                r = upd.apply_staged(ROOT)
                if r:
                    log('applied update: %s' % r)
                    applied = bool(r.get('ok'))
            except Exception as exc:
                log('apply failed: %s' % exc)
        # 重启（更新 / 崩溃）时尽量沿用原端口，浏览器里打开的页面才能自动重连；
        # 只有端口确实被别的程序长期占用时才换端口。
        if listening(port) and health(port).get('instance_id') != TAG:
            for _ in range(60):
                time.sleep(.25)
                if not listening(port):
                    break
            else:
                for p in range(18774, 18875):
                    if free(p) and not listening(p):
                        port = p
                        break
                log('port busy, switched to %s' % port)
                save_port(port)
                write_state(port=port)
        out = open(DATA / 'serve.log', 'ab')
        err = open(DATA / 'serve.err.log', 'ab')
        proc = subprocess.Popen([str(python_exe()), str(SHELL / 'server/serve.py')], cwd=SHELL, env=server_env(port),
                                creationflags=NOWIN, stdout=out, stderr=err)
        write_state(pid=proc.pid, port=port, started=time.time())
        ok = wait_healthy(port, proc, 45)
        if upd:
            try:
                verdict = upd.confirm_or_rollback(ROOT, ok)
                if verdict:
                    log('update verify: %s' % verdict)
                if verdict == 'ok' and applied and os.name == 'nt' and (ROOT / 'Helix.exe').is_file() and os.environ.get('HELIX_DESKTOP_NO_SHORTCUTS') != '1':
                    subprocess.run([str(ROOT / 'Helix.exe'), '--register-shortcuts'], cwd=ROOT, creationflags=NOWIN, timeout=20)
                    if not had_desktop:
                        subprocess.Popen([str(ROOT / 'Helix.exe')], cwd=ROOT, creationflags=NOWIN)
                if verdict == 'rolled_back':
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(10)
                        except Exception:
                            proc.kill()
                    continue
            except Exception as exc:
                log('verify failed: %s' % exc)
        code = proc.wait()
        out.close(); err.close()
        if STOP.exists():
            break
        if code == 75:
            log('restart requested by server (update)')
            continue
        now = time.time()
        crashes = [t for t in crashes if now - t < 600] + [now]
        delay = min(60, 2 ** min(len(crashes), 6))
        log('server exited with %s; restart in %ss' % (code, delay))
        for _ in range(int(delay * 4)):
            if STOP.exists():
                break
            time.sleep(.25)
    STOP.unlink(missing_ok=True)
    write_state(pid=None, supervisor_pid=None)
    log('supervisor stopped')


def _kill(pid, script):
    if not pid:
        return
    if os.name == 'nt':
        command = ("$p=Get-CimInstance Win32_Process -Filter 'ProcessId=%d';if(-not $p){exit 0};"
                   "if($p.CommandLine -notlike '*%s*'){exit 2};Stop-Process -Id %d -Force -ErrorAction SilentlyContinue") % (
                       int(pid), str(script).replace("'", "''"), int(pid))
        subprocess.call(['powershell', '-NoProfile', '-Command', command], creationflags=NOWIN)
    else:
        try:
            os.kill(int(pid), 15)
        except OSError:
            pass


def stop():
    s = read_state()
    if not s:
        print('This portable copy has no recorded running process.')
        return
    DATA.mkdir(parents=True, exist_ok=True)
    STOP.write_text('stop', encoding='utf-8')
    h = health(s.get('port') or 0)
    if h and h.get('instance_id') != TAG:
        raise RuntimeError('The port belongs to another copy; no process was stopped.')
    _kill(s.get('pid'), SHELL / 'server/serve.py')
    for _ in range(40):
        if not read_state().get('supervisor_pid'):
            break
        time.sleep(.25)
    else:
        _kill(s.get('supervisor_pid'), ROOT / 'portable_launcher.py')
    write_state(pid=None, supervisor_pid=None)
    STOP.unlink(missing_ok=True)
    print('Helix stopped.')


def start(open_browser=True):
    cfg = load_cfg()
    port = int(cfg.get('port', 18772))
    s = read_state()
    for p in {port, s.get('port') or port}:
        if health(p).get('instance_id') == TAG:
            port = p
            break
    else:
        if not free(port) and listening(port):
            port = next(p for p in range(18774, 18875) if free(p))
        save_port(port)
        DATA.mkdir(parents=True, exist_ok=True)
        STOP.unlink(missing_ok=True)
        subprocess.Popen([str(python_exe()), str(ROOT / 'portable_launcher.py'), '--supervise', '--port', str(port)], cwd=ROOT,
                         env={**os.environ, 'PYTHONUTF8': '1'}, creationflags=DETACH, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=open(DATA / 'supervisor.err.log', 'ab'), close_fds=True)
        for _ in range(240):
            if health(port).get('instance_id') == TAG:
                break
            time.sleep(.25)
        else:
            raise RuntimeError('Helix did not become ready; see shell/data/serve.err.log and supervisor.log')
    url = 'http://127.0.0.1:%s/' % port
    if open_browser:
        webbrowser.open(url)
    print(json.dumps({'version': health(port).get('release'), 'url': url, 'instance_id': TAG}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--supervise', action='store_true')
    parser.add_argument('--refresh-supervisor', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    if args.refresh_supervisor:
        # A 3.1 supervisor caches its updater module. Restart that exact installation
        # so the bridge's new native allowlist is actually loaded before applying 3.2.
        s = read_state()
        h = health(s.get('port') or 0)
        if h and h.get('instance_id') != TAG:
            raise RuntimeError('The running service belongs to another installation.')
        STOP.write_text('refresh', encoding='utf-8')
        _kill(s.get('supervisor_pid'), ROOT / 'portable_launcher.py')
        _kill(s.get('pid'), SHELL / 'server/serve.py')
        write_state(pid=None, supervisor_pid=None)
        STOP.unlink(missing_ok=True)
        return start(False)
    if args.stop:
        return stop()
    if args.supervise:
        return supervise(args.port or int(load_cfg().get('port', 18772)))
    start(not args.no_browser)


if __name__ == '__main__':
    main()
