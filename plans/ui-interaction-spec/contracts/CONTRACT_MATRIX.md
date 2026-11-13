# UI-01 四对象字段矩阵

审计基线 `f0b9f6d6d5e1df970794d2cd16b9cdcec387b7fc`。Kernel SQLite v34、Executor SQLite v9、公开 HTTP v12、持久 SSE `session-event.v20`。SQLite/Python 使用 snake_case，HTTP 使用 camelCase。下表用 HTTP 名称，带下划线的字段为内部字段。多个字段共用一行时，各列结论适用于该行每个字段。

**列约定**。可空列的「否」允许空集合；「可缺」表示可省略而非可传 null。「权威」区分所属域的状态、服务端派生投影、持久事实和诊断信息。身份字段及时间戳本身无版本语义，除非版本列另有标注。「部分」指现有投影可用但覆盖或类型有缺口。

## 源码索引

| 简称 | 后端定义和投影 | API 来源 |
|---|---|---|
| S | Kernel [Session DDL](../../../source/kernel/src/platform_kernel/schema/interaction.py)、[模型](../../../source/kernel/src/platform_kernel/interaction/models.py)、[目录与历史](../../../source/kernel/src/platform_kernel/interaction/history.py)、[详情投影](../../../source/kernel/src/platform_kernel/interaction/api_projection.py)、[HTTP Schema](../../../source/kernel/src/platform_kernel/api/schemas.py) | `GET/POST /api/v12/sessions`（L/C）、`GET/PATCH /api/v12/sessions/{id}`（D/M）、Session 命令响应（C 形状） |
| T | Kernel [Task DDL](../../../source/kernel/src/platform_kernel/schema/tasks.py)、[模型](../../../source/kernel/src/platform_kernel/tasks/models.py)、[持久化](../../../source/kernel/src/platform_kernel/tasks/persistence.py)、[进度投影](../../../source/kernel/src/platform_kernel/tasks/progress.py) | `GET /api/v12/sessions/{id}/task`（T）、`GET …/messages`（H）；D/C 仅摘要 |
| H | S 的 `message_history`、[Agent DDL](../../../source/kernel/src/platform_kernel/schema/agent.py)、[Agent 时间线](../../../source/kernel/src/platform_kernel/agent_runtime/persistence/timeline.py)、[Agent 模型](../../../source/kernel/src/platform_kernel/agent_runtime/models.py)、[进展](../../../source/kernel/src/platform_kernel/interaction/progress.py) | `GET …/messages`（H）；`POST …/messages` 返回 Receipt（Q）；`GET …/events`（SSE） |
| W | Kernel [Workflow DDL](../../../source/kernel/src/platform_kernel/schema/workflow.py)、[WorkflowRun 模型](../../../source/kernel/src/platform_kernel/workflow_execution/models.py)、[运行详情投影](../../../source/kernel/src/platform_kernel/workflow_execution/api_projection.py)、[历史执行投影](../../../source/kernel/src/platform_kernel/workflow_execution/projection.py) | `GET /api/v12/workflow-runs/{id}?projectId=…`（W）、H 的 `turns[].executionTasks`（E）、`GET …/sessions/{id}/results`（R） |
| N | [Executor SQLite](../../../source/services/executor/src/platform_executor_service/database.py)、[Executor 读取](../../../source/services/executor/src/platform_executor_service/control_components/queries.py)、[Kernel 节点诊断](../../../source/kernel/src/platform_kernel/workflow_execution/node_diagnostics.py)、[读取代理](../../../source/kernel/src/platform_kernel/workflow_execution/owner.py) | W 子路由 `/nodes`（N）和 `/graph`（G）；Executor 内部 Job API |
| P | [DataVersionRefV2 / JobStatusV6](../../../source/packages/protocol-contracts/src/platform_protocol_contracts/execution_v6.py)、[Data 响应](../../../source/services/data/src/platform_data_service/api/responses.py)、[发布路由](../../../source/services/data/src/platform_data_service/api/routers/publication.py) | H/E/W/R 内精确引用；`GET /api/v12/data/publications/{publicationId}`；Data Version 详情 |

前端简称均位于 [web/src](../../../source/web/src/)。「生成」指 [public-api-v12.ts](../../../source/web/src/generated/public-api-v12.ts)，其 `dict[str, Any]` 仅生成开放对象，不能证明内部字段存在；下表以实际投影补证。手写类型见 [types.ts](../../../source/web/src/types.ts)。

## Session

| 字段 | 对象 | 后端定义/持久来源 | API | 前端类型/消费 | 可空 | 版本 | 权威 | UI |
|---|---|---|---|---|---|---|---|---|
| sessionId | Session | S sessions PK | L/C/D/M/H | SessionSummary/Session/History，use-project-sessions | 否 | — | 身份 | 是 |
| projectId | Session | S sessions | L/C/D/R | 生成/Session；SessionSummary 手写缺 | 否 | — | 归属 | 是 |
| owner_subject | Session | S sessions + immutable trigger | 审计 API；常规目录不返回 | 目录由服务端限 owner | 否 | — | 授权事实 | 是 |
| title | Session | S sessions.title；L 回退首条用户原文截断并可能加验证前缀 | L/M | SessionSummary/App、SessionRenameDialog | 是 | — | 元数据/列表显示投影 | 是；D 缺 |
| archived | Session | S sessions.archived_at 是否存在 | L/M | SessionSummary/use-project-sessions | 否 | — | 元数据投影 | 是；D 缺 |
| archived_at | Session | S sessions | 无 | 无 | 是 | — | 归档时间 | 部分 |
| createdAt | Session | S session.created 事件时间 | L | SessionSummary/App | 是 | — | 创建时间投影 | 是 |
| lastActivityAt | Session | 无公开投影 | 无 | 无 | 未提供 | — | 缺 | 否，G01 |
| goalRevision | Session | S sessions；初值 0，接收持久 Turn 增一 | L/C/D/H/Q | SessionSummary/Session/History | 否 | 会话轮次 | 当前轮次 | 是 |
| agentRunId | Session | S sessions.agent_run_id | L/C/D | SessionSummary/Session | 是 | 当前指针 | AgentRun 归属 | 是 |
| pendingAgentRunId | Session | S sessions.pending_agent_run_id | L | SessionSummary | 是 | 待启动指针 | 持久受理 | 是 |
| agent_start_state | Session | S sessions；启动调度 | 无直接公开字段 | 通过 H status | 是 | — | 内部状态 | 是（通过投影） |
| workflowDraftId | Session | S agent_requests / message_requests | L/C/D | Session/SessionSummary | 是 | 当前轮关联 | 投影 | 是 |
| confirmation | Session | S 当前 goalRevision 最后一条 confirmation | C/D | Session/ChatCards（交互以 H 为准） | 是 | Confirmation revision | 摘要 | 部分，缺 decision 字段 |
| agentOutput | Session | H agent_runs.output_json / AgentFinalV5 | C/D | Session（手写遗漏 workflowRuns） | 是 | AgentFinalV5 | 已校验持久回复 | 是 |
| agentSteps | Session | H agent_runs.steps_json | C/D | 生成；手写 Session 无 | 否 | 生命周期轨迹 | 诊断 | 是 |
| workflowRunIds | Session | W 当前 AgentRun actions 的最新尝试 | C/D | 生成；手写 Session 无 | 否 | 最新 attempt 投影 | 派生 | 部分，非历史 |
| remoteOutputs | Session | W 当前 AgentRun actions 的输出 | C/D | 生成；手写 Session 无 | 否 | 精确 Data 引用 | 派生 | 部分，非历史 |
| task | Session | T 当前 spec 的 taskId/revision/objective + progress 的 lifecycle/state | C/D 摘要；H 完整；L 缺 | 生成；History.task/TaskCard | 是 | Task revision | 当前主 Task 投影 | 部分，G01 |
| preparation | Session | T task_preparations 当前展示投影 | D/H、`GET …/preparation` | TaskPreparation/WorkspacePanel | D/H 是 | preparation revision | 准备事实 | 是 |
| controlProposals | Session | S control_proposals | C/D/H | ControlProposal/ControlProposalCard | 否 | 可带 taskRevision | 控制提议状态 | 是 |
| nextCursor | Session 目录 | S list_sessions，rowid 向后分页 | L | use-project-sessions | 是 | 游标 | 投影 | 是 |

## Task

| 字段 | 对象 | 后端定义/持久来源 | API | 前端类型/消费 | 可空 | 版本 | 权威 | UI |
|---|---|---|---|---|---|---|---|---|
| schemaVersion | Task | T TaskContextV1 | H/T | TaskContext | 否 | task-context.v1 | 格式 | 是 |
| spec.schemaVersion | Task | T TaskSpecV1 / spec_json | H/T | 开放对象，手写未声明 | 否 | task-spec.v1 | 格式 | 是 |
| spec.taskId | Task | T tasks PK / spec_json | H/T、C/D 摘要 | TaskContext/TaskCard | 否 | 跨修订稳定 | 身份 | 是 |
| spec.sessionId | Task | T tasks.session_id UNIQUE / spec_json | H/T | TaskContext 可选 | 否 | — | 主任务归属 | 是 |
| spec.revision | Task | T tasks.revision 与 task_revisions PK | H/T、C/D 摘要 | TaskContext/TaskCard | 否 | 独立任务规格修订 | 当前版本 | 是 |
| spec.objective | Task | T spec_json；首次用户原文，后续不可变 | H/T、C/D 摘要 | TaskContext/TaskCard | 否 | 原始目标贯穿版本 | 用户原文 | 部分，无单独 current objective |
| spec.sourceGoalRevision | Task | T spec_json | H/T | 手写未声明 | 否 | 原始目标来源轮次 | 来源 | 部分 |
| spec.sourceMessageId | Task | T spec_json | H/T | 手写未声明 | 否 | — | 来源 | 部分 |
| spec.constraints、spec.criteria | Task | T spec_json；`_apply_requirements` | H/T | TaskContext/TaskCard | 否 | 每修订快照 | 任务要求 | 是 |
| constraints/criteria[].id、text、sourceQuote | Task | T TaskRequirement；text 落盘为用户原文 | H/T | id/text 已声明；sourceQuote 未声明 | 否 | 可通过新 id 替换 | 用户事实 | 部分 |
| constraints/criteria[].interpretation | Task | T `_apply_requirements` | H/T | TaskCard | 是 | 随要求快照 | Agent 解释 | 是 |
| constraints/criteria[].sourceMessageId、sourceGoalRevision | Task | T `_apply_requirements` | H/T | 未声明 | 否 | 来源轮次 | 溯源 | 部分 |
| criteria[].kind | Task | T TaskCriterion | H/T | TaskContext/TaskCard | 否 | 随要求快照 | 验收种类 | 是 |
| criteria[].modelId、outputNames | Task | T TaskCriterion；仅 RESULT 要求 modelId 和非空 outputNames | H/T | 手写未声明 | modelId 可空；outputNames 否 | 随要求快照 | 验收规则 | 部分 |
| spec.suggestions、unresolvedItems | Task | T TaskSpecV1 / spec_json | H/T | TaskContext/TaskCard | 否 | 每修订快照 | 建议/缺项声明 | 是 |
| spec.steps[].id、title、dependsOn、criterionIds、gaps | Task | T TaskStep / spec_json | H/T | TaskContext/TaskCard | 否 | 每修订快照 | 业务步骤声明 | 是 |
| progress.schemaVersion | Task | T TaskProgressV1 | H/T | 手写未声明 | 否 | task-progress.v1 | 格式 | 是 |
| progress.lifecycle | Task | T TaskProgressV1，持久 tasks.lifecycle / task_completions + 派生 | H/T、C/D 摘要 | TaskContext/TaskCard | 否 | 按当前 taskRevision | 服务端生命周期 | 是 |
| progress.state | Task | T `_task_state` 及完成覆盖 | H/T、C/D 摘要 | TaskContext/TaskCard | 否 | 按当前 taskRevision 动态投影 | 服务端状态 | 是 |
| progress.criteria[].id、satisfied | Task | T `_criterion_progress` | H/T | TaskContext/TaskCard | 否 | 当前 spec | 服务端验收事实 | 是 |
| progress.criteria[].evidence | Task | T evidence_json，仅满足条件时投影 | H/T | TaskContext（UI-02 补齐证据字段） | 是 | 按 revision | 证据链接 | 部分 |
| evidence.criterionId、agentRunId | Task | T TaskEvidence，提交时补齐 AgentRun | H/T | TaskContext（UI-02 补齐） | 证据存在时否 | — | 溯源 | 部分 |
| evidence.workflowRunId、reviewQuote | Task | T TaskEvidence | H/T | TaskContext（UI-02 补齐） | 是 | — | Run/评审原文 | 是 |
| evidence.reviewGoalRevision | Task | T `_apply_evidence` 提交时补充 | H/T | 手写未声明 | 证据存在时否 | 证据提交轮次 | 溯源 | 部分 |
| evidence.workflowDraftId | Task | T `_draft_progress` | H/T | TaskContext/TaskCard | 可缺 | 精确 draft ID | 溯源 | 是 |
| progress.steps[].id、complete | Task | T `_step_progress` | H/T | TaskContext/TaskCard | 否 | 当前 spec | 服务端业务步骤结论 | 是 |
| progress.results | Task | T `_result_progress` 按条件验收，versionId 去重 | H/T | TaskContext/WorkspacePanel | 否 | 精确 Data 引用 | 已满足条件的成果子集 | 部分，非完整 Run 清单 |
| progress.blockingReasons | Task | T `_blocking_reasons` | H/T | TaskContext/TaskCard | 否 | 当前投影 | 阻塞原因码 | 是 |
| progress.metrics.totalTokens、durationMs、inputTokens、outputTokens、scope、requestCount、observationCallCount | Task | T `_task_metrics`，仅 task_runs；scope=EXPLICITLY_LINKED_RUNS | H/T | TaskCard；手写缺 inputTokens/outputTokens/scope | 否 | — | 记录用量统计 | 是 |
| created_at | Task | T tasks/task_revisions/task_completions 各自时刻 | 无 | 无 | 否 | 后两表随 taskRevision | 历史事实 | 部分 |
| task_revisions.agent_run_id、tool_call_id、spec_json、evidence_json、update_json | Task | T task_revisions；task_update_calls 按调用去重 | H/T 仅当前 spec/派生 evidence，SSE 仅修订号通知 | 无历史 API 类型 | 否 | (taskId, revision) | 不可变修订事实 | 否，G02 |
| task_runs.agent_run_id、task_revision、reason、created_at | Task | T task_runs 显式关联 | 无公开逐项关系 | 无 | 否 | taskRevision | 权威关联 | 否，G03 |
| confirmation_tasks.confirmation_id、task_id、task_revision | Task | T confirmation_tasks 及批准时修订核对 | 无公开逐项关系 | 无 | 否，绑定记录可无 | taskRevision | 权威绑定 | 否，G03 |
| currentObjective、goalRevisions、scope | Task | 无独立字段或目标变更模型 | 无 | 无 | 未提供 | — | 缺 | 否，G02 |

## Turn 与 Snapshot

| 字段 | 对象 | 后端定义/持久来源 | API | 前端类型/消费 | 可空 | 版本 | 权威 | UI |
|---|---|---|---|---|---|---|---|---|
| goalRevision | Turn | S messages/message_requests，H agent_runs | H/Q | Turn/mergeHistory/App | 否 | (sessionId, goalRevision) | 轮次身份 | 是 |
| agentRunId | Turn | H agent_runs；未落 AgentRun 时用 S 持久请求/启动指针 | H/Q | Turn/use-session-stream | 否 | — | 运行身份 | 是 |
| status | Turn | H AgentRun 状态；S 路由/待启动时由 Kernel 投影 | H | Turn/chat-state | 否 | — | 当前轮状态 | 是 |
| errorCode | Turn | H stable_error 或 S 路由错误 | H/Q | Turn/ActivityPanel | 是 | — | 诊断 | 是 |
| requestMode | Turn | S agent_requests.mode，未启动回退 resolved/requestedMode | H | Turn/message-routing | 否 | — | 模式投影 | 是 |
| requestId、requestedMode、resolvedMode | Turn | S message_requests | H/Q（Q 的前两项非空） | Turn/MessageReceipt/use-message-actions | H 是 | requestId 幂等；非版本 | 受理/路由事实 | 是 |
| routingStatus、routingSource | Turn | S message_requests | H/Q（Q 非空） | Turn/message-routing | H 是 | — | 路由状态/来源 | 是 |
| intentDecision、dialoguePolicy、semanticIntent、taskAction、controlIntent、clarificationReason | Turn | S message_requests 与 IntentDecisionV4；控制型请求通常单独投影 | H/Q | Turn/message-routing | 是 | Q.intent 为 intent-decision.v4 | 路由意图；非实际 Task 关系 | 部分，G03/G04 |
| clarificationRequired | Turn | S message_requests，旧记录默认 false | H/Q | Turn/message-routing | 否 | — | 路由澄清标记 | 部分，非任务缺项分类 |
| correctsRequestId、correctsAgentRunId | Turn | S message_requests / message_corrections | H/Q | Turn/use-message-actions | 是 | — | 纠正关联 | 是 |
| workflowDraftId | Turn | S workflow_drafts、agent_requests/message_requests | H/Q | Turn/WorkflowDraftCard | 是 | 不可变 draft 身份 | 关联 | 是 |
| timing.createdAt、startedAt、finishedAt | Turn | H agent_runs；S 路由/待启动回退 | H | Turn/ActivityPanel | 是（真实 AgentRun created/started 非空） | — | 时刻事实 | 是 |
| routingMetrics | Turn | S message_requests 路由统计，仅 AUTO | H | RoutingMetrics/ActivityPanel | 是 | — | 诊断 | 是 |
| routingMetrics.provider、model、inputTokens、outputTokens、totalTokens、durationMs | Turn | S route_*，未结束时 tokens 为 null | H | RoutingMetrics | 是 | — | 诊断 | 是 |
| routingMetrics.requestCount、unknownAttempts | Turn | S route_provider_requests/unknown_cost_attempts | H | RoutingMetrics | 否 | — | 诊断 | 是 |
| agentMetrics | Turn | H agent.complete/failed 事件 | H | AgentMetrics/ActivityPanel | 是 | — | 诊断 | 是 |
| agentMetrics.requestCount、observationCallCount、inputTokens、outputTokens、totalTokens、durationMs | Turn | H 终态事件；Agent 运行统计 | H | AgentMetrics | 否（父对象有值时） | — | 诊断 | 是 |
| diagnostics.reason、issues[].path、type、code | Turn | H `_public_diagnostics` 稳定错误白名单 | H | AgentDiagnostics，未声明 issue.code | 父可空；issues/code 可缺，其余否 | — | 诊断 | 是 |
| reply | Turn | H agent_runs.output_json → ReplyPresentationV1 | H | ReplyPresentation/App | 是 | 正式回复结构 | 已持久回复 | 是 |
| reply.conclusion、executionSummary | Turn | H ReplyPresentationV1 | H | ReplyPresentation | 是 | — | 正式正文 | 是 |
| reply.details、unresolvedItems、sources | Turn | H ReplyPresentationV1 | H | ReplyPresentation | 否 | — | 正式正文/引用 | 是 |
| reply 各段 markdown、sourceIndexes；sources[].title、versionId、unitId、checksum | Turn | H ReplySectionV1/ReplySourceV2 | H | ReplySection/ReplySource | 否 | 精确 Knowledge 版本 | 正文/来源 | 是 |
| interactionOptions[].label、description、message | Turn | H AgentFinalV5；仅合格的最近已完成回复允许提交选项 | H | InteractionOption/App | 否 | — | Agent 建议，非授权 | 是 |
| plans[].revision、goal、reason、steps | Turn | H agent_plans | H | AgentPlan/WorkspacePanel | 否 | 每 AgentRun planRevision | 本批计划 | 是 |
| plans[].steps[].stepId、tool、purpose | Turn | H AgentPlanStepV2 | H | AgentPlan | 否 | 随 planRevision | 本批计划 | 是 |
| activities、progressUpdates、confirmations、executionTasks | Turn | H `project_activities`、`build_turn_progress`、S confirmations、W session_execution_tasks | H | ActivityPanel/ChatCards/WorkspacePanel | 否 | 子对象各有身份 | 各自投影 | 是 |
| items[].messageId、goalRevision、role、content、inputReferences | Snapshot Message | S messages；用户引用取同轮持久请求，其他消息为空数组 | H | Message/App/WorkspacePanel | 否 | goalRevision 归属，精确引用 | 正式消息事实 | 是 |
| sessionId、goalRevision | Snapshot | S sessions | H | History/use-session-stream | 否 | 当前轮指针 | 归属 | 是 |
| nextCursor、eventCursor、snapshotAt | Snapshot | H 消息分页、同事务 max event_id 和读取时间 | H | History/use-session-stream | nextCursor 可空，其余否 | 消息游标/SSE 水位，不是 Task revision | 同事务边界 | 是 |
| task、preparation | Snapshot | T 当前 task/preparation | H | History/TaskCard/WorkspacePanel | 是 | 各自 revision | 当前快照，历史页也非当轮旧版本 | 是 |
| controlRequests[].requestId、agentRunId、status | Snapshot | S run_controls；REQUESTED/PROCESSING/COMPLETE | H、`POST …/stop`（另带 sessionId） | History/use-session-stream；审计时手写 union 缺 PROCESSING；UI-02 补齐 | 否 | requestId 幂等 | 停止处理状态 | 部分，G07 |
| controlProposals、controlMessages | Snapshot | S control_proposals/message_requests；控制消息独立投影，statusSnapshot/waitingReason/results 为读取当时的会话事实 | H | ControlProposal/ControlMessage/App | 否 | 请求身份 | 控制状态/当时事实 | 是 |
| controlMessages[].requestId、message、responseKind、semanticIntent、status、createdAt | Snapshot ControlMessage | S message_requests / control_proposal_queries | H | ControlMessage/App | 否（控制子集） | requestId | 请求、路由事实 | 是 |
| controlMessages[].controlIntent、clarificationReason、waitingReason | Snapshot ControlMessage | S message_requests / control_status_snapshot | H | ControlMessage/App | 是 | — | 控制意图/当前等待原因 | 是 |
| controlMessages[].statusSnapshot、results | Snapshot ControlMessage | S control_status_snapshot | H | ControlMessage/App | 否 | 精确 Data 引用 | 当前读取投影 | 部分，非历史时刻状态 |
| controlProposals[].proposalId、sessionId、sourceRequestId、intent、targetType、targetId、status、createdAt | Snapshot ControlProposal | S control_proposals / proposal_view | H/C/D | ControlProposal/ControlProposalCard | 否 | 提议身份 | 状态与作用域 | 是 |
| controlProposals[].taskRevision、decisionRequestId、decidedAt | Snapshot ControlProposal | S control_proposals / proposal_view | H/C/D | ControlProposal/ControlProposalCard | 是 | 可关联 Task revision | 决定事实 | 是 |
| taskId、taskRevision、turnKind | Turn | DB 有部分显式绑定；Turn 无统一字段 | H 无 | 无 | 未提供 | — | 缺 | 否，G03/G04 |

## WorkflowRun

| 字段 | 对象 | 后端定义/持久来源 | API | 前端类型/消费 | 可空 | 版本 | 权威 | UI |
|---|---|---|---|---|---|---|---|---|
| runId / workflowRunId | WorkflowRun | W workflow_runs.run_id PK | W / E/R/N/G | 生成/ExecutionTask/Result/WorkspacePanel | W/E/N/G 否；R 未执行 action 可空 | 每次 attempt 新 ID | 身份 | 是 |
| sessionId | WorkflowRun | W workflow_runs.session_id FK | W 缺；H/R 外层上下文 | History/调用方范围 | 否（DB） | — | 归属 | 部分 |
| projectId | WorkflowRun | W workflow_runs.project_id | W/R 外层 | 生成、请求范围 | 否 | — | 归属 | 是 |
| agentRunId、toolCallId、position | WorkflowRun | W workflow_runs；按 AgentRun action 和 attempt 关联 | W/E；R（手写 Result 缺 position） | 生成/ExecutionTask/Result | 否 | 联合身份，position 批内顺序 | 关联事实 | 是 |
| goalRevision | WorkflowRun | H 由 AgentRun/Turn 带入 | E/R；W 缺 | ExecutionTask/Result | 否 | Session 轮次 | 归属 | 是 |
| taskId、taskRevision | WorkflowRun | T 可经 confirmation_tasks 回溯；无 Run 直接列 | W/E/R 缺 | 无 | 绑定可无 | Task revision | 条件性关联 | 否，G03 |
| confirmationId | WorkflowRun | W workflow_runs.confirmation_id FK | E；W/R 缺 | ExecutionTask（UI-02 修正为可空） | 是 | 精确批次 | 授权关联 | 部分，G05 |
| attempt、retryOfWorkflowRunId | WorkflowRun | W workflow_runs，按 agentRunId/toolCallId 递增 | E；R attempt 仅最新且可空；W 缺 | ExecutionTask/Result | attempt 否，retryOf 是 | 重试链 | 事实 | 是（需 H） |
| isLatestAttempt | WorkflowRun | W `MAX(attempt)` 同 action | E | ExecutionTask/WorkspacePanel | 否 | attempt 投影 | 派生 | 是 |
| modelId、modelVersion、workflowId、workflowVersion | WorkflowRun | W workflow_runs | W/E，R 仅 modelId | 生成/ExecutionTask/WorkspacePanel | 否 | modelVersion/workflowVersion 冻结 | 身份/版本 | 是 |
| modelChecksum、workflowChecksum | WorkflowRun | W workflow_runs | W；H Confirmation Action 也有 | 生成；E 无 | 否 | 冻结摘要 | 完整性事实 | 是 |
| executionService、executorId | WorkflowRun | W workflow_runs | W | 生成，节点调用经 Kernel | 否 | — | 派发归属 | 是 |
| arguments | WorkflowRun | W arguments_json，来自冻结 action | W；H Confirmation Action | 生成/Action/ConfirmationEditor | 否（值可为 JSON null） | 随 Run 冻结 | 参数事实 | 是 |
| inputReferences | WorkflowRun | W input_references_json | W 缺；可经 E confirmationId + toolCallId 查 H Confirmation Action | Action/WorkspacePanel | DB 否；联结可能缺 | 精确 Data 引用 | 冻结输入 | 部分，G05 |
| status | WorkflowRun | W workflow_runs.status；Executor Job 同步或 Kernel 派发/批次终态 | W/E | ExecutionTask/WorkspacePanel | 否 | 按 attempt | 执行事实 | 是 |
| remoteJobId | WorkflowRun | W workflow_runs.remote_job_id | W | 生成；N/G 代理内部用 | 是 | 每次 attempt | 跨服务身份 | 是 |
| stage、blockedByWorkflowRunId | WorkflowRun | W session_events stage/blocked 事实 | E | ExecutionTask/ChatCards | 是 | 作用域 runId | 进度/阻塞投影 | 是 |
| communicationError、errorCode | WorkflowRun | W workflow_dispatches.last_communication_error / workflow_runs.error，经规范化 | E | ExecutionTask/ChatCards | 是 | — | 通信与执行诊断 | 是 |
| error | WorkflowRun | W workflow_runs.error | W | 生成；展示优先 E.errorCode | 是 | — | 诊断 | 是 |
| createdAt、startedAt、finishedAt、durationMs | WorkflowRun | W workflow_runs | W/E | 生成/ExecutionTask/WorkspacePanel | createdAt 否，其余是 | — | 时刻/耗时 | 是 |
| outputs / remoteOutputs、outputCount | WorkflowRun | W outputs_json；E 计数；R 最新 action attempt 输出 | W/R/E 分别 | 生成/Result/ChatCards/WorkspacePanel | 否 | 精确 Data 版本 | 资源事实/计数 | 是，历史 attempt 查 W |
| R.status、R.agentStatus | WorkflowRun 结果行 | H `action_status` / AgentRun；成功映为 COMPLETE，超时/取消可折叠 FAILED | R | Result（agentStatus 未声明）/ChatCards | 否 | 最新 attempt 投影 | action/turn 状态 | 部分，不能替代 E/W |
| dispatch.state、deadline_at、next_poll_at、claim_state、target_digest | WorkflowRun | W workflow_dispatches/workflow_service_bindings | 无直接公开字段 | 无 | claim_state 是；绑定行可无，其余否 | target_digest 固定派发目标 | 内部派发事实 | 部分 |
| publication_id、cancel_requested | WorkflowRun 关联 Job | N executor_jobs；P JobStatusV6 不含两字段 | 内部数据库；W/E 无 | 生成 Data Publication 独立可用 | publication_id 是；cancel_requested 否 | 每 Job | Data 关联、取消请求 | 否，G06/G07 |
| scientificValidation | WorkflowRun | 无通用 Run 级合同 | 无 | 无 | 未提供 | — | 缺 | 否，G08 |
| workflowRunId、availability、nodes | WorkflowRun 节点/图响应 | N Kernel 代理；NOT_DISPATCHED/UNAVAILABLE/AVAILABLE | N/G | 生成/WorkflowNodeDetails | 否 | 以 runId 定位 | 诊断可用性 | 是 |
| nodes[].nodeId、operatorId、runtime、status | WorkflowRun 节点 | N job_nodes | N（G 仅 nodeId） | 生成/WorkflowNodeDetails | 否 | (remoteJobId, nodeId)；operatorVersion 未公开 | 执行节点事实 | 是 |
| nodes[].attemptCount、startedAt、finishedAt、errorCode | WorkflowRun 节点 | N job_nodes + node_attempts 聚合 | N | 生成/WorkflowNodeDetails | 是 | attemptCount 为计数 | 节点诊断 | 部分，逐尝试未公开 |
| nodes[].dependsOn | WorkflowRun 图 | N job_nodes.dependencies_json | G | 生成/WorkflowNodeDetails | 否 | 本次运行 DAG | 拓扑事实 | 是 |
| node_attempts.attempt_id、attempt_number、status、started_at、finished_at、error_code | WorkflowRun 节点尝试 | N node_attempts | 无公开逐项合同 | 无 | 时间和错误可空，其余否 | 节点 attempt | 持久诊断 | 部分，G05 |

## 共享关联字段

| 字段 | 对象 | 后端定义/持久来源 | API | 前端类型/消费 | 可空 | 版本 | 权威 | UI |
|---|---|---|---|---|---|---|---|---|
| confirmationId、agentRunId、goalRevision | Turn Confirmation | S confirmations | H、C/D 当前 | Confirmation/ChatCards | 否 | 精确批次、会话轮次 | 归属 | 是 |
| status、kind、revision | Turn Confirmation | S confirmations | H、C/D 当前 | Confirmation/ChatCards | 否 | 每 AgentRun Confirmation revision | 审批状态/批次种类 | 是 |
| reason、createdAt、requestId | Turn Confirmation | S confirmations/confirmation_response | H、C/D 当前 | Confirmation（UI-02 修正 reason/createdAt 可空） | 是（created_at DB 非空，DTO 可空） | requestId 幂等 | 原因/时刻 | 部分 |
| decision、decisionState | Turn Confirmation | S confirmations | H（C/D 不投影） | Confirmation/canConfirm | 是 | — | 决定及处理状态 | 是（以 H 为准） |
| replacesConfirmationId、sourceWorkflowRunId | Turn Confirmation | S confirmations | H、C/D 当前 | Confirmation/ConfirmationEditor | 是 | 替代链/恢复来源 | 关联事实 | 是 |
| actions[].toolCallId、tool、modelId、modelVersion、modelChecksum、workflowId、workflowVersion、workflowChecksum、inputSchemaChecksum、fingerprint | Turn Confirmation | S confirmation_actions；position 排序 | H、C/D 当前 | Action/ConfirmationEditor（校验和、fingerprint 未声明） | 否 | 冻结代码/输入合同身份 | 批次授权事实 | 部分 |
| actions[].arguments、inputReferences | Turn Confirmation | S confirmation_actions JSON | H、C/D 当前 | Action/ConfirmationEditor/WorkspacePanel | 否 | 冻结参数/精确 Data 引用 | 批次授权事实 | 是 |
| kind、service、datasetId、versionId、semanticId、objectId、checksum、mediaType、format、sizeBytes | ResourceReference | P DataVersionRefV2 | H/W/R 和 Data | ResourceReference/ResourceLink/WorkspacePanel | 否 | versionId/semanticId/objectId/checksum 锁定精确版本 | 资源身份 | 是 |
| outputName | ResourceReference | P DataVersionRefV2 | W/R/H 成果 | ResourceReference/ChatCards | 可缺 | 名称随运行定义 | 命名成果 | 是 |
| agentRunId、goalRevision、segmentId、activityId、kind、purpose、status、title | Turn Activity | H agent.activity 持久事件及 fold | H/SSE | Activity/ActivityPanel | 否 | 按 AgentRun/activityId 折叠 | 活动状态/公开摘要 | 是 |
| summary、toolName、toolCallId、errorCode | Turn Activity | H agent.activity | H/SSE | Activity/ActivityPanel | 是 | — | 公开描述/诊断 | 是 |
| createdAt、startedAt、finishedAt | Turn Activity | H event fold | H | Activity | createdAt 否，其余是 | — | 时刻投影 | 是 |
| contextUsage、outputBytes、outputTokens、capability、semanticRole、templateKey、outcomeCode、facts | Turn Activity | H agent.activity，按活动种类提供 | H/SSE | Activity/ActivityPanel | 可缺 | — | 诊断/公开事实 | 是 |
| progressId、agentRunId、code、status、scope、title、summary、facts | Turn ProgressUpdate | H build_turn_progress | H | ProgressUpdate/ActivityPanel | 否 | 按 AgentRun/progressId | 服务端业务里程碑投影 | 是 |
| workflowRunId、createdAt、startedAt、finishedAt | Turn ProgressUpdate | H build_turn_progress | H | ProgressUpdate/ActivityPanel | 是 | 精确 Run（单项时） | 归属/时间 | 是 |

## 写入入口与身份校验

| API（Session 相对路径） | 请求字段与事实 |
|---|---|
| `POST /sessions` | `projectId` 必填。按认证 owner 创建独立 Session，初始 revision=0。 |
| `PATCH /{id}` | `title` 或 `archived` 至少一项，允许省略/传 null 表示该项无更新。title 1–120 字符，归档仅为目录元数据。 |
| `POST /{id}/messages` | `requestId/message/requestedMode` 必填；`inputReferences` 默认空、最多 20 个，按 version/object 去重；`correctsAgentRunId/correctsRequestId` 可空，后者要求显式 mode。Q 用原 requestId 核对受理；Q 的 `schemaVersion=message-request.v5`、原请求 `request`、`intent` 补充请求/路由快照。 |
| `POST /{id}/stop` | `requestId, agentRunId` 必填，目标必须是当前已启动或待启动 AgentRun。返回受理状态。 |
| `POST /{id}/confirm`、`/reject` | `confirmationId` 必填；拒绝另需 `reason`。批准核对当前批次、决策处理状态、Task 修订绑定。 |
| `POST /{id}/confirmations/{confirmationId}/revisions` | `requestId, expectedGoalRevision, actions[]`，每项 `toolCallId, arguments` 及可空 `expectedInputReferences`；原 action 集保持，Data 选择精确核对。 |
| `POST /{id}/workflow-runs/{runId}/recoveries` | `requestId, expectedGoalRevision`；仅当前 AgentRun 最近合格失败/超时/取消尝试，服务端生成新 Confirmation。 |
| `GET /{id}/workflow-drafts/{draftId}[/export]` | 草稿的精确 ID；资源包含 schemaVersion、draftId/sessionId/agentRunId/goalRevision、status、payload、diagnostics、errorCode、checksum、createdAt。payload 可用时，export 路由返回 payload JSON 下载，而非详情 envelope。payload 明示 executionAllowed=false。详情投影不填 checksum/createdAt，由 Schema 给 null 默认值。 |

## Snapshot 通知字段

| 字段 | 对象 | 后端定义/持久来源 | API | 前端类型/消费 | 可空 | 版本 | 权威 | UI |
|---|---|---|---|---|---|---|---|---|
| id | SessionEvent | S session_events.event_id | 持久 SSE 帧 | use-session-stream/session-stream-state | 否；preview 无 id | 增量游标 | 重放水位 | 是 |
| schemaVersion、type、data、createdAt | SessionEvent | S session_events + session_streams 封装 | 持久 SSE JSON | use-session-stream；事件清单 fixture | 否 | session-event.v20 | 提交通知，快照核对 | 是 |
| data.taskId、taskRevision | Task Event | T task.created/revised/completed/reopened | SSE | 触发 H 刷新 | 否 | Task revision | 通知 | 是 |
| data.workflowRunId、confirmationId、attempt、retryOfWorkflowRunId | Workflow Event | W workflow.queued | SSE | 触发快照刷新 | confirmationId/retryOf 可空，其余否 | Run attempt | 持久关联通知 | 是 |
| data.stage | Workflow Event | W workflow.stage_changed | SSE、折叠进入 E.stage | ExecutionTask | 是 | 运行阶段事实 | 阶段通知 | 是 |
| data.agentRunId、requestId | Stop Event | S control.requested/control.complete | SSE | use-session-stream，H 核对 | 否 | requestId 幂等 | 命令处理通知 | 是 |
| data.confirmationId | Confirmation Event | S confirmation.*，如 processing/confirmed/rejected/superseded | SSE | 触发 H 刷新 | 相应批次事件非空 | 精确批次 | 通知 | 是 |

Kernel HTTP 请求/响应定义与路由见 [schemas.py](../../../source/kernel/src/platform_kernel/api/schemas.py)、[Session commands](../../../source/kernel/src/platform_kernel/api/routes/session_commands.py)、[Session queries](../../../source/kernel/src/platform_kernel/api/routes/session_queries.py)、[Workflow routes](../../../source/kernel/src/platform_kernel/api/routes/workflow_runs.py)。UI 目标能力缺口见 [CONTRACT_GAPS.md](CONTRACT_GAPS.md)。
