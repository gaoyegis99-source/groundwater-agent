# UI-02 Selector 设计

## 类型证据与输入

| 已核实的输入 | 类型 | 字段依据 |
|---|---|---|
| 会话目录/元数据 | 生成 `kernel__SessionSummary` 的 `sessionId, archived` 子集 | Kernel `SessionSummary` / `list_sessions` |
| 当前快照及已并入的历史页 | `History`、`Turn`、`TaskContext`、`Activity`、`Confirmation`、`ExecutionTask` | `message_history`、`session_run_summaries`、`derive_task_progress`、`session_execution_tasks` |
| 停止请求 | `History.controlRequests` | `run_controls` 的 REQUESTED/PROCESSING/COMPLETE；修正手写 union |
| 成果引用 | `Result`、`ResourceReference` | `list_session_results` 和 `DataVersionRefV2` |
| 独立状态 | 生成 `kernel__WorkflowNodeDiagnostic`、`data__PublicationResponse`；`WorkflowDraft` | Kernel 节点代理、Data publication、Kernel Draft |

源码位置和可空性见 [合同矩阵](CONTRACT_MATRIX.md)。输入可选表示调用方尚未取得事实；快照内 `task: null` 表示已确认无主任务。`undefined` 表示未知。直接使用历史 Confirmation，Session 详情摘要缺 decision/decisionState，不能据此给审批操作。公开 HTTP 存在开放嵌套对象，调用方仍需做边界校验。为接收已核实的快照，补齐 Task criteria evidence 字段和 Confirmation/Run 关联字段的可空性。生成的严格节点/Data 枚举用于编译期穷举，运行时继续保留未知值回退。

## 纯函数接口

- `selectSessionUi({sessionId, metadata?, history?, results?, verifiedOutputs?, freshness, canWrite?})` 从同一 Session 快照返回会话元数据徽标、独立快照可信度、主 Task 状态和轮次列表。results 是调用方已按 project/owner/Session 隔离的成果读取状态（not-requested/loading/loaded/failed）。只有 loaded 携带已读完分页的 R 详情，允许空列表。调用方负责维护当前请求与访问范围。Selector 会再次按 workflowRunId、agentRunId、toolCallId、goalRevision 核对结果。只有匹配当前 Session 的 metadata/history 才可读取。未加载的目录行无 Task/Turn 聚合。
- `selectWorkflowNodeUi`、`selectResourcePublicationUi`、`selectWorkflowDraftUi` 投影单个已取得并由调用方核对身份的 DTO。图节点可用性和节点状态分别映射。Data 状态函数可用于已知 publicationId 的事实；目前无可靠 Run→publicationId 公开关联。科学验证显示未提供。
- 返回 `UiStatus` 包含原值、中文标签、tone、说明、操作候选。停止作为独立的 Turn 控制投影，Task lifecycle/state、Run 执行、成果、科学验证各自独立。Run 标记按每次尝试保存，透传精确 Run ID；DAG 读取按此 ID，业务 Task Steps 由 TaskContext 保留。
- `freshness` 由快照/连接/回执核对层传入。待核对时仍可显示上次快照和只读入口，状态说明明确陈旧；不提供业务写入候选。角色和命令中的权限校验仍属于调用方和服务端。Selector 的操作是条件候选，提交中、防重放、当前网络、编辑表单和服务端冲突仍由原交互链处理。恢复候选只表示可检查，服务端决定是否生成新确认。
- Task state 直接读取服务端投影，只有 lifecycle/state 都为 COMPLETE 且现有 criteria/steps 全部有匹配的服务端通过记录时才展示完成。数据矛盾回退待核实。Task 更新、流程推进、停止和发布命令的副作用保持原链路。Selector 不记录历史、不消耗 SSE、不保存时钟、不估计百分比、不触发网络请求。
- 未知值统一灰色“状态待核实”，无业务变更操作。缺失且明确不适用的输入独立显示。WorkflowRun 成功且预期有成果时，按实际读取状态展示未请求、读取中、成果待同步或读取失败；成果待同步要求 R 已读完且缺完整匹配引用。当前 Executor 成功发生于 Data 提交之后，故测试的“未发布”场景分别覆盖真实发布中、成功后浏览器未收到引用。调用方把当前访问范围内经 Data 精确版本/对象/语义核对成功的引用传为 verifiedOutputs。outputAccess 逐项开放预览和下载；仅所有引用均核验后才提供整批操作候选。内容端点仍独立授权，可能返回错误。科学状态独立。

[状态枚举与展示](STATE_MAPPING.md) · [缺口与后续条件](CONTRACT_GAPS.md)
