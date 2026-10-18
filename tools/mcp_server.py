"""可搬移的只读 MCP 入口；不安装到 Codex 或修改用户 MCP 配置。"""
from pathlib import Path
import json
import os
import site
import subprocess
import sys

HOME = Path(__file__).resolve().parents[1]
SCIENCE = HOME / 'runtime/science'
site.addsitedir(str(SCIENCE))
sys.path.insert(0, str(HOME / 'shell/server'))
mode = sys.argv[1] if len(sys.argv) > 1 else 'rdkit'

if mode == 'config':
    config = {'mcpServers': {name: {'command': str(HOME / 'runtime/python/python.exe'), 'args': [str(Path(__file__).resolve()), name]} for name in ('rdkit', 'tooluniverse', 'biomcp', 'vina')}}
    (HOME / 'mcp-servers.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    print('已生成本目录 mcp-servers.json；未修改任何外部客户端配置。')
    raise SystemExit(0)

if mode == 'biomcp':
    exe = SCIENCE / 'bin/biomcp.exe'
    raise SystemExit(subprocess.call([str(exe), 'serve']))

from mcp.server.fastmcp import FastMCP
import science

mcp = FastMCP('Helix ' + mode + ' read-only tools')
if mode == 'rdkit':
    @mcp.tool()
    def molecule_descriptors(smiles: str, other_smiles: str = '') -> dict:
        """真实 RDKit 描述符与可选 Morgan 相似度；未作科学或临床接受。"""
        return science.run({'tool': 'rdkit', 'smiles': smiles, 'other_smiles': other_smiles})
elif mode == 'tooluniverse':
    @mcp.tool()
    def pubchem_properties(cid: int) -> dict:
        """通过 ToolUniverse 实际调用 PubChem，读取指定 CID 的结构属性。"""
        return science.run({'tool': 'tooluniverse', 'cid': cid})

    @mcp.tool()
    def scientific_tool_catalog(query: str = '') -> dict:
        """读取随包工具定义。目录存在不等于各工具已具备模型、密钥或在线验证。"""
        return science.run({'tool': 'tooluniverse', 'action': 'catalog', 'query': query})
elif mode == 'vina':
    @mcp.tool()
    def vina_version() -> dict:
        """运行随包官方 Vina --version。"""
        return science.run({'tool': 'vina', 'action': 'version'})

    @mcp.tool()
    def vina_dock(receptor_pdbqt: str, ligand_pdbqt: str, center: list[float], size: list[float], exhaustiveness: int = 2, seed: int = 2026) -> dict:
        """真实有界 CPU 对接；输出构象/日志/指纹，不改变人工核验和科学关卡。"""
        return science.run({'tool': 'vina', 'receptor_text': receptor_pdbqt, 'ligand_text': ligand_pdbqt, 'center': center, 'size': size, 'exhaustiveness': exhaustiveness, 'seed': seed})
else:
    raise SystemExit('Unknown tool')

mcp.run(transport='stdio')
