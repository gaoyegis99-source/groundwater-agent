# UI-10 失败停止与恢复

基线 `f0b9f6d`。本卡沿用 Kernel 的 AgentRun 级停止记录与恢复批次接口。Turn 时间线独立显示 UI-02 的 REQUESTED/PROCESSING/COMPLETE 投影，同时保留权威 Turn 状态。停止在途按当前会话读取代隔离，已有本地 requestId 继续用于显式重试。WorkflowRun/节点终态及已发布成果仍以服务端为准。

会话运行列表展示上游失败导致未执行的记录及阻塞来源。节点成功、失败与未执行状态取自 Executor；恢复沿用原冻结范围，Kernel 新建需要独立审批的批次。逐运行取消在途状态和逐节点恢复审计仍受 G05/G07/G10 限制。

修改 `TurnTimeline.tsx`、`WorkspacePanel.tsx`、`use-message-actions.ts`、`progress.test.tsx` 与进度记录。测试先复现停止状态只在按钮出现，修复后断言处理中与最终取消分开显示。定向 Web 72 项通过。

- 锁定 Python 执行 Kernel Task Controls、Confirmation Recovery 和 Executor Control，90 项通过，包括停止与发布竞争。
- `../bin/dev check --scope web` 退出 0，371 项、类型与构建通过。生产 Chromium 30 项通过，涵盖失败恢复并要求新确认、恢复成果的发布和下载。真实跨服务浏览器测试待 UI-13。

## 授权复核

本地 HEAD 为 `f0b9f6d6d5e1df970794d2cd16b9cdcec387b7fc`。开始时工作区有 52 个已修改的跟踪文件和 11 项未跟踪记录，包含既有前端实现及计划文档。本轮在原工作区增量修改。

- Kernel 的 `api/routes/session_commands.py` 将停止请求返回为 HTTP 202。 `interaction/control_persistence.py` 按 Session 和 requestId 幂等，并校验 AgentRun 一致性。 `interaction/history.py` 在快照提供同一请求的 REQUESTED、PROCESSING 或 COMPLETE。停止回执的 COMPLETE 表示请求处理结束，Turn 和 WorkflowRun 的终态及成果仍由服务端各自投影。
- 本地 POST 在途状态按身份、Session、读取代和 AgentRun 隔离。旧轮次回执和回执前到达的快照均有专门回归。请求响应失败时按 Session、AgentRun、requestId 核对服务端快照，匹配后解除该请求错误，排队纠正等待当前处理结束再发送。尚无回执时，显式重试沿用原 requestId。
- Kernel 的 `workflow_execution/persistence/queue.py` 选择当前 action 的最新失败、超时或取消尝试。 `interaction/recovery.py` 校验当前轮次和待确认状态，从原冻结批次复制所选 action 并创建新 PENDING RECOVERY Confirmation。既有运行记录和成果继续按原 WorkflowRun ID 展示。
- 本轮修改 `source/web/src/use-message-actions.ts`、`source/web/src/use-session-stream.ts`、`source/web/src/App.tsx`、`source/web/src/progress.test.tsx`、本报告、`DECISIONS.md` 和 `PROGRESS.md`。公共合同缺口见 [G05、G07、G10](../contracts/CONTRACT_GAPS.md)，分别涉及完整节点尝试与输出、逐 WorkflowRun 取消在途和远端清理、权威恢复资格及拒绝原因。

### 测试

- `npm --prefix source/web test -- src/progress.test.tsx -t 'previous turn has|predates the receipt|ambiguous stop response'` 在修复前退出 1，三个回归均复现。
- `npm --prefix source/web test -- src/progress.test.tsx src/recovery-confirmation.test.tsx` 在修复后退出 0，2 文件、61 项通过。
- 从 `source/` 执行 `../bin/dev check --scope web` 退出 0，38 文件、394 项通过，含 Selector 回归。类型检查、生产构建、Ruff 和差异空白检查通过。
- 从 `source/` 使用锁定 CPython 运行 `python -m pytest -q --basetemp "$(mktemp -d)" kernel/tests/test_task_controls.py kernel/tests/test_confirmation_recovery.py services/executor/tests/test_control.py`，退出 0，90 项通过。
- `npm --prefix source/web run test:e2e:build` 退出 0，生产构建 Chromium 33 项通过，含恢复范围、失败重试、重新确认和成果发布。该套件使用合同兼容的 HTTP/SSE 测试响应。
- 从 `source/` 执行 `../bin/dev check --scope contracts` 退出 0，190 项通过，运行时 OpenAPI、TypeScript 和 Python SDK 合同通过。
- 从 `source/` 使用锁定 CPython 执行 `python -m scripts.checks.check_compose_e2e --product groundwater --interaction-only --webui-report <证据目录>`，退出 0，6 项通过。隔离 Gateway、Kernel、Executor、Data 和原生 Worker 使用合成观测与固定本地 Provider。停止先完成的分支保留 CANCELLED 终态，发布先完成的分支保留 SUCCEEDED 及两份下载。超时恢复用例经 Web 创建新确认，验证 TLI 可读取，SDK 独立批准后创建新运行并下载成果。证据在 `ui-10-isolated/platform-groundwater-e2e-8a1b4d6b0a38/gate.json`，包含停止、超时、新确认和恢复成果截图。
- 外部模型 Provider 与完整生命周期门禁为 NOT_RUN。本轮核验范围为 UI-10。
