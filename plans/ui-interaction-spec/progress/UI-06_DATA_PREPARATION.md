# UI-06 数据准备交互

基线 `f0b9f6d`，工作区已有 UI-01 至 UI-05B 的未提交实现及测试。

- 会话数据页继续从本地草稿、用户消息、当前准备快照和 Confirmation 冻结引用展示四个阶段。准备快照来自 Kernel `GET …/messages` 和 `GET …/preparation` 的同一投影。历史修订、其他轮次与终态准备保留只读事实，当前修订的逐分析状态使用 Kernel 条目级 READY/BLOCKED。PASS、FAIL、UNKNOWN 与未来枚举各有单独文案。
- Data 版本与语义读取沿用 `readDataVersion`，核对数据集、版本、语义、对象、checksum、格式、媒体类型与大小。通过身份检查后才显示字段、单位、统计、CRS、时间和输入版本，复用已有 `DataFieldQuality`。缺少画像时显示未提供。读取仅在数据页发起，按项目、身份、会话、引用及请求隔离。
- 版本详情带精确引用打开现有资料库。Kernel 提供的替代版本号通过 Data 读取实际对象并附加到普通草稿。切换会话、身份或连续选择时中止之前的替代版本读取。需要变更冻结批次时继续使用现有参数编辑器和服务端确认。
- `source/web/src/WorkspacePanel.tsx`、`App.tsx`、`workbench-polish.css`、`WorkspacePanel.test.tsx`、`workflow-draft.test.tsx` 与 `source/web/e2e/build-task-overview.spec.ts`。进度、决策与合同缺口文档同步更新。
- 阶段限于 Web。Data API 和 Kernel 准备合同沿用现行版本。真实跨服务 E2E 与真实 Provider 待 UI-13 验证。

## 验证

- `../bin/dev check --scope web` 从 `source/` 执行，退出 0，369 项 Vitest，通过类型、生产构建、Ruff 与 diff 检查。新测试先复现逐分析状态缺失。
- `../bin/dev check --scope contracts` 通过 OpenAPI、生成类型、Python SDK 和 187 项合同测试。
- 锁定 Python 运行 Kernel Tasks/Session History 与 Data Profile，47 项通过。
- `npm --prefix source/web run test:e2e:build` 退出 0，30 项通过。新增数据准备用例覆盖 1440×900、1024×768、768×1024 和 390×844，保存桌面和手机截图。浏览器使用合同兼容的测试响应。

## 授权后的版本核验复核

基线仍为 `f0b9f6d`，保留原有工作区改动。Kernel `preparation_tool.py` 从原输入的 Data 版本详情读取当前版本并投影 `SELECTION_POLICY` 检查。Data `catalog/service.py` 的版本详情读取数据集的 `current_version`。本轮在附加建议版本前核对 Data 返回的版本 ID、原输入数据集和该数据集的当前版本。失败留在工作台显示原因。用户随后手动选择附件会取消之前尚在途的建议版本读取。

修改 `App.tsx`、`WorkspacePanel.tsx`、`api.ts`、`app.test.tsx`、`workflow-draft.test.tsx`、`build-task-overview.spec.ts` 及进度记录。回归覆盖正确版本、跨数据集响应、已被替代的建议，以及核验失败时同页的独立分析仍可提案。

- Web Vitest 377 项、Kernel Task/Session History 与 Data Profile 47 项、生产 Chromium 33 项均通过。类型检查随生产构建通过，`git diff --check` 通过。
- 浏览器使用合同兼容响应。真实跨服务的同轮多候选验收仍受 G18 限制，本轮未修改公开合同。截图及 Playwright JSON 位于本聊天的 `ui-06` 目录。

补充验证同一会话内的准备修订竞态。浏览器读取建议版本期间，SSE 触发准备快照刷新。旧请求在快照失效或准备身份、修订、所属轮次及有效性改变时取消，迟到响应无法添加附件或跳转对话。回归在修复前复现错误附加，修复后通过。Web 378 项、生产构建与数据准备 Chromium 定向用例通过。

生产构建浏览器用例也覆盖该竞态。在 390×844 下暂缓旧建议的 Data 响应，通过 SSE 测试事件切换到修订 2，再释放旧响应。页面停留在数据准备页，选择新建议后草稿只含新版本。定向 Chromium 1 项通过，使用合同兼容的 HTTP 和 SSE 响应。

加入窄屏竞态后，全量生产构建浏览器回归退出 0，33 项通过，零失败、跳过或 flaky。最终 Playwright JSON 和桌面、移动端截图保存在本聊天的 `ui-06-final` 目录。
