"""隔离执行有界科学查询，stdout 仅输出结构化结果。"""
import contextlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import site

if os.environ.get('PYTHONPATH'):
    site.addsitedir(os.environ['PYTHONPATH'])
os.environ['TOOLUNIVERSE_LIGHT_IMPORT'] = '1'
os.environ['TOOLUNIVERSE_PROFILE'] = ''


def rdkit(request):
    from rdkit import Chem, DataStructs
    from rdkit.Chem import Crippen, Descriptors, Lipinski, rdMolDescriptors, rdFingerprintGenerator
    smiles = str(request.get('smiles') or '')
    if not smiles or len(smiles) > 2000:
        raise ValueError('请输入不超过 2000 字符的 SMILES')
    mol = Chem.MolFromSmiles(smiles)
    if mol is None or mol.GetNumAtoms() > 500:
        raise ValueError('SMILES 无法解析或超过 500 原子上限')
    result = {'canonical_smiles': Chem.MolToSmiles(mol), 'molecular_weight': Descriptors.MolWt(mol), 'logP': Crippen.MolLogP(mol), 'TPSA': rdMolDescriptors.CalcTPSA(mol), 'HBD': Lipinski.NumHDonors(mol), 'HBA': Lipinski.NumHAcceptors(mol), 'rotatable_bonds': Lipinski.NumRotatableBonds(mol), 'atoms': mol.GetNumAtoms()}
    if request.get('other_smiles'):
        other = Chem.MolFromSmiles(str(request['other_smiles'])[:2000])
        if other is None or other.GetNumAtoms() > 500:
            raise ValueError('第二个 SMILES 无法解析或超过原子上限')
        generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
        result['tanimoto_morgan_r2_2048'] = DataStructs.TanimotoSimilarity(generator.GetFingerprint(mol), generator.GetFingerprint(other))
    return result


def tooluniverse(request):
    if request.get('action') == 'catalog':
        # 目录是随包真实工具定义；不把需要额外模型/密钥的定义当成已执行。
        data = Path(os.environ['PYTHONPATH']) / 'tooluniverse' / 'data'
        names = set()
        for path in data.glob('*.json'):
            try:
                definitions = json.loads(path.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                continue
            if isinstance(definitions, list):
                names.update(t['name'] for t in definitions if isinstance(t, dict) and isinstance(t.get('name'), str) and 'parameter' in t)
        names = sorted(names)
        query = str(request.get('query') or '').casefold()[:100]
        matches = [n for n in names if query in n.casefold()]
        return {'total': len(names), 'matched': len(matches), 'tools': matches[:80], 'catalog_only': True, 'note': '目录定义不等于全部工具已通过在线验证；部分工具另需 API 密钥、模型或可选依赖。'}
    from tooluniverse import ToolUniverse
    tu = ToolUniverse(load_workspace=False)
    tu.load_tools(include_tools=['PubChem_get_compound_properties_by_CID'])
    cid = int(request.get('cid') or 2244)
    if not 0 < cid < 1000000000:
        raise ValueError('CID 超出范围')
    name = 'PubChem_get_compound_properties_by_CID'
    if name not in tu.all_tool_dict:
        raise ValueError('此版本未提供受控 PubChem 查询，工具目录仍可使用')
    output = tu.run_one_function({'name': name, 'arguments': {'cid': cid}})
    if isinstance(output, dict) and (output.get('error') or output.get('status') == 'error'):
        raise ValueError(json.dumps(output, ensure_ascii=False))
    return {'function': name, 'cid': cid, 'output': output}


def biomcp(request):
    science = Path(os.environ['PYTHONPATH'])
    matches = list(science.rglob('biomcp.exe'))
    if not matches:
        raise ValueError('BioMCP 原生可执行文件缺失')
    exe = sorted(matches, key=lambda p: ('bin' not in p.parts, len(str(p))))[0]
    if request.get('action') == 'version':
        args = ['--version']
    else:
        # 命令契约在安装后用实际 --help 核验；无任意命令/路径执行入口。
        query = str(request.get('query') or 'MASH ADORA2A').strip()[:200]
        args = ['search', 'article', '--keyword', query, '--limit', '5', '--json', '--no-cache']
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    r = subprocess.run([str(exe), *args], capture_output=True, timeout=45, **kwargs)
    if r.returncode:
        raise ValueError(r.stderr.decode('utf-8', errors='replace')[-1500:])
    raw = r.stdout.decode('utf-8', errors='replace')
    try:
        output = json.loads(raw)
    except ValueError:
        output = raw[:100000]
    return {'command': ['biomcp', *args], 'output': output}


def main():
    request = json.load(sys.stdin)
    tool = request.get('tool')
    diagnostics = io.StringIO()
    try:
        with contextlib.redirect_stdout(diagnostics):
            output = {'rdkit': rdkit, 'tooluniverse': tooluniverse, 'biomcp': biomcp}[tool](request)
        result = {'ok': True, 'tool': tool, 'version': importlib.metadata.version({'rdkit': 'rdkit', 'tooluniverse': 'tooluniverse', 'biomcp': 'biomcp-cli'}[tool]), 'research_only': True, 'human_reviewed': False, 'result': output}
    except Exception as exc:
        result = {'ok': False, 'error': str(exc), 'tool': tool}
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == '__main__':
    main()
