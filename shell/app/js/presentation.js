// Display names only. Original task IDs, file names and scientific values remain in the records.
export const fileName = value => {
 const name=String(value||'文件').split('/').pop().replace(/^[0-9a-f]{15}-(?=.)/,'');
 const names={'DISCOVERY_STATE_V4.json':'研究进展与关卡.json','VALIDATION_STATE_V4.json':'实验验证状态.json','CANDIDATE_FREEZE.json':'候选清单与冻结状态.json','review_result.template.json':'人工核对填写模板.json','manifest.json':'研究文件目录.json','README.md':'使用说明.md'};
 if(names[name])return names[name];
 if(/^CURRENT_SPRINT_TASKS_\d+\.csv$/.test(name))return '本轮任务表.csv';
 if(/^CURRENT_FOLLOWUP_TASKS_\d+\.csv$/.test(name))return '后续任务表.csv';
 if(/^CURRENT_CHECKIN_.*\.csv$/.test(name))return '任务与个人打卡对照表.csv';
 if(/^CURRENT_SPRINT_\d+\.md$/.test(name))return '本轮工作计划.md';
 if(/^human_[\da-f]+-input\.json$/.test(name))return '实操工具输入记录.json';
 if(/^human_[\da-f]+-output\.json$/.test(name))return '实操工具输出记录.json';
 if(/^human_[\da-f]+-/.test(name))return name.replace(/^human_[\da-f]+-/,'实操成果-').replace('run.json','运行记录.json').replace('log','日志');
 return name;
};
export const fileKind=f=>/CURRENT_.*(?:SPRINT|TASK|CHECKIN|FOLLOWUP)|任务|计划/.test(f.name)?'plan':/DISCOVERY_STATE|VALIDATION_STATE|CANDIDATE_FREEZE|review_result/.test(f.name)?'evidence':'work';
export const fileKindLabel=f=>({plan:'计划与任务',evidence:'科研依据',work:'操作成果与资料'})[fileKind(f)];
export const taskStatus=s=>({PENDING:'待开始',OPEN:'进行中',IN_PROGRESS:'进行中',COMPLETED:'已完成',DONE:'已完成',BLOCKED:'受阻',HOLD:'暂缓',UNKNOWN:'待确认',PASS:'通过',REJECTED:'未通过'})[String(s||'').toUpperCase()]||s||'待开始';
export const taskOutput=t=>({'ONBOARD-Q':'操作记录、账号和权限问题清单','ONBOARD-N':'真实工具日志、输入输出和参数','ONBOARD-Y':'结构核对表、处理前后文件','ONBOARD-D':'结构计算日志、参数和真实输出','ONBOARD-S':'检索记录、原文来源和本人阅读笔记','ONBOARD-TOOLS':'问题清单、复现步骤和处理结果','ONBOARD-ROLES':'全员分工确认表：负责内容、交付物、时间预算和复核人'})[t.task_id]||t.deliverable||'按任务要求提交成果';
