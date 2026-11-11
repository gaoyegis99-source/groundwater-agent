# Groundwater V6 — Source-Driven Vibecoding Plan

本套 MD 是**给 Codex / Claude Code / Cursor 等 AI 编码助手执行的现有系统功能完善计划**，不是开发 Vibecoding 系统。

## 源码基线
- Repository: `gaoyegis99-source/groundwater-agent`
- Branch: `main`
- Commit inspected: `3354fc2d959219f399ed667b993dcbdf3d0db89e`
- 实际项目根目录：`source/`
- 本轮读取 GitHub 递归树：1,746 个 blob，tree `truncated=false`
- 重要清单：7 workflows、7 executable models、106 operator catalog entries

## 首次执行
1. 阅读 `00_MASTER_PLAN.md`、`01_SOURCE_AUDIT.md`、`02_CALL_GRAPH.md`、`03_GAP_MATRIX.md`、`04_VIBECODING_RULES.md`。
2. 阅读 `20_SESSION_START_PROMPT.md`，先做只读复核，不直接写代码。
3. 一次只执行一张任务卡。
4. 当前源码与本计划不一致时，以源码为准，先更新本计划。
5. 每次结束更新 `90_PROGRESS.md`。

## 文档
- `00_MASTER_PLAN.md` 总路线
- `01_SOURCE_AUDIT.md` 源码事实审计
- `02_CALL_GRAPH.md` 模块职责与真实调用链
- `03_GAP_MATRIX.md` 功能差距和优先级
- `04_VIBECODING_RULES.md` AI 编码工作协议
- `05_M0_FEEDBACK_LOOP.md` 本地快速反馈
- `06_CHAT_CONFIRMATION.md` 对话和确认编辑器
- `07_AGENT_PREFLIGHT.md` Agent 路由与数据参数检查
- `08_WORKFLOW_DRAFTS.md` 草稿、DAG、执行前准备
- `09_DATA_WORKBENCH.md` Data 数据工作台
- `10_KNOWLEDGE_EVIDENCE.md` Knowledge 证据工作台
- `11_TASK_PROGRESS_RESULTS.md` 任务进度和结果复用
- `12_MAP_CHART_VIEWERS.md` 地图、栅格、图表
- `13_GROUNDWATER_WORKFLOWS.md` 现有地下水工作流完善
- `14_OPERATOR_GAP_ANALYSIS.md` 106 算子盘点与缺口
- `15_NEW_CAPABILITIES.md` Kriging、分类、DRASTIC 等
- `16_ADVANCED_MODELS.md` Pastas、MODFLOW 6
- `17_OPEN_SOURCE_MAPPING.md` 开源项目借鉴映射
- `18_ACCEPTANCE.md` 验收和测试策略
- `19_TASK_TEMPLATE.md` 任务卡模板
- `20_SESSION_START_PROMPT.md` 新会话提示词
- `90_PROGRESS.md` 进度记录

## 明确不做
不安排部署、安全加固、鉴权重构、多租户、兼容迁移、Registry、CI 平台治理或 Vibecoding 产品开发。若某个功能无法在此范围内完成，报告阻塞与替代方案，不擅自扩大范围。
