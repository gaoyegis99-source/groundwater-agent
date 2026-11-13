# UI-09 WorkflowRun 时间线与节点

基线 `f0b9f6d`。工作区延续 UI-08 的未提交实现。

- 会话运行列表读取消息快照中已持久化的运行身份。运行与其轮次以 AgentRun 和 goalRevision 核对。关联确认用确认 ID、同一 AgentRun 和 goalRevision，冻结 action 进一步以 toolCallId、模型及工作流版本匹配。
- 同一批次保留尝试编号、实际耗时、重试来源和服务端错误。使用 UI-02 状态投影呈现运行、发布及通信情况。成果通过同一 Run ID、AgentRun、toolCallId、goalRevision 和精确输出引用匹配，预览继续使用 Data 身份校验。
- 节点通过原有 `GET /workflow-runs/{runId}/nodes` 和 `/graph` 获取，沿用 UI-02 状态映射，缺少状态时回退到待核实。Task Steps 和 AgentPlan 继续使用独立区块。历史尝试的逐节点日志和逐尝试成果仍受 G05/G13 限制。
- 修改 `WorkspacePanel.tsx`、`WorkflowNodeDetails.tsx`、`ui-selectors.ts`、`App.tsx`、`workbench-polish.css`、`WorkspacePanel.test.tsx` 与进度记录。新增测试先复现运行未显示确认和对应成果，定向 Web 50 项通过。
- 锁定 Python 运行 Kernel Workflow Progress、Session History 与 Kernel/Executor Schema，22 项通过。
- `../bin/dev check --scope web` 退出 0，371 项通过，类型、生产构建、Ruff 和 whitespace 通过。生产构建 Playwright 退出 0，30 项通过，包括失败与恢复尝试的节点和成果隔离、刷新与重连。浏览器使用合同测试响应。

## 授权后的运行详情复核

基线仍为 `f0b9f6d`，保留原工作区修改。运行卡新增折叠的运行 ID、模型与工作流版本、确认 ID 和服务端时间。耗时显示服务端毫秒值，保留零值。未提供尝试编号时显示缺失，避免把旧快照默认为第一次运行。节点读取按项目和运行 ID 隔离，切换时显示读取状态。组件回归先复现切换后旧节点仍显示，修复后通过。

修改 `WorkspacePanel.tsx`、`WorkflowNodeDetails.tsx`、`WorkflowNodeDetails.test.tsx`、`build-workflow-tasks.spec.ts` 和进度记录。Web 388 项、Kernel/Executor 22 项、生产 Chromium 33 项通过，类型检查随构建通过，`git diff --check` 通过。桌面与 390px 浏览器覆盖原失败尝试、恢复后的独立节点和成果、身份、耗时及刷新。浏览器使用合同兼容响应，本轮未重跑真实跨服务门禁。截图与 Playwright JSON 位于本聊天的 `ui-09` 目录。G05/G13 的历史逐节点尝试与历史成果缺口继续保留。

节点依赖图补充核对图节点和诊断节点的一一对应。图不可用、属于其他运行、节点集合不一致或含重复 ID 时，保留诊断列表和错误码。重复 ID 回归在修复前复现遗漏失败节点，修复后通过。Web 391 项、TypeScript 和生产构建的运行恢复定向用例通过。本次补充未重跑隔离门禁。

随后扩展 `v12-interaction-recovery.spec.ts` 并完成隔离 Interaction 门禁，退出 0，6 项全部通过。真实 Executor 超时后，经新确认恢复运行。刷新后同时展开两个批次，原批次显示 `JOB_DEADLINE_EXCEEDED`，恢复批次显示节点完成。浏览器核对恢复批次的运行 ID、确认 ID 与创建时间，并验证 390×844 页面无横向溢出。测试通过真实 Kernel 节点及依赖图接口读取，使用固定本地测试 Provider。门禁 JSON、节点证据和桌面、窄屏截图位于本聊天的 `ui-09-isolated` 目录。独立 E2E TypeScript 检查和 `git diff --check` 通过。
