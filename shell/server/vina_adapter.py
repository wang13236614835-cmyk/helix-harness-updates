"""随包 CPU Vina 的有界执行。输入原件只读，输出独立保存，不修改科研关卡。"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import uuid

HOME = Path(__file__).resolve().parents[2]
RUNTIME = HOME / 'runtime/vina'
RUNS = HOME / 'shell/data/vina-runs'
_lock = threading.Lock()


def identity():
    try:
        return json.loads((RUNTIME / 'manifest.json').read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}


def example():
    folder = RUNTIME / 'example'
    return {'ok': True, 'label': '历史 3BEJ/MUF 工具自测示例（不是当前 ADORA2A 研究结论）', 'receptor_text': (folder / 'receptor.pdbqt').read_text(encoding='utf-8'), 'ligand_text': (folder / 'ligand.pdbqt').read_text(encoding='utf-8'), 'center': [22.01248387096774, -8.20790322580645, 9.63058064516129], 'size': [23.51, 26.071, 26.91], 'source': 'research/workbench-projects/MASH_PILOT_2026/reports/redocking_report.json', 'human_reviewed': False}


def result_file(run_id, name):
    if not re.fullmatch('[0-9a-f]{32}', run_id) or name not in ('pose.pdbqt', 'stdout.txt', 'stderr.txt', 'run.json'):
        raise ValueError('无效的 Vina 结果文件')
    path = (RUNS / run_id / name).resolve()
    if not path.is_relative_to(RUNS.resolve()) or not path.is_file():
        raise ValueError('结果文件不存在')
    return path


def _vector(value, positive=False):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError('对接盒中心和尺寸都需要三个数值')
    out = [float(v) for v in value]
    if not all(math.isfinite(v) and abs(v) <= 10000 for v in out):
        raise ValueError('对接盒参数必须是有限数值')
    if positive and not all(0 < v <= 60 for v in out):
        raise ValueError('每轴盒尺寸需大于 0 且不超过 60 Å')
    if positive and math.prod(out) > 50000:
        raise ValueError('当前入口的对接盒体积上限为 50000 Å³')
    return out


def run(request):
    meta = identity()
    exe = RUNTIME / 'vina.exe'
    if not exe.is_file() or hashlib.sha256(exe.read_bytes()).hexdigest() != meta.get('sha256'):
        raise ValueError('Vina 二进制缺失或校验不一致')
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    if request.get('action') == 'version':
        result = subprocess.run([str(exe), '--version'], capture_output=True, timeout=10, **kwargs)
        if result.returncode:
            raise ValueError('Vina 无法启动')
        return {'ok': True, 'tool': 'vina', 'version': meta['version'], 'result': result.stdout.decode('utf-8', errors='replace').strip(), 'research_only': True, 'human_reviewed': False}
    receptor = str(request.get('receptor_text') or '')
    ligand = str(request.get('ligand_text') or '')
    if not receptor or len(receptor.encode()) > 3_000_000 or not ligand or len(ligand.encode()) > 500_000:
        raise ValueError('请提供已准备的受体和配体 PDBQT（上限分别为 3 MB / 500 KB）')
    if not any(line.startswith(('ATOM  ', 'HETATM')) for line in receptor.splitlines()) or not any(line.startswith(('ATOM  ', 'HETATM')) for line in ligand.splitlines()):
        raise ValueError('PDBQT 没有原子记录')
    center, size = _vector(request.get('center')), _vector(request.get('size'), True)
    exhaustiveness = int(request.get('exhaustiveness', 2))
    seed = int(request.get('seed', 2026))
    if not 1 <= exhaustiveness <= 8 or not 0 <= seed <= 2147483647:
        raise ValueError('搜索强度需为 1–8；随机种子需为非负 32 位整数')
    if not _lock.acquire(blocking=False):
        raise ValueError('已有一个 Vina 任务运行中，请等待它结束')
    try:
        run_id = uuid.uuid4().hex
        folder = RUNS / run_id
        folder.mkdir(parents=True)
        inputs = {}
        for name, content in [('receptor.pdbqt', receptor), ('ligand.pdbqt', ligand)]:
            path = folder / name
            path.write_bytes(content.encode('utf-8'))
            inputs[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        command = [str(exe), '--receptor', 'receptor.pdbqt', '--ligand', 'ligand.pdbqt', '--out', 'pose.pdbqt', '--cpu', '2', '--exhaustiveness', str(exhaustiveness), '--seed', str(seed), '--num_modes', '3']
        for key, values in [('center', center), ('size', size)]:
            for axis, value in zip('xyz', values):
                command.extend(['--' + key + '_' + axis, str(value)])
        record = {'run_id': run_id, 'engine': 'AutoDock Vina CPU', 'version': meta['version'], 'executable_sha256': meta['sha256'], 'source': meta['source'], 'input_sha256': inputs, 'center_angstrom': center, 'size_angstrom': size, 'command': command, 'started_at': datetime.now(timezone.utc).isoformat(), 'research_only': True, 'human_reviewed': False}
        try:
            process = subprocess.run(command, cwd=folder, capture_output=True, timeout=120, **kwargs)
            stdout, stderr = process.stdout, process.stderr
            record['exit_code'] = process.returncode
        except subprocess.TimeoutExpired as error:
            stdout, stderr = error.stdout or b'', error.stderr or b''
            record['exit_code'] = None
            record['error'] = 'Vina 超过 120 秒，已停止该计算；未自动重试'
        (folder / 'stdout.txt').write_bytes(stdout)
        (folder / 'stderr.txt').write_bytes(stderr)
        pose = folder / 'pose.pdbqt'
        modes = []
        if pose.is_file():
            for line in pose.read_text(encoding='utf-8').splitlines():
                match = re.match(r'^REMARK VINA RESULT:\s+([-+\d.eE]+)\s+([-+\d.eE]+)\s+([-+\d.eE]+)', line)
                if match:
                    modes.append({'affinity_kcal_mol': float(match[1]), 'rmsd_lower_bound': float(match[2]), 'rmsd_upper_bound': float(match[3])})
        record['completed_at'] = datetime.now(timezone.utc).isoformat()
        record['modes'] = modes
        record['ok'] = record['exit_code'] == 0 and bool(modes)
        if not record['ok']:
            record.setdefault('error', stderr.decode('utf-8', errors='replace')[-1500:] or '没有生成带数值评分的构象')
        else:
            record['pose_sha256'] = hashlib.sha256(pose.read_bytes()).hexdigest()
        (folder / 'run.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        if not record['ok']:
            raise ValueError(record['error'] + '；记录编号 ' + run_id)
        return {'ok': True, 'tool': 'vina', 'version': meta['version'], 'research_only': True, 'human_reviewed': False, 'result': record, 'artifacts': {name: '/api/tools/vina/file?run_id=' + run_id + '&name=' + name for name in ('pose.pdbqt', 'stdout.txt', 'run.json')}}
    finally:
        _lock.release()
