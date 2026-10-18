"""从本机完整源码构建无凭据组员包；不修改原配置或 data/。"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import stat
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTS = {'.py','.js','.css','.html','.svg','.ico','.png','.jpg','.jpeg','.webmanifest','.json','.txt','.ps1','.bat','.md'}
DENY_DIRS = {'data','__pycache__','node_modules','.git','backups','backup','_devstate','dist','release','releases'}
DENY_NAMES = {'credentials.json','cloud.json','people.json','models.json','install.json','python.txt','release.ps1','package_members.py'}
SECRET = re.compile(rb'(?:ntn_|secret_|sk-)[A-Za-z0-9]{20,}')

def safe_source(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if any(p.startswith('.') or p.lower() in DENY_DIRS for p in rel.parts): return False
    if any(p.lower() in DENY_NAMES for p in rel.parts): return False
    for parent in (path, *path.parents):
        if parent == root.parent: break
        info=parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0) & getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0):
            return False
    return path.is_file() and path.suffix.lower() in EXTS and not path.name.startswith('test_')

def build_local(root: Path, output: Path) -> dict:
    root=root.resolve()
    for required in ('server/serve.py','server/identity.py','app/index.html','launch/install.ps1','launch/helix.ps1','launch/common.ps1'):
        if not (root/required).is_file(): raise RuntimeError('缺少打包文件：'+required)
    files={}
    for folder in ('app','server','launch'):
        for path in sorted((root/folder).rglob('*')):
            if safe_source(path,root):
                raw=path.read_bytes()
                if SECRET.search(raw): raise RuntimeError('发现疑似密钥，拒绝打包：'+str(path.relative_to(root)))
                files[path.relative_to(root).as_posix()]=raw
    trust=root/'update-trust.json'
    if trust.is_file():files['update-trust.json']=trust.read_bytes()  # 组长发布密钥：组员只接受这个签名的更新
    # 不复制原 helix.config.json；只生成经过审阅的固定白名单配置。
    cfg={'project_name':'Helix 本机组员版','port':18772,'project_id':'HELIX_MEMBER_EMPTY','member_mode':True,'workbench_app':'','ledger_root':''}
    files['helix.config.json']=json.dumps(cfg,ensure_ascii=False,indent=2).encode('utf-8')
    files['安装Helix.bat']=('@echo off\r\npowershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch\\install.ps1" -Dest "%LOCALAPPDATA%\\HelixHarness-Member\\shell"\r\npause\r\n').encode('ascii')
    guide=root/'docs'/'组员注册与分发说明.md'
    if not guide.is_file(): raise RuntimeError('缺少组员版范围说明，拒绝打包')
    files['README.md']=guide.read_bytes()
    manifest={p:hashlib.sha256(raw).hexdigest() for p,raw in files.items()}
    files['MANIFEST.sha256.json']=json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8')
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'x',zipfile.ZIP_DEFLATED) as z:
        for name,raw in files.items(): z.writestr('Helix-Members/'+name,raw)
    with zipfile.ZipFile(output) as z:
        if z.testzip(): raise RuntimeError('压缩包完整性检查失败')
        for name in z.namelist():
            parts=Path(name).parts
            if any(p.lower() in DENY_NAMES or p.lower()=='data' for p in parts):
                raise RuntimeError('压缩包包含不应分发的状态文件：'+name)
    return {'path':str(output),'files':len(files),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'member_mode':True}

def build(root: Path, output: Path, *, local: bool=False, origin: str='') -> dict:
    if local:return build_local(root,output)
    # Cloud members need a browser, not a separate ledger or installation.
    origin=origin or os.environ.get('HELIX_PUBLIC_ORIGIN','')
    if origin:
        from urllib.parse import urlsplit
        parsed=urlsplit(origin)
        if parsed.scheme not in ('http','https') or not parsed.hostname or any(x in origin for x in '\r\n'):
            raise ValueError('请提供有效的团队云端网址')
    files={'README.txt':('Helix 3.1 组员加入说明\n\n请组长提供团队自己的云端网址或个人加入链接，打开后设置自己的账号和密码。之后使用普通组员入口登录。\n本说明包不包含固定服务器、账号、邀请码、Token或项目资料。\n需要完整本机程序时，请使用 Helix-Harness-3.1-Full-Windows.zip。\n本机账号与云端账号分别保存，Notion是任务进度镜像。\n').encode('utf-8-sig')}
    if origin:files['打开Helix云端.url']=('[InternetShortcut]\r\nURL='+origin+'\r\n').encode()
    files['MANIFEST.sha256.json']=json.dumps({name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()},ensure_ascii=False,indent=2).encode()
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'x',zipfile.ZIP_DEFLATED) as z:
        for name,raw in files.items():z.writestr('Helix-Members/'+name,raw)
    with zipfile.ZipFile(output) as z:
        assert z.testzip() is None
        for name,raw in files.items():assert z.read('Helix-Members/'+name)==raw
    return {'path':str(output),'files':len(files),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'account_scope':'cloud','contains_credentials':False}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--local',action='store_true',help='Optional independent local member source package')
    parser.add_argument('--origin',default='',help='Team cloud URL; omitted packages contain joining instructions only')
    args=parser.parse_args()
    out=args.output or ROOT.parent/'releases'/('Helix-Members-3.0-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.zip')
    print(json.dumps(build(ROOT,out,local=args.local,origin=args.origin),ensure_ascii=False,indent=2))

if __name__=='__main__': main()
