# Groundwater Agent 前端交互 UI 详细设计

## 1. 产品定位
Groundwater Agent 是面向地下水科研与业务分析的垂直领域 Agent。用户通过自然语言提出研究问题，Agent 理解目标、澄清条件、读取知识和数据、选择适用方法、组织工作流、请求确认、执行计算、验证结果并交付成果。

不是传统 WebGIS：首页不以地图为中心；不以图层树、工具栏和 GIS 操作作为主导航；不要求用户先选算子。地图、图表、表格是按需科研工具和成果视图。

不是单纯聊天机器人：Session 持续承载研究交流；Task 管理目标和完成条件；Turn 表示一轮协作；WorkflowRun 表示实际执行；成果带有版本身份和 lineage。

## 2. 四对象交互合同

### Session
一个研究主题的持续交流容器。沿用当前约束：一个 Session 最多一个主 Task。保存会话消息并关联 Task、Draft、Confirmation、WorkflowRun、成果。展示标题、最近活动、归档状态、主任务摘要和待处理状态。新 Session 不继承旧 Task、确认或执行授权。普通咨询不强制创建 Task。会话搜索和分页只能使用服务端真实能力。

### Task
一个持续科研交付目标。包括原始目标、当前目标、目标修订、研究范围、约束、criteria、业务 steps、缺项、阻塞、评审和成果。Task 不等于一次聊天，也不等于 DAG。普通咨询不创建 Task。不能用前端百分比估算进度；criteria 未满足时不能显示完整完成。

### Turn
一次用户输入触发的 Agent 协作周期。一个 Task 可跨多个 Turn。Turn 可为普通咨询、澄清、数据准备、方法建议、Draft、Confirmation、结果解释。按 Turn 分组展示用户消息/附件、Agent 公开活动摘要、正式回复、澄清卡和本轮产生的业务卡片。模型请求和工具轨迹不逐条展示。

### WorkflowRun
一次经过授权的真实专业计算运行。Draft != Confirmation != WorkflowRun != Publication。一个 Task 可关联多个 WorkflowRun。每个 Run 显示状态、运行时间、冻结输入、参数快照、执行尝试、节点事实、恢复关系和成果。Run 成功不等于成果发布完成，也不等于科学验证通过。Stop 请求不等于取消成功。

## 3. 对象关系
Project → Session → Turns[]；Session → PrimaryTask?；Task → GoalRevisions / Criteria / BusinessSteps / Blockers / WorkflowRuns[]；WorkflowRun → Attempts / NodeFacts / PublishedResources[]。这是 UI 关系，不要求新增同名数据库表，以真实 API 和持久化模型为准。

## 4. 状态投影
前端只映射服务端状态到 UI label/tone/action，不创建业务状态机。Session badge 是展示聚合，不是后端新状态。Task lifecycle 与 state 分开显示。Turn status 来自快照。WorkflowRun、Publication、Scientific Validation 分开呈现。unknown enum 显示“状态待核实”，不得默认成功。网络不确定显示“状态待核对”。断线后以 snapshot reconciliation 为准。

## 5. 页面信息架构
一级：科研会话（核心）、研究资料库、专业知识依据（按角色/服务授权）、分析方法/能力目录（辅助）、系统信息（按权限）。
Session 内工作台：数据、方案与参数、任务执行、成果。对话默认主页面；工作台按需侧栏或路由展开；窄屏全屏工作台。避免永久三栏挤压对话。

## 6. 关键生命周期
新建 Session（不自动建 Task）→ 用户发起 Turn → Agent 识别持续目标并由服务端建立 Task → 澄清 → 数据准备和质量检查 → 方法建议 → Workflow Draft → Confirmation → WorkflowRun → 结果验证/发布 → 用户评审 → 继续当前研究或创建新 Session → 归档。

## 7. 页面交互
- 首页：科研问题输入、轻量场景示例；不放地图大屏、图层树、GIS 工具栏或架构介绍。
- 会话页：左侧研究会话列表，中央 Turn 时间线，输入框固定可见，Task 顶栏只在有 Task 时显示。
- 方案页：研究目标、数据、方法、关键参数、预期成果、限制；DAG 默认折叠。
- 确认页：冻结输入、参数来源、修订差异、预期输出；编辑/确认/拒绝。
- 执行页：业务阶段、真实节点状态、实际耗时、失败/恢复；不显示假百分比。
- 成果页：研究摘要、科学质量、专业视图、表格、报告、方法依据、限制、版本和 lineage。
- 资料库：目录、版本详情、质量、字段、单位、CRS、时间/空间范围、预览和 lineage。

## 8. 消息与队列
Turn 按用户消息、附件、ActivitySummary、正式回复、交互卡片、成果卡组织。输入框支持自然语言、上传、资料库选择、草稿、稍后发送、停止。Draft、Queued、Submitted、Receipt uncertain 必须区分。回执不确定时使用原 requestId 核对，不盲目生成新 ID 重发。

## 9. 状态边界
Agent 分析中用户修订：按现有协议终止/替代旧 AgentRun；等待确认时修订使旧确认失效；执行中补充要求进入后续队列；停止只发起请求，终态由 Executor 决定；Run 成功但结果未发布显示同步中；恢复生成新批次并遵循重新确认；新 Session 不继承旧授权。

## 10. 视觉
浅色专业科研软件；靛蓝主色；左导航 230–250px；中央对话 max-width 760–900px；右侧工作台按需展开 340–420px。窄屏单列。技术 ID、checksum、trace 默认折叠。科学限制必须可见。

## 11. 当前源码映射
优先复用：App.tsx、use-project-sessions.ts、use-message-actions.ts、use-session-stream.ts、TaskCard.tsx、ActivityPanel.tsx、WorkspacePanel.tsx、WorkflowDraftCard.tsx、WorkflowDraftGraph.tsx、ChatCards.tsx、MessageComposer.tsx、MessageQueue.tsx、ConfirmationEditor.tsx、ArtifactViewer.tsx、DatabaseWorkbench.tsx、style.css，以及 continuous-collaboration-design.md、webui.md。路径必须按本地 checkout 核实。

## 12. 实施原则
先合同后 UI：审计后端 schema/API/persistence → 建立字段关系矩阵 → 状态映射 → 事件同步 → 页面组件 → E2E。后端缺字段时先记 contract gap 和 ADR，不在前端推测或私造业务状态。


## 13. 统一视觉参考
视觉规范以 `design-references/DESIGN_SYSTEM.md` 为准。页面参考图：
- 首页：`design-references/01_home_research_entry.png`
- Session/Turn：`design-references/02_session_turn_conversation.png`
- Task：`design-references/03_task_workbench.png`
- WorkflowRun：`design-references/04_workflow_run_execution.png`
- Results：`design-references/05_results_and_analysis.png`
- Data Library：`design-references/06_data_library.png`
- Plan Confirmation：`design-references/07_plan_confirmation.png`
- Report Preview：`design-references/08_report_preview.png`

图片只定义布局层级、视觉风格和组件关系，不定义真实业务数据、后端状态或精确像素。所有页面必须遵守同一套颜色、字体、间距、圆角、按钮、状态和响应式规则。
