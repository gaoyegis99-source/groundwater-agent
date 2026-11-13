# UI-05B Task 详情与科研交付条件管理

基线 `f0b9f6d6d5e1df970794d2cd16b9cdcec387b7fc`。从包含 UI-01 至 UI-05A 的未提交工作区继续。

## 合同与实现

- 任务详情读取 `GET /api/v12/sessions/{sessionId}/messages` 同事务的 TaskContext 和消息快照。独立 `GET …/task` 使用相同的 TaskContext 投影；现有 Session 快照的作用域、事件水位和恢复路径继续生效。当前目标使用 `spec.objective`，范围使用已公开的约束，规格使用 `spec.revision`，缺项使用 `spec.unresolvedItems`。
- 交付条件复用 UI-02 Selector，按 `progress.criteria[].satisfied` 与同 ID 的证据形状投影。未知条件类型、缺少进度记录和满足标记无对应证据均显示「待核实」。有明确否定记录的已知类型显示「待完成」。任务完成还要求服务端 lifecycle/state 同为 COMPLETE、非空 criteria 和已声明步骤的完成记录。浏览器只显示服务端事实和引用。
- 证据字段来自 `tasks/persistence.py::_apply_evidence` 和 `tasks/progress.py::_criterion_progress`。答复和草稿以明确 AgentRun 和草稿 ID 关联已加载对话。用户评审显示原文，并用 `reviewGoalRevision` 关联来源轮次。成果按 WorkflowRun、AgentRun、模型、要求的输出名称和不可变版本身份匹配，打开时复用现有 Data 身份核验及成果查看器。交付条件、Task 完成和通用科学验证各用各自的合同。
- 业务步骤来自规格与 `progress.steps`，展示步骤状态、依赖、条件和已声明缺口。任务级阻塞原因取自 `progress.blockingReasons`。现有 AgentPlan、会话执行记录和 Executor 节点保持独立。
- 「在对话中提出修改」回到普通消息输入框并保留已有草稿。显式用户评审沿用服务端识别的 `确认完成：<条件原文>` 普通消息格式。请求成功后的完成状态由最新服务端快照决定。
- 工作台按需打开，沿用 UI-05A Task Header。详情使用设计系统的边框、状态色、证据折叠和窄屏单列。

## 修改文件

| 文件 | 变化 |
|---|---|
| `source/web/src/TaskCard.tsx`、`task-overview.css` | 任务详情、交付证据、成果、步骤与响应式样式 |
| `source/web/src/WorkspacePanel.tsx`、`App.tsx` | 对话来源跳转、普通消息评审与草稿、会话隔离 |
| `source/web/src/ui-selectors.ts`、`types.ts` | 条件和步骤投影、精确成果匹配、已公开证据类型 |
| `source/web/src/task-overview.test.tsx`、`source/web/e2e/build-task-overview.spec.ts` | 状态、证据、关联、恢复及浏览器评审回归 |
| `plans/ui-interaction-spec/contracts/CONTRACT_GAPS.md`、`progress/PROGRESS.md`、本报告 | 缺口、进度和验证记录 |

## 剩余合同缺口

[G02、G03、G08、G13–G15](../contracts/CONTRACT_GAPS.md) 记录独立目标或范围与版本浏览、精确 Task↔Turn 归属、科学验证结论、历史尝试成果，以及逐步骤失败和逐条件证据明细。业务步骤的 `complete: false` 仅表示未完成。现阶段的任务级阻塞和会话执行失败均以各自服务端字段展示。后端合同、持久化与执行服务本轮无改动。

## 验证

- 从 `source/` 执行 `../bin/dev check --scope web`，退出 0。38 个文件、367 项通过，TypeScript、生产构建、Ruff 和 diff 检查通过。任务测试验证未知类型、证据缺失、步骤缺口和阻塞、完成判定、会话切换与迟到快照。
- 用仓库锁定的 CPython 3.12.8 执行 `python -m pytest -q kernel/tests/test_tasks.py kernel/tests/test_session_history.py`（独立临时目录），退出 0，43 项通过。
- `npm --prefix source/web run test:e2e:build` 在 Chromium 生产构建最终串行运行退出 0，29 项通过。此前与 Web 门禁并行运行时，已有首页 1440px 用例等待页面文案超时（28 通过、1 失败）。最终截图由串行复跑产生，包括桌面 1440×900 和窄屏 390×844。
- Chromium 用例验证条件证据跳转、精确成果读取、已有草稿保留、同 Session 普通消息提交、服务端评审与完成、刷新恢复和 390px 无横向溢出。浏览器使用合同兼容的 HTTP 与事件测试响应。真实跨服务 E2E 和 Provider 未运行。

## 授权后的评审恢复验收

当前工作区已有交付条件、证据回溯、业务步骤和评审提交。复核发现，任务详情发送评审消息后如果收到 503，重试入口仅在对话页，任务详情内的评审按钮则因存在待核对请求而停用。`App.tsx` 将已有消息恢复入口也展示在工作台，复用会话已保存的请求，按现有项目身份控制操作权限。`build-task-overview.spec.ts` 验证失败后刷新、从任务页重试、原请求标识和消息体一致、评审完成由快照恢复，以及原草稿保留。回归在修复前失败，修复后通过。

- Web 375 项（38 个文件）、Kernel Task 与会话历史 43 项、生产 Chromium 33 项均通过，类型检查随生产构建通过，`git diff --check` 通过。
- 503 和重试使用合同兼容的浏览器响应。本轮未重跑真实跨服务 E2E 或外部 Provider。此前工作区的隔离浏览器验收已验证真实 Task 评审与归档后恢复，见 [UI-05A 隔离验收](UI-05A_TASK_OVERVIEW.md#完成证据复核)。
- 桌面 1440×900 和移动端 390×844 截图、Playwright JSON 保存在本聊天的 `ui-05b` 目录。

## 隔离服务补充验收

`v12-live.spec.ts` 在首个评审 POST 到达 Gateway 前模拟网络中断。刷新后任务仍在等待评审，用户可从任务详情重试。测试核对重试的请求标识和消息体，确认 Kernel 只持久化一条评审消息和一个对应轮次。归档刷新后恢复同一已完成任务、评审证据与成果引用。

隔离 Compose WebUI 门禁退出 0，20 项通过，零失败、跳过或 flaky。运行使用真实 Gateway、Kernel、Executor、Data、Knowledge 和固定本地测试 Provider。独立 TypeScript 检查和 `git diff --check` 通过。日志、Playwright JSON 和重试截图保存在本聊天的 `ui-05b-isolated` 目录。
