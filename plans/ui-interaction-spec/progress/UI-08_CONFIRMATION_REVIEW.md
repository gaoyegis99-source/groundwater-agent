# UI-08 Confirmation 修订与审批

基线 `f0b9f6d`，沿用工作区已有审批流程。Kernel `interaction/revisions.py` 验证当前 goalRevision、源 PENDING 批次和同一 action ID 集合，生成新冻结批次和替代关系。确认与拒绝继续调用现有 Session 端点，修订及回执核对保留原 requestId。浏览器在同一会话读取代内只允许一次确认类提交在途，服务端仍负责幂等和权限。

确认卡按明确的替代批次 ID 与 action ID 比较，包括移除的字段。各 action 的冻结输入以精确 ResourceReference 打开资料库。参数来源缺口 G17/ADR-UI-010，继续使用冻结值和来源可确定的其他快照。修改 `ChatCards.tsx`、`use-message-actions.ts`、`recovery-confirmation.test.tsx` 和进度合同记录。

## 验证

- 新增组件回归先复现冻结链接和移除差异缺失，修复后通过。
- 锁定 Python 执行 `kernel/tests/test_confirmation_recovery.py`，14 项通过，覆盖新修订重新审批、权限和幂等。
- `../bin/dev check --scope web` 退出 0，370 项 Web 测试、TypeScript、生产构建和 Ruff 通过。`npm --prefix source/web run test:e2e:build` 退出 0，30 项通过。覆盖当前修订操作、旧确认只读、恢复后新确认、重复点击与回执恢复。拒绝分支由组件和服务端测试覆盖；真实跨服务浏览器审批未运行。

## 授权后的回执复核

HTTP 5xx 无法确定操作是否已保存。确认类操作收到 5xx 时，前端现在核对会话快照。已保存的修订按原 requestId 识别并回到新确认卡。源批次仍可操作时，已核实的当前快照才允许重试，同一修订沿用原请求 ID 和完整参数。回归分别覆盖 502 提交前、提交后，以及重复点击和 503 后的显式审批重试。修改 `use-message-actions.ts`、`App.tsx`、`workspace-parameter.test.tsx`、`progress.test.tsx`、隔离测试及其请求辅助函数。

- Web 385 项、Kernel 确认和恢复 14 项通过。隔离 Compose 的生产构建、E2E TypeScript 检查及 `git diff --check` 通过。
- 隔离 Interaction 6 项通过，退出 0。浏览器收到模拟 502 前，真实 Gateway 已返回修订成功的 201。随后核对修订 2 处于待确认、修订 1 无审批入口，SDK 显式批准后只启动一次运行，TLI 和 Web 读到同一结果。隔离环境使用固定本地测试 Provider。截图、门禁与修订证据位于本聊天的 `ui-08-isolated` 目录。
- G17 仍需后端提供冻结批次的逐参数来源。
- 5xx 回执修复后复跑 `npm --prefix source/web run test:e2e:build`，退出 0，生产 Chromium 33 项通过，零失败、跳过或 flaky。覆盖当前修订键盘操作、320px 至 1440px 响应式场景和恢复后新确认。Playwright JSON 位于本聊天的 `ui-08-build` 目录。

断线复核在修复前发现，参数编辑器仍可对未核实快照提交修订。现在编辑器与确认卡使用相同的权限、实时连接与快照判断，断线期间保留输入并禁用保存。修改 `App.tsx`、`WorkspacePanel.tsx` 和 `workspace-parameter.test.tsx`。Web 386 项和生产 Chromium 33 项通过，构建及 `git diff --check` 通过。本次补充未重跑隔离 Interaction。

旧轮次批次回归验证，历史轮次的 PENDING 记录只显示在历史批次中。工作台仅从当前轮次最新且可审批的批次提供编辑入口，已经打开的旧编辑器禁用保存。数据缺项入口使用当前会话确认卡的批次。修改 `WorkspacePanel.tsx`、`App.tsx`、`WorkspacePanel.test.tsx`。Web 387 项、生产 Chromium 33 项、构建和 `git diff --check` 通过。未重跑隔离 Interaction。
