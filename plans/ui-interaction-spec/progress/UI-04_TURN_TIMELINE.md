# UI-04 Turn 时间线实施与恢复验证

基线 HEAD `f0b9f6d6d5e1df970794d2cd16b9cdcec387b7fc`。在既有 UI-01 至 UI-03A 工作区上继续，Session 导航与 Task 工作台沿用已有交互。

## 实现

- `TurnTimeline` 按历史快照的 `goalRevision` 组织用户输入、结构化引用、活动、正式回复、澄清、业务卡和错误。显示服务端时间与 Selector 状态；轮次定位提供键盘焦点。当前确认固定入口保留，轮内提供定位链接。历史确认折叠且只读。
- 草稿检查 Session、draftId、agentRunId 和 goalRevision。确认检查当前 Turn 的 agentRunId/goalRevision，展示实际 revision。Run 按 workflowRunId 保留各尝试。Result 检查 workflowRunId、agentRunId、toolCallId、goalRevision、成功 action 状态与成果数，精确 Data 版本、对象、语义和 checksum 核验后开放预览/下载。
- 正式 Markdown 使用既有安全渲染链，增加受限的 KaTeX MathML。禁用 raw HTML 和 LaTeX 信任指令。宽表格和公式局部滚动，代码按需展开。仅允许当前轮已核验成果图片，引用保留精确身份。支持复制提问和正式答复。
- ActivityPanel 读取公开标题、摘要、状态、里程碑和时刻。耗时从服务端 startedAt/finishedAt 计算，排队不计入执行耗时，缺少时刻时省略。实时计时仅表示经过时间；失败和历史记录可展开查看。
- 最新内容变更时仅跟随处于底部的读者。向上阅读显示跳转入口；历史分页保持当前可见轮次位置。当前确认及澄清操作等待有效快照，历史卡片不会获得审批权限。

## SSE 与历史恢复

| 场景 | 验证路径 |
|---|---|
| 重复、乱序、无效 eventId | 状态函数单测拒绝回退和非法整数；组件回归验证重复/乱序不增加快照读取。 |
| 初次连接、续接 | Chromium 真实 EventSource 请求带 `afterEventId=10`，收到 eventId=11 后断线，下一次请求带 `Last-Event-ID: 11`。浏览器使用符合 v12 的 SSE 测试响应。Kernel 本地测试从真实持久事件读取并检查服务端回放。 |
| 快照水位 | 低水位读取不替换当前事实，超出快照水位的事件触发核对；重新连接后的批准入口等待快照。 |
| 历史分页 | 同轮跨页按消息 ID、轮次归并，旧页不改变当前水位和正式回复；Playwright 核对合并后轮次定位。 |
| Session / Project 切换 | 保留 scope/generation/AbortController 隔离。迟到旧页、成果、预览及 Session 变更回归测试；项目切换的目录响应沿用 UI-03A 测试。 |
| 临时回复 | 预览单独读取，忽略旧序号。断线清理、正式快照替换；受其他轮次终止事件影响时仍按 AgentRun 身份过滤。 |

## 验证结果

- `../bin/dev check --scope web`（在 `source/`）退出 0。37 个测试文件、353 项通过，含普通咨询、有 Task 的会话、澄清、活动状态与耗时、多个确认与 Run、长文本与表格、MathML、分页、断线、向上阅读和失败状态。类型检查、生产构建及 Ruff 通过。
- 锁定 CPython 3.12.8 运行 `python -m pytest -q kernel/tests/{test_session_history,test_v11_contract,test_task_controls,test_turn_progress,test_session_results,test_confirmation_recovery}.py` 退出 0，59 项通过。
- `npm --prefix source/web run test:e2e:build` 最终退出 0，27 项通过。包括真实浏览器 SSE 请求头与断线续接、科学表格及公式、跨页、复制和轮次定位。布局测试使用稳定连接 fixture；原先有限流在新审批保护下立即断开，历史确认测试也已改为先展开再检查。真实跨服务 E2E 未运行。
- Chromium 截图为 `turn-timeline-desktop.png`（1440×900）与 `turn-timeline-mobile.png`（390×844）。截图及 `ui-04-playwright-results.json` 随任务交付。

## 修改文件

- 时间线、投影和显示：`TurnTimeline.tsx`、`App.tsx`、`ActivityPanel.tsx`、`ChatCards.tsx`、`MessageContent.tsx`、`WorkflowDraftCard.tsx`、`ui-selectors.ts`、`conversation-polish.css`。
- 读取：`use-session-stream.ts`、`session-stream-state.ts`。依赖和构建：`source/package-lock.json`、`source/web/package.json`、`vite.config.ts`。
- 回归：`activity.test.tsx`、`app.test.tsx`、`conversation-polish.test.tsx`、`message-content.test.tsx`、`message-routing.test.tsx`、`progress.test.tsx`、`session-stream-state.test.ts`、`workflow-draft.test.tsx`、`playwright.build.config.ts`、`e2e/build-turn-timeline.spec.ts`、`e2e/application-accessibility.spec.ts`、`e2e/build-air-quality-product.spec.ts`、`e2e/build-workflow-tasks.spec.ts`。
- 记录：[合同缺口](../contracts/CONTRACT_GAPS.md)、[进度](PROGRESS.md)、本报告。

## UI-05 前置条件

UI-05 依赖 UI-02、UI-03 和 UI-04。UI-03A 基础范围已完成，进入 UI-05 仍需用户批准，并明确是否接受 UI-03A 满足导航依赖。当前 TaskContext 可提供生命周期、条件和业务步骤。独立当前目标、公开修订历史及精确 Turn↔Task revision 绑定涉及 G02/G03，需要后端补充或批准采用现有公开范围。见[合同缺口](../contracts/CONTRACT_GAPS.md)。
