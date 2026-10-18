"""开发用（不含任何真实姓名，主核/交叉核取自任务表本身）：用真实夹具在临时状态目录里造一条螺旋账本（播种本周任务 + V4 异常 + 一次主核 + 一次交叉核）。"""
import json, os, sys
from pathlib import Path
app = Path(sys.argv[1]); state = Path(sys.argv[2])
os.environ["MASH_AIDD_USER_DATA"] = str(state)
sys.path.insert(0, str(app))
from harness import sources, sprint, service
snap = sources.load_research_snapshot()
led, st = service.open_helix("MASH_2026_002", None, migrate=False)
sprint.record_sources(led, snap)
print(sprint.seed_from_sprint(led, snap, include_exceptions=True))
st = service.load_state(led)
y = next(b for b in st.open_tasks if b.get("task_id") == "W1-Y")
service.pair_base(led, y["base_id"], verdict="complement", reviewer=y.get("owner"), confirmed=True, decision="NEEDS_EVIDENCE",
                  note="5 个 MCULE 结构与标准 ID 已逐条核对；C7 手性未定义，保持 UNKNOWN。", source_refs=["sha256:1f9d423b DISCOVERY_STATE_V4.json"])
sprint.cross_review(led, y["base_id"], reviewer=y.get("cross_reviewer"), agree=True, note="独立打开 SDF 与登记表核对，一致。", confirmed=True)
q = next(b for b in service.load_state(led).open_tasks if b.get("task_id") == "W1-Q")
service.pair_base(led, q["base_id"], verdict="complement", reviewer=q.get("owner"), confirmed=True, decision="HOLD", note="评分协议第 4 条待组内确认后再冻结。")
print(led.verify().to_dict())
