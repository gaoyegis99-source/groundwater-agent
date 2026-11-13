# UI-01 合同缺口

下表为当前 checkout 的缺口与阶段决策。拟议的服务端合同待批准；本阶段使用已公开事实并保留未知态。证据见 [字段矩阵](CONTRACT_MATRIX.md) 与 [关系审计](ENTITY_RELATIONSHIP_AUDIT.md)。

| ID | 当前证据和影响 | 本阶段处理 | 后续所需能力 / ADR |
|---|---|---|---|
| G01 Session 目录 | SessionPage 仅列 title/createdAt/archived 和当前指针。没有最近活动、Task 摘要或待处理聚合；详情也无 title/archived。列表排序按创建 rowid。 | 目录按真实服务端搜索、分页、归档过滤；只对已读取且身份匹配的详情投影 Task/当前 Turn。 | 完整 UI-03 目录需 Kernel 提供最后活动时间及状态摘要，明确更新时间、排序、游标和 owner 范围。ADR-UI-005 |
| G02 Task 目标与历史 | task_revisions 持久保留规格与证据；objective 固定。API 只有当前 TaskContext，无当前目标独立字段、scope 或修订页。 | 展示原始目标、约束和现行 revision；文案不承诺历史可查。 | 如要查看历史，Kernel 增加只读版本合同；独立目标变更需要明确语义、授权及重新确认边界。ADR-UI-006 |
| G03 显式 Task 关系 | task_runs、confirmation_tasks 在 SQLite；公开 Turn/Run 不给 taskId+taskRevision；存在同会话无关咨询。 | Run 按精确 AgentRun/Turn 归属，Task 成果用 criteria evidence；不按 Session 将全部 Run 归入 Task。 | Kernel 暴露显式绑定及来源，区分未绑定与未投影。ADR-UI-006 |
| G04 Turn 分类 | Router 意图字段是请求决策，任务缺项可能经普通回复表达，显式 DIALOGUE/旧记录可缺具体意图。 | 仅展示明确路由意图；无确定证据保留未分类。等待输入使用 Task state。 | 若需完整分类，Kernel 定义持久 Turn 类型和结构化任务澄清事实。ADR-UI-006 |
| G05 Run 完整详情 | W 省略 sessionId/goalRevision/confirmationId/attempt/retryOf/inputReferences。E/H 能恢复部分关系。节点 API 返回计数而无逐尝试或节点输出。R 只有每个 action 最新 attempt。 | 保留所有已加载 E，严格按 Run ID 联结引用；查旧尝试输出用 W。缺行显示未提供。 | Kernel 完整 Run lineage/read DTO；Executor/Kernel 如需提供逐节点尝试和审计数据，应明确访问与脱敏范围。ADR-UI-007 |
| G06 发布可观测性 | ADR-UI-013 新增受运行归属约束的 Run publication 读取。Executor 用作业存储的发布 ID 向 Data 核对当前状态。 | Web 运行详情展示 Data 发布状态，成果同步和下载仍使用 Results 与精确 Data 引用。 | 缺历史关联时保留未知，Data 提交仍先于 Run 成功。验收见 UI-13 场景 13。 |
| G07 停止粒度 | H run_controls 是 AgentRun 级，真实状态含 PROCESSING，现有手写 union 漏写；Executor cancel_requested 未公开。 | 独立显示目标 AgentRun 停止请求，依 Turn/Run 权威终态显示取消。Selector 输入补齐真实 stop union。 | 如需逐 Run 取消请求或远端清理确认，需明确 Kernel/Executor 合同。ADR-UI-007 |
| G08 科学验证 | 无公开通用 Run 级验证状态、标准、证据或判定来源。Preparation PASS/FAIL、Task criteria、Data profile 与模型特定诊断各有不同范围。 | 显示科学验证状态未提供。 | 由产品/领域定义验证对象、标准版本、判定和证据，服务端持久化并公开。ADR-UI-008 |
| G09 DTO 精度 | H items/turns/task、C/D confirmation、R items 为开放对象；手写类型部分字段缺失或可空性不同（见矩阵）。生成校验只能核对外层。 | Selector 只取审计确认的字段，未知枚举安全回退。补齐停止 union、Task 证据字段、确认元数据与运行关联可空性。Selector 单测用符合投影的 fixture。 | 后续由 Kernel 拥有者发布强类型嵌套 DTO，明确兼容规则后重新生成。ADR-UI-007 |
| G10 恢复可用性 | recovery_source 检查同 action 最新 attempt，要求失败/超时/取消；Interaction 还要求当前 AgentRun 和 expectedGoalRevision、无待确认，且源批次存在。未公开 eligibility。 | 仅提供证据足够时的“检查恢复”入口候选，实际恢复由服务端核准并生成新确认。 | 若 UI 需准确展示禁用原因，增加服务端 eligibility 投影。ADR-UI-007 |

## UI-03 前置条件

UI-01、UI-02 的合同和验证通过后，仍需用户批准开始 UI-03。Session 导航基础范围可以使用已核实的目录、标题、归档、搜索和分页。若 UI-03 以完整目录最近活动、主任务和待处理状态为验收条件，先交付 G01 的服务端投影，或由用户明确批准降级范围。其他缺口留给对应任务；进入导航阶段不授权变更任何后端合同。

## UI-04 时间线补记

| ID | 现有合同 | 本阶段处理 |
|---|---|---|
| G11 回复内媒体与引用 | ReplyPresentation 只有 Markdown、段落与 Knowledge 引用；没有经过核验的逐段 Data/图片附件字段。Markdown Data 版本链接不含语义、对象和 checksum。 | 仅自动加载与同轮已核验的精确栅格成果引用匹配的图片。其余图片保留替代文字，版本链接可打开目录核对。 |
| G12 轮次归属与分批事实 | Task 是当前会话快照。Turn 没有完整的 Task revision 绑定；独立的控制消息也没有常规 Turn 的持久身份。公开活动的时间以服务端保存的 createdAt/startedAt/finishedAt 为准。 | 任务提示与控制消息独立呈现。业务卡严格按实际键联结，不由活动或回复反推 Task 或运行状态。 |
| G13 历史成果查询 | Session Results 只返回每个 action 最新 attempt。 | 旧 Run 与失败事实仍保留；缺引用时可用运行详情核对。本阶段没有为整个历史主动加载每个旧 Run。 |

## UI-05B 任务评审补记

| ID | 现有合同 | 本阶段处理 |
|---|---|---|
| G14 业务步骤失败与阻塞 | `TaskProgressV1.steps` 只投影 `id` 与 `complete`。规格步骤的 `gaps` 为已声明缺口，`blockingReasons` 属于整个任务。缺少逐步失败状态、原因以及与运行错误的显式关联。 | 逐项显示完成、未完成或待核实，并展示原有缺口。任务阻塞原因和会话执行失败分别展示。需要逐步失败审计时，由 Kernel 投影该步骤的状态与归属。 |
| G15 交付条件证据明细 | 满足时返回证据引用，未满足时证据为空。Task 成果是聚合列表，无逐条件资源或失败证据字段。Session Results 仅含每个 action 的最新尝试（G13）。 | 已满足项展示服务端证据 ID 或评审原文；按明确的运行、Agent、模型、成果名称与精确版本引用联结当前可用成果。缺失时仍展示服务端证据和任务级成果。若需逐条件历史成果和未满足原因，需公开相应证据明细。科学验证仍受 G08 限制。 |

## UI-06 数据准备补记

| ID | 现有合同 | 本阶段处理 |
|---|---|---|
| G16 替代版本与分析绑定 | Preparation 检查的 `currentVersion` 只提供版本号；公开检查未提供替代对象及对应冻结 action 的标识。 | 用 Data 读取该版本并附加到普通草稿。保留现有冻结批次编辑入口，禁止按参数名自动选中跨 action 绑定。直接指定替代对象或冻结 action 需要明确的服务端引用。 |

## UI-08 确认审查补记

| ID | 现有合同 | 本阶段处理 |
|---|---|---|
| G17 冻结参数来源 | Confirmation action 冻结参数值、输入引用和版本，未冻结可公开的逐参数来源。Preparation 和 Draft 的来源属于各自快照。 | 展示权威冻结值和基于显式替代关系的修订差异。来源需由服务端与冻结 action 一起持久化和公开。 |

## UI-13 局部阻塞补记

| 缺口 | 当前服务端事实 | 影响与待审批方案 |
|---|---|---|
| G18 同轮多个准备候选 · PASS | Kernel 用既有准备修订记录重建同轮候选，身份由目标类型、目标 ID、目标版本与规范化参数（含输入版本 ID）确定。相同身份的较新检查生效。现有确认 action 继续定义实际冻结范围。 | 无数据库迁移或公开 Schema 变更。旧记录可在读取时恢复。真实隔离服务验证缺坐标的流场与可确认趋势同时可见，趋势单独执行成功。见 ADR-UI-011 与场景 05。 |
