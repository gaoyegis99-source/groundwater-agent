# UI-02 权威状态到界面映射

颜色为设计语义，文本同时表达状态。灰 = neutral，蓝 = info，绿 = success，琥珀 = warning，红 = danger。操作列列出状态可提供的入口；写入候选仅在快照已核对且用户有写权限时提供。归档/重命名根据真实目录元数据。停止、批准、恢复还要求当前 Turn/批次等条件。调用方继续核对请求与权限。[Selector 设计](UI_SELECTOR_DESIGN.md)

每张表共用注明的来源。未知枚举统一「状态待核实」/灰/「当前数据无法确认此状态」，仅保留适用的查看或刷新。缺失字段按表显示相应未提供态；网络或回执不确定时保留最近快照和独立的「状态待核对」/琥珀/刷新，暂停写入候选。

## Session

来源为 Kernel SessionPage/SessionMetadataResponse 的 `archived`（由 `archived_at` 派生），以及当前会话 H 快照身份。Session 无通用业务状态枚举。会话详情的 Task、Turn、停止和确认标签各取对应事实；目录无跨会话聚合。active/recent/attention/review 等验收场景词仅用于说明，不作为服务端 Session 状态。

| 原始值 | UI 名称 | 色 | 用户说明 | 操作 |
|---|---|---|---|---|
| archived=false | 未归档 | 蓝 | 会话位于常用目录 | 打开、重命名、归档 |
| archived=true | 已归档 | 灰 | 会话位于归档目录 | 打开、重命名、取消归档 |
| 无匹配目录元数据 | 归档状态未提供 | 灰 | 尚未取得此会话的目录元数据 | 打开 |
| 快照未取得/待核对 | 状态待核对 | 琥珀 | 请刷新以核对当前会话快照 | 刷新 |

## Task lifecycle 与 state

来源为 Kernel H/`GET …/task` 中 `TaskContextV1.progress`。Session D 的摘要不能独立核实完成条件。生命周期、状态、criteria、steps 和证据全部来自服务端。Task 已完成仅显示在 COMPLETE 双字段且条件及步骤通过时；Turn COMPLETE 不升级 Task。

| 原始字段 / 值 | UI 名称 | 色 | 用户说明 | 操作 |
|---|---|---|---|---|
| task=null | 无主任务 | 灰 | 当前会话尚未建立主任务 | 无任务操作，可走会话输入 |
| task 缺快照 | 主任务待核对 | 灰 | 尚未取得主任务快照 | 刷新 |
| lifecycle=ACTIVE | 任务未完成 | 蓝 | 科研交付目标仍在跟进 | 查看 |
| lifecycle=COMPLETE | 任务已完成 | 绿 | 当前任务修订已完成 | 查看 |
| state=ACTIVE | 任务进行中 | 蓝 | 仍有交付条件待满足 | 查看 |
| state=ANALYZING | 正在分析 | 蓝 | 服务端正在处理当前研究请求 | 查看 |
| state=WAITING_INPUT | 等待补充信息 | 琥珀 | 请查看任务缺项或准备检查 | 查看、补充输入 |
| state=WAITING_CONFIRMATION | 等待执行确认 | 琥珀 | 请核对当前冻结批次 | 查看；审批动作由 Confirmation 决定 |
| state=EXECUTING | 正在执行 | 蓝 | 任务正在等待已批准的计算 | 查看；停止入口由当前 Turn 决定 |
| state=BLOCKED | 任务受阻 | 红 | 请查看服务端记录的阻塞原因 | 查看 |
| state=WAITING_REVIEW | 等待业务评审 | 琥珀 | 有待用户评审的交付条件 | 查看、评审 |
| state=COMPLETE | 任务条件已满足 | 绿 | 当前任务的交付条件和业务步骤已完成 | 查看 |

Kernel 由当前运行、待确认、失败、准备缺项、用户评审和验收事实派生 state。已持久完成记录按 Task revision 生效，规格修订可以重开。没有 PREPARING、PLANNING 或 PUBLISHING 的 Task state；准备/发布取各自域事实。

## Turn、停止与 Agent Activity

Turn 源为 H `turns[].status`，通常为 `agent_runs.status`。ROUTING/QUEUED 也来自已持久的 message request/启动指针。澄清由 `intentDecision=NEEDS_CLARIFICATION` 等路由字段单独标注；业务补充使用 Task WAITING_INPUT 或已核验的回复。Turn 无 WAITING_INPUT/STOP_REQUESTED 枚举。浏览器 Draft/Queued/Receipt uncertain 不接入服务端 Turn 枚举。

| Turn 原始值 | UI 名称 | 色 | 用户说明 | 操作 |
|---|---|---|---|---|
| ROUTING | 正在判断需求 | 蓝 | 服务端正在识别请求意图 | 查看、请求停止 |
| QUEUED | 本轮等待处理 | 灰 | 请求已受理，等待启动 | 查看、请求停止 |
| RUNNING | 本轮处理中 | 蓝 | 本轮协作仍在进行 | 查看、请求停止 |
| WAITING_CONFIRMATION | 本轮等待确认 | 琥珀 | 本轮等待当前执行决定 | 查看、请求停止 |
| WAITING_EXECUTION | 本轮等待计算 | 蓝 | 本轮等待已确认的计算 | 查看、请求停止 |
| COMPLETE | 本轮已结束 | 绿 | 服务端已结束本轮协作 | 查看 |
| FAILED | 本轮未完成 | 红 | 请查看本轮失败信息 | 查看 |
| CANCELLED | 本轮已取消 | 灰 | 服务端已记录本轮取消 | 查看 |
| SUPERSEDED | 本轮已被替代 | 灰 | 旧轮次保留供查阅 | 查看 |

停止的源为 H `controlRequests[].status`，按 agentRunId 精确匹配，优先显示最近尚未结束的请求。Run/Turn 保持自身终态；`COMPLETE` 表示处理请求结束。

| 停止原始值 | UI 名称 | 色 | 用户说明 | 操作 |
|---|---|---|---|---|
| REQUESTED | 已请求停止 | 琥珀 | 停止请求已受理，等待服务端处理 | 刷新 |
| PROCESSING | 正在处理停止 | 琥珀 | 等待服务端核对最终运行状态 | 刷新 |
| COMPLETE | 停止请求处理结束 | 灰 | 请查看本轮和执行记录的最终状态 | 刷新 |

Activity 源为 H `turns[].activities[].status`，Kernel 折叠 `agent.activity` 持久事件。同一活动按 agentRunId/activityId 识别。摘要用公开 title/summary；诊断或模型请求计数只在适当入口呈现。公开业务里程碑为另一个投影 `progressUpdates`（RUNNING/COMPLETE/FAILED/INTERRUPTED），读取自身 title/summary/status，不作为 Activity 或 Run 的替代。

| Activity 原始值 | UI 名称 | 色 | 用户说明 | 操作 |
|---|---|---|---|---|
| QUEUED | 活动等待中 | 灰 | 活动等待开始 | 查看 |
| RUNNING | 活动进行中 | 蓝 | 活动正在进行 | 查看 |
| COMPLETE | 活动已完成 | 绿 | 该活动已结束 | 查看 |
| FAILED | 活动失败 | 红 | 该活动未能完成 | 查看 |
| INTERRUPTED | 活动已中断 | 灰 | 该活动已停止 | 查看 |

## Confirmation 与 Workflow Draft

Confirmation 源为 H `confirmations[].status/decision/decisionState`，历史与当前批次均保留。待确认操作仅在当前最新批次、无处理中的决定和停止、满足既有 `canConfirm` 时提供。恢复批次有 sourceWorkflowRunId，可在原 Turn 终态时等待新审批。kind=INITIAL/EDITED/RECOVERY 表示种类；decision=CONFIRM/REJECT/SUPERSEDE 表示命令，处理阶段看 decisionState。

| Confirmation 原始值 | UI 名称 | 色 | 用户说明 | 操作 |
|---|---|---|---|---|
| status=PENDING | 待确认 | 琥珀 | 冻结批次等待决定 | 查看；符合条件时编辑、确认、拒绝 |
| status=CONFIRMED | 已确认 | 绿 | 此批次已获得批准 | 查看 |
| status=REJECTED | 已拒绝 | 红 | 此批次的执行请求已被拒绝 | 查看 |
| status=SUPERSEDED | 已被替代 | 灰 | 旧批次保留供查阅 | 查看 |
| decisionState=REQUESTED | 决定已受理 | 蓝 | 等待服务端处理决定 | 刷新 |
| decisionState=RUNNING | 正在处理决定 | 蓝 | 服务端正在处理此决定 | 刷新 |
| decisionState=null | 无额外处理徽标 | — | 保留批次状态 | 取决于批次状态 |

Draft 源为 Kernel WorkflowDraftResponse.status。草稿 payload 固定 executionAllowed=false。生成完成可查看并导出草稿。

| Draft 原始值 | UI 名称 | 色 | 用户说明 | 操作 |
|---|---|---|---|---|
| GENERATING | 正在生成草稿 | 蓝 | 服务端正在生成方案草稿 | 查看 |
| COMPLETE，含 payload | 草稿已生成 | 绿 | 草稿可供查看和导出 | 查看、有效 payload 可导出 |
| COMPLETE，payload 为空 | 未生成可用草稿 | 灰 | 本轮编排已结束，说明在本轮回复中 | 查看 |
| FAILED | 草稿生成失败 | 红 | 请查看草稿诊断 | 查看 |
| CANCELLED | 草稿已取消 | 灰 | 服务端已取消草稿生成 | 查看 |
| SUPERSEDED | 草稿已被替代 | 灰 | 旧草稿保留供查阅 | 查看 |

## WorkflowRun

来源为 Kernel E/W `workflow_runs.status`，由 Executor JobStatusV6 与 Kernel 派发事实写入。E.stage 从 stage 事件投影。R 的 action COMPLETE/FAILED 不用于映射执行终态。恢复候选要求当前终态 Turn、最近失败/超时/取消尝试、存在 Confirmation、无前项失败阻塞、无待确认和未处理停止；最终资格由服务端校验。停止是 AgentRun 命令，Run 无公开取消请求状态。

| 原始字段 / 值 | UI 名称 | 色 | 用户说明 | 操作 |
|---|---|---|---|---|
| status=QUEUED | 等待执行 | 灰 | 运行已入队 | 查看 |
| status=RUNNING | 正在执行 | 蓝 | 运行仍在执行链路中 | 查看 |
| status=SUCCEEDED | 执行成功 | 绿 | 此执行尝试已成功结束 | 查看；独立成果入口见下 |
| status=FAILED | 执行失败 | 红 | 此尝试失败，请查看详情 | 查看、合格时检查恢复 |
| status=TIMED_OUT | 执行超时 | 红 | 此尝试达到执行时限 | 查看、合格时检查恢复 |
| status=CANCELLED | 执行已取消 | 灰 | 服务端已记录该尝试取消 | 查看、合格时检查恢复 |
| stage=QUEUED | 等待派发 | 灰 | 运行等待派发或开始执行 | 查看运行 |
| stage=EXECUTING | 正在运行节点 | 蓝 | Executor 正在执行工作流节点 | 查看运行 |
| stage=PUBLISHING | 正在发布成果 | 蓝 | Executor 正在完成发布链路 | 查看运行 |
| stage=null | 无阶段徽标 | — | 只显示 Run 状态 | 查看运行 |
| communicationError 有值 | 执行状态待核对 | 琥珀 | 与执行服务通信异常，显示最近记录 | 刷新 |

## Workflow Node

来源为 Kernel `GET …/workflow-runs/{id}/nodes`，代理 Executor 持久 `job_nodes`。图来自 `/graph` 的 dependsOn，节点状态以节点响应为准。节点尝试的 LEASED/RUNNING/SUCCEEDED/FAILED/LOST/CANCELLED 仅内部逐项持久化，公开只给 attemptCount，禁止以计数猜测 LOST。Task Steps 的 complete 来自 Task 条件和业务依赖，与 Executor DAG 节点状态分开。

| 原始字段 / 值 | UI 名称 | 色 | 用户说明 | 操作 |
|---|---|---|---|---|
| availability=NOT_DISPATCHED | 尚未派发 | 灰 | 尚无 Executor Job 节点事实 | 刷新 |
| availability=UNAVAILABLE | 节点详情暂不可用 | 琥珀 | 暂时无法取得节点事实 | 刷新 |
| availability=AVAILABLE | 节点详情可用 | 蓝 | 可以查看当前 Job 的节点事实 | 刷新（节点状态另有查看入口） |
| status=WAITING | 等待依赖 | 灰 | 节点等待上游依赖 | 查看 |
| status=READY | 等待调度 | 灰 | 节点已具备调度条件 | 查看 |
| status=LEASED | 已分配 | 蓝 | 节点已取得 Worker 租约 | 查看 |
| status=RUNNING | 节点运行中 | 蓝 | Worker 正在执行此节点 | 查看 |
| status=SUCCEEDED | 节点执行成功 | 绿 | 此节点执行成功 | 查看 |
| status=FAILED | 节点执行失败 | 红 | 请查看节点错误 | 查看 |
| status=CANCELLED | 节点已取消 | 灰 | 服务端已记录节点取消 | 查看 |
| status=SKIPPED | 节点未执行 | 灰 | 此节点被跳过 | 查看 |

## Resource Publication

Data 状态只来自已核对 publicationId 的 `PublicationResponse.state`。以下操作为状态查看；发布详情本身无 ResourceReference 时，预览/下载由精确版本入口提供。

| Data 原始值 | UI 名称 | 色 | 用户说明 | 操作 |
|---|---|---|---|---|
| OPEN | 发布已创建 | 灰 | 等待提交发布资料 | 查看、刷新 |
| PREPARING | 准备发布 | 蓝 | Data 正在准备提交 | 查看、刷新 |
| READY | 待提交发布 | 琥珀 | Data 已完成发布准备 | 查看、刷新 |
| PUBLISHED | 已发布 | 绿 | Data 已提交此发布批次 | 查看、刷新 |
| FAILED | 发布失败 | 红 | 请查看 Data 发布详情 | 查看、刷新 |
| ABORTED | 发布已终止 | 灰 | Data 已终止此发布批次 | 查看、刷新 |

Run 当前没有公开 publicationId。如下是基于公开 E/R 的**成果展示**，独立于 Data 枚举。读取状态行的 raw 来自调用方读取状态，其余成果展示的 raw 为 null。当前 Executor 先提交成果再记执行成功；若 H 先更新，R 后到，展示当前读取状态。真正发布尚在进行的场景取 RUNNING/PUBLISHING。历史 attempt 的 R 行可能不可得，可直接用 W 查原运行输出。

| 已取得的事实 | UI 名称 | 色 | 用户说明 | 操作 |
|---|---|---|---|---|
| E RUNNING + PUBLISHING | 正在发布成果 | 蓝 | Executor 正在完成发布链路 | 刷新 |
| E SUCCEEDED、有正 outputCount、R 未请求 | 未请求成果 | 灰 | 尚未读取此会话的成果列表 | 刷新 |
| E SUCCEEDED、有正 outputCount、R 读取中 | 正在读取成果 | 蓝 | 正在读取此会话的成果列表 | 无 |
| E SUCCEEDED、有正 outputCount、R 已加载但缺精确匹配/完整引用 | 成果待同步 | 琥珀 | 已读取成果列表，尚未取得与此运行匹配的完整引用 | 刷新 |
| E SUCCEEDED、有正 outputCount、R 读取失败 | 成果读取失败 | 红 | 暂时无法读取成果列表，可重新查询 | 刷新 |
| E SUCCEEDED、匹配 R action COMPLETE、引用有效且数量相符 | 已取得发布成果 | 绿 | 已同步此执行的精确成果引用 | 精确引用经 Data 核验后逐项预览、下载 |
| 无以上证据，含 outputCount=0 | 发布状态未提供 | 灰 | 当前没有可核对的 Data 发布状态 | 无 |

## Scientific Validation

| 原始字段 / 值 | UI 名称 | 色 | 用户说明 | 操作 | 来源 |
|---|---|---|---|---|---|
| 无通用公开字段 | 科学验证状态未提供 | 灰 | 当前运行未提供通用科学验证结论 | 无验证动作 | 当前 W/E/R 和 Data 合同无 Run 级判定 |
| 将来收到当前版本未知值 | 状态待核实 | 灰 | 当前数据无法确认此状态 | 只读 | 等待正式合同 |

Task criteria、准备 PASS/UNKNOWN/FAIL、运行成功、发布回执、模型特定诊断都保持各自证据语义。Data 的 QualityRunResponse.status（PASSED/PASSED_WITH_WARNINGS/FAILED/ERROR）用于特定数据版本与规则套件的表格质量判断。科学验证通过的展示须有明确标准、判定及可追溯证据。[G08](CONTRACT_GAPS.md)
