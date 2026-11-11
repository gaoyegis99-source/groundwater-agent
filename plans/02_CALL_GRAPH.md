# 02 模块调用链与状态所有权

## 1. 用户对话到执行结果

源码架构文档 `source/docs/architecture/architecture-structure-and-flow.md` 定义的主要路径：

1. Web 通过 `POST /api/v12/sessions/{sessionId}/messages` 提交消息。
2. Kernel Intent Router 判定普通对话、执行提案、Workflow Draft 或澄清。
3. Agent 使用 Data / Knowledge observation tools 读取本轮证据。
4. 执行提案先调用 `prepare_task_inputs`。
5. 再 `record_plan`，形成持久化 AgentAction。
6. Kernel 生成 Confirmation。
7. 用户确认后 Kernel 调用 Executor。
8. Executor 从静态 DAG 调度节点与 Worker。
9. Worker 输出 checksum-addressed artifacts。
10. Data 原子发布 output group 与 lineage。
11. Kernel 投影 AgentFinal / Session Event。
12. Web 通过 SSE + snapshot 展示进度、任务和结果。

**重要：** Agent 文本不能建立执行事实；Confirmation 是执行授权边界；Executor 不接受 Agent 临时生成的动态 DAG。

## 2. 状态所有权

| 业务状态 | 所有者 | Web 应如何使用 |
|---|---|---|
| Session / Turn / AgentRun | Kernel | 读取 Session snapshot 与 SSE |
| Confirmation | Kernel | 展示当前有效确认并提交 confirm/reject/revise |
| TaskContext / task progress | Kernel | 通过 task projection 展示目标、缺口和成果 |
| WorkflowRun / Job / node attempt | Executor，经 Kernel 投影 | 显示持久状态，不从模型回复猜测 |
| Dataset / DatasetVersion / SemanticRevision | Data | 使用精确 version identity |
| Preview / Query / Publication | Data | 使用 operation/publication id 查询权威状态 |
| Knowledge version / unit / retrieval receipt | Knowledge Next | 引用必须完整 read 且状态 OK |
| Operator definition / workflow definition | 静态 catalog/domain package + Assembler | Agent 只使用已安装目录 |
| Product UI / model display metadata | Product package | Web 不另建业务真相 |

## 3. 前端主要状态流

### `use-session-stream.ts`
- 持有 history、workflowDrafts、results、preview、connection、trace。
- SSE 收到事件后更新 progress dirty / details dirty。
- 通过 `scheduleRefresh` 合并消息刷新。
- 通过 `loadDetails` 读取 workflow draft 与 results。
- 重连时 force snapshot refresh。
优化不能删除 snapshot reconciliation，也不能只依赖 SSE 事件而不恢复服务端状态。

### `WorkflowDraftCard.tsx`
只负责渲染 WorkflowDraft projection。不要在组件内直接调用 Executor。
若增加“补齐草稿”，新增独立编辑状态和明确的保存/导出动作，仍需遵守 `execution_allowed=false`。

### `ConfirmationEditor.tsx`
从 Confirmation.actions 复制初始参数，按 model inputSchema 渲染字段。资源参数使用 Data resource list。修改保存形成新的不可变修订。
字段校验应依赖真实 schema / descriptor / Preflight，不应仅在 UI 里做“看似通过”的检查。

### `SpatialViewers.tsx`
当前 GeoJSON 和 GeoTIFF 使用 OpenLayers。优化优先稳定 Map/Source/Layer 生命周期，避免在 render/style state change 时重建数据源。

### `ChartView.tsx`
调用 Data Query API，读取最多 1000 行，再构造 ECharts series。必须明确查询截断、排序和字段单位。

## 4. 后端关键入口

### Agent preparation
`kernel/src/platform_kernel/agent_runtime/preparation_tool.py`
- 解析 target_tool / arguments / selection_policy。
- 使用模型 schema 验证参数。
- 对每个 data_input_parameter 调用 inspect_data_version。
- 检查 exact version identity / current version policy。
- 记录 descriptor.fields 和 semantics.fields 是否存在。
- 保存 Preparation snapshot。

后续字段级检查应该扩展该流程或在其附近新增纯函数，避免把同一校验复制到 prompt、UI 和 Executor。

### Reply submission
`kernel/src/platform_kernel/agent_runtime/tooling/reply_submission.py`
- `submit_reply` 是终态回复边界。
- Workflow Draft 进入 `compile_draft` 做纯编译校验。
- 失败可进行有限 protocol repair。
- `_sequential_draft_issue` 是额外业务规则，目前以消息正则和 sink 数量判断线性顺序。

### Workflow Compiler
`packages/workflow-compiler/src/platform_workflow_compiler/graph.py`
- Workflow v6 schema
- exact port binding
- cardinality one
- artifact-only nodeOutput
- DAG cycle detection
- parameter schema validation
- output validation

### Executor
`services/executor/`
- admission
- job persistence
- node scheduling
- worker lease
- retry/recovery
- result finalization/publication

不能从 UI 新增一条“直接跑草稿”的旁路。

## 5. 修改调用链的顺序

每次改动至少写清楚：
- 用户动作从哪个组件发出
- 调用哪个 API
- 后端哪个路由接收
- 哪个 application/service function 处理
- 持久化到哪个状态对象
- 事件如何到 Web
- 结果如何打开或复用

缺任意一环，不得把功能标记为完成。
