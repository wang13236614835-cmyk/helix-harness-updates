"""随包科研材料和真实工具入口。原件只读；结果不改 Gate 或真人签署。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from functools import lru_cache
from urllib.parse import quote

HOME = Path(__file__).resolve().parents[2]
RESEARCH = HOME / 'research'
SCIENCE = Path(os.environ.get('HELIX_SCIENCE_ROOT') or HOME / 'runtime' / 'science')


@lru_cache(maxsize=16)
def _manifest(path, stamp):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'groups': {}, 'files': [], 'papers': 0, 'pdfs': 0}


def manifest(root=None):
    p = Path(root or RESEARCH) / 'manifest.json'
    try:
        stamp = (p.stat().st_mtime_ns, p.stat().st_size)
    except OSError:
        stamp = None
    return _manifest(str(p), stamp)


def summary(root=None):
    m = manifest(root)
    return {k: m.get(k) for k in ('groups', 'papers', 'pdfs', 'pdf_files', 'created_at', 'authority_modified')} | {'bundled': bool(m.get('files')), 'file_count': len(m.get('files', []))}


def files(query='', offset=0, limit=40, root=None):
    query = str(query).strip().casefold()[:160]
    rows = [x for x in manifest(root).get('files', []) if query in x['path'].casefold()]
    offset = max(0, int(offset))
    limit = min(100, max(1, int(limit)))
    return {'ok': True, 'total': len(rows), 'offset': offset, 'files': [{k: x[k] for k in ('path', 'bytes', 'sha256', 'group')} | {'url': '/api/research/file?path=' + quote(x['path'], safe='')} for x in rows[offset:offset + limit]]}


def file_path(rel, root=None):
    root = Path(root or RESEARCH)
    entries = {x['path'] for x in manifest(root).get('files', [])}
    if rel not in entries:
        raise ValueError('文件未登记在随包科研清单中')
    p = (root / rel).resolve()
    if not p.is_relative_to(root.resolve()) or not p.is_file():
        raise ValueError('包内文件不存在')
    return p


def papers(query='', root=None):
    try:
        rows = json.loads((Path(root or RESEARCH) / 'literature/index.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        rows = []
    query = str(query).casefold()[:160]
    rows = [p for p in rows if query in ' '.join(str(p.get(k) or '') for k in ('title', 'title_cn', 'abstract', 'doi', 'pmid')).casefold()]
    return {'ok': True, 'total': len(rows), 'papers': [{k: p.get(k) for k in ('id', 'title', 'title_cn', 'year', 'doi', 'pmid', 'abstract', 'source_url', 'pubmed_url', 'pdf_included', 'bundled_pdf')} for p in rows[:100]]}


def installed(distribution):
    for metadata in SCIENCE.glob(distribution.replace('-', '_') + '-*.dist-info/METADATA'):
        for line in metadata.read_text(encoding='utf-8').splitlines():
            if line.startswith('Version: '):
                return line.split(': ', 1)[1]
    return ''


def registry():
    tools = [
        {'id': 'rdkit', 'name': 'RDKit', 'distribution': 'rdkit', 'scope': 'SMILES 标准化、分子描述符、相似度', 'offline': True, 'source': 'https://github.com/rdkit/rdkit'},
        {'id': 'tooluniverse', 'name': 'ToolUniverse', 'distribution': 'tooluniverse', 'scope': '随包工具目录及 PubChem 只读查询', 'offline': False, 'source': 'https://github.com/mims-harvard/ToolUniverse'},
        {'id': 'biomcp', 'name': 'BioMCP', 'distribution': 'biomcp-cli', 'scope': '文献只读检索；同时提供 stdio MCP 入口', 'offline': False, 'source': 'https://github.com/genomoncology/biomcp'},
    ]
    for t in tools:
        t['version'] = installed(t['distribution'])
        t['status'] = 'installed' if t['version'] else 'unavailable'
    import vina_adapter
    vina_meta = vina_adapter.identity()
    tools.append({'id': 'vina', 'name': 'AutoDock Vina CPU', 'version': vina_meta.get('version', ''), 'status': 'installed' if vina_meta.get('version') else 'unavailable', 'scope': '已准备 PDBQT 的分子对接、构象与评分；结果不自动通过关卡', 'offline': True, 'source': 'https://github.com/ccsb-scripps/AutoDock-Vina'})
    return tools


def run(body):
    tool = str(body.get('tool') or '')
    if tool == 'vina':
        import vina_adapter
        return vina_adapter.run(body)
    if tool not in ('rdkit', 'tooluniverse', 'biomcp'):
        raise ValueError('工具未接入')
    if not any(t['id'] == tool and t['status'] == 'installed' for t in registry()):
        raise ValueError('工具依赖未安装')
    request = {k: v for k, v in body.items() if k in ('tool', 'smiles', 'other_smiles', 'cid', 'query', 'action')}
    env = {**os.environ, 'PYTHONPATH': str(SCIENCE), 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('science_worker.py'))], input=json.dumps(request).encode('utf-8'), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=60, **kwargs)
    except subprocess.TimeoutExpired:
        raise ValueError('工具 60 秒内未返回；未自动重试，请检查网络后手动运行')
    try:
        output = json.loads(result.stdout)
    except ValueError:
        raise ValueError('工具启动失败：' + result.stderr.decode('utf-8', errors='replace')[-1800:])
    if not output.get('ok'):
        raise ValueError(output.get('error') or '工具执行失败')
    return output
