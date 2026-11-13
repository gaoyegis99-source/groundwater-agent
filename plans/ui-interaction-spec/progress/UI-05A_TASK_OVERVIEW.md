# UI-05A Task 生命周期与研究任务概览

基线 `f0b9f6d6d5e1df970794d2cd16b9cdcec387b7fc`。本轮从包含 UI-01 至 UI-04 的未提交工作区继续。

## 合同与实现

- Task Header 读取当前 Session 的 `History.task`。目录的 Session 标题作为研究名称显示，目录无标题时使用「研究任务」。服务端 TaskSpecV1 没有独立名称或当前目标字段。标题区使用 `spec.objective`，概览将 Kernel 保持不变的同一个值标为「原始研究目标」。无主 Task 时省略标题区。
- 标题区保留「查看任务概览」。当前快照允许补充信息且对话可写时，「补充任务信息」聚焦普通消息输入框并保留草稿。等待确认且当前会话快照有明确待确认批次时，「查看当前执行确认」聚焦现有确认卡片。入口仅导航到该会话已有操作，G03 仍适用。
- Kernel 的 `GET …/messages` 同事务返回 TaskContext、当前消息快照和事件水位，`GET …/task` 也返回当前规格。本轮沿用同事务路径，避免独立 Task 请求引入竞态。Task 归属核对 Session ID 与已提供的 spec.sessionId。刷新、后续消息以及任务事件继续走 UI-04 的水位合并和 Session/项目请求隔离。
- Task 标题区和工作台共用 UI-02 Selector。完成要求 lifecycle/state 同为 COMPLETE，存在非空 criteria，每项有匹配的已满足记录与 Kernel 证据引用，声明步骤均有完成记录。按 criterion kind 核对服务端已知证据字段，不在浏览器重新执行验收。空业务步骤合法。
- 概览复用 TaskCard，显示现行规格版本、原始目标、已有约束、完成条件、缺项、阻塞原因和业务步骤。步骤关联使用 `dependsOn` 和 `criterionIds`。AgentPlan 和 Executor 节点继续位于已有独立区块。任务评审保持普通消息提交，并核对当前访问和快照状态。
- 桌面与窄屏沿用现有按需工作台路由。浅蓝标题区与状态徽标取自设计系统，保留对话为主的布局。

## 文件

- 界面与投影：`source/web/src/App.tsx`、`TaskCard.tsx`、`WorkspacePanel.tsx`、`ui-selectors.ts`、`task-overview.css`。本次复核复用了已有的 WorkspacePanel 和 Selector。
- 验证：`source/web/src/task-overview.test.tsx`、`WorkspacePanel.test.tsx`、`source/web/e2e/build-task-overview.spec.ts`、`source/web/playwright.build.config.ts`。
- 记录：本报告和 [PROGRESS.md](PROGRESS.md)。

## 未解决问题

任务名称沿用 Session 标题。独立当前目标、完整修订历史和 Task↔Turn 精确绑定仍缺公开合同（G02/G03）。研究范围只展示服务端约束。本次变更限于 UI-05A，沿用既有工作区内容。见[合同缺口](../contracts/CONTRACT_GAPS.md)。

## 验证

- 从 `source/` 执行 `../bin/dev check --scope web`，38 个测试文件、365 项通过。类型检查、生产构建、Ruff 和 diff 检查通过。
- 使用仓库锁定 Python 执行 `kernel/tests/test_tasks.py` 和 `kernel/tests/test_session_history.py`，43 项通过。
- `npm --prefix source/web run test:e2e:build`，生产构建 Chromium 28 项通过。新增用例检查任务概览、刷新后读取最新规格、390px 无横向溢出以及切换至无 Task 会话，并输出桌面和窄屏截图。HTTP 和 SSE 使用合同兼容的测试响应，未运行真实跨服务 E2E。
- UI-05A 复核在当前工作区执行 `npm --prefix source/web test`，38 个文件、372 项通过，含 57 项 Task、WorkspacePanel 和 Selector 定向测试。重新执行 Kernel Task 和 Session 历史回归，43 项通过。
- 当前工作区生产构建 Chromium 全套 33 项通过，含正常提交补充信息、服务端返回新 Task 修订、刷新恢复、窄屏聚焦当前待确认修订及切换至普通咨询。TypeScript 检查随生产构建通过，`git diff --check` 通过。测试使用合同兼容响应，未在本次复核运行真实跨服务 E2E。

## 长对话复核

任务标题区移到对话滚动容器上方，滚到最新消息时仍能查看任务状态和打开概览。标题区有高度上限，长目标或等待原因可以在区内滚动。继续使用当前 Session 的 Task 快照及 UI-02 Selector。

本轮修改 `App.tsx`、`task-overview.css` 和 `build-task-overview.spec.ts`。新增长对话断言在调整前复现标题区移出视野，调整后在 1440×900 和 390×844 通过。浏览器用例仍验证提交补充信息、刷新恢复、打开当前确认以及切换至普通咨询。截图保存在本次聊天的 `ui-05a` 截图目录。

- `npm --prefix source/web test` 通过 373 项（38 个文件）。Kernel Task 与 Session 历史测试通过 43 项。Pytest 清理早先运行遗留的临时目录时发出权限警告。
- `npm --prefix source/web run test:e2e:build` 通过 33 项，包含 TypeScript 检查和生产构建。UI-05A 浏览器用例使用合同兼容的 HTTP/SSE 测试响应。本轮真实跨服务 E2E 为 NOT_RUN。`git diff --check` 通过。

长目标回归在 390×844 复现了标题区内的操作被推到滚动区下方。标题区将研究目标和等待说明各显示为最多两行，完整目标和缺项或阻塞原因可在概览读取。生产构建后定向 Chromium 用例通过，验证两个操作均完整在视野内、概览展示完整长目标，且确认入口聚焦当前待确认修订。

## 完成证据复核

Kernel 在 `tasks/persistence.py` 的 `_apply_evidence` 校验 USER_REVIEW 原文，并持久化评审轮次。UI-02 Selector 使用快照内的这两个字段检查任务完成所需证据。原文与当前条件不一致或缺少有效评审轮次时，状态显示「待核实」。两个回归在修复前均复现误报完成。改动限于 `ui-selectors.ts`、`task-overview.test.tsx` 和进度记录。Web 375 项通过，生产构建及两项 Task Chromium 定向用例通过，`git diff --check` 通过。

最终复跑 `npm --prefix source/web run test:e2e:build`，退出 0，生产 Chromium 33 项通过，0 失败、跳过或 flaky。截图与 Playwright JSON 已保存到本聊天的 `ui-05a-final` 目录。从 `source/` 执行 `../bin/dev check --scope contracts`，退出 0，190 项合同测试、Runtime OpenAPI、公开和生成 TypeScript 合同、Python SDK、Ruff 与 diff 检查通过。生产浏览器的 Task 路径使用合同测试响应。本次复跑的真实跨服务测试为 NOT_RUN。

随后使用当前工作区和锁定 Python 从 `source/` 执行 `python -m scripts.checks.check_compose_e2e --product groundwater --webui-only --webui-report …/ui-05a-isolated`，退出 0，20 项通过，0 失败、跳过或 flaky。使用固定本地测试 Provider 和实际 Gateway、Kernel、Executor、Data、Knowledge。Task 用例验证补充要求推进规格修订，服务端持久化匹配的评审证据，归档后重新打开和刷新恢复任务完成。Playwright JSON 和截图在本聊天的 `ui-05a-isolated` 目录。外部 Provider 未参与这次验收。
