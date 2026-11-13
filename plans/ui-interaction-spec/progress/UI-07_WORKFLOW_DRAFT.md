# UI-07 方案摘要与技术详情

基线 `f0b9f6d`。延续 UI-06 的工作区。

- Kernel 会话草稿读取按 Session 与 Draft ID 授权，返回产生草稿的 AgentRun 和 goalRevision、状态、失败诊断和编译后的草稿 payload。草稿的标题与摘要承载方案目标和方法说明；合同没有独立的科研目标或范围字段，相关内容不从正文推断。
- 默认按标题与摘要、待补充事项、分析步骤、预期输出、精确输入引用、参数与来源、限制展示。输入合同和知识证据继续按需展开。依赖图移入折叠区，保留节点详情、端口和依赖布局。
- 草稿徽标和导出入口使用 UI-02 Selector。成功草稿需明确 `executionAllowed: false` 才显示导出。卡片保持草稿只读，执行授权属于 Confirmation。
- UI-13 复核发现编排轮次可以为 COMPLETE 而 payload 为空。Selector 将其投影为中性的「未生成可用草稿」，说明引导用户查看本轮回复，导出继续要求有效 payload。
- 本卡修改 `source/web/src/WorkflowDraftCard.tsx`、`workflow-draft.test.tsx` 和进度记录。测试先复现图未折叠，再验证大图、缺项、多输出、失败和取消。未运行真实 Provider。

## 验证

- 先复现 DAG 默认展开；修正后草稿组件与 Selector 合计 45 项通过。
- `../bin/dev check --scope web` 通过 369 项 Vitest、类型检查、生产构建、Ruff 和 diff 检查。
- 锁定 Python 执行 `kernel/tests/test_workflow_drafts.py`，56 项通过。
- `npm --prefix source/web run test:e2e:build` 退出 0，生产构建 Chromium 30 项通过。依赖图交互由组件测试验证，真实跨服务 E2E 尚未运行。

## 授权后的技术详情复核

依赖图原先折叠时仍会计算布局和创建 SVG。现在由原生折叠控件按需创建图，关闭时移除图及节点选择。摘要仍列出全部分析步骤和预期成果。测试先复现折叠状态仍有 SVG，再验证 40 节点、39 条节点依赖、两个精确输出以及关闭和重新打开。修改 `WorkflowDraftCard.tsx`、`workflow-draft.test.tsx`、`v12-question-matrix.spec.ts` 和进度记录。

- Web Vitest 379 项（38 个文件）通过，Kernel `test_workflow_drafts.py` 56 项通过。E2E 文件独立 TypeScript 检查及 `git diff --check` 通过。
- 隔离 Compose `check_compose_e2e --product groundwater --webui-only` 退出 0，20 项通过，零失败、跳过或 flaky。真实 Gateway、Kernel、Data、Knowledge、Executor 和固定本地 Provider。草稿场景验证键盘展开、390px 页面无横向溢出、关闭后移除、知识正文回溯、JSON 导出，以及旧草稿与新草稿在刷新后均可读取且没有执行授权。截图与 JSON 位于本聊天 `ui-07-isolated`。外部 Provider 未运行。
- 独立科研目标和范围仍未由草稿合同提供，沿用持久草稿标题和摘要。本轮沿用后端公开合同。

草稿读取补充核对返回的 Draft、Session、AgentRun 与目标修订号。身份不一致时显示单条草稿的读取错误和重试入口，已读取的其他草稿仍可使用。四种不匹配回归在修复前复现草稿消失且无重试入口，修复后与原有 503 恢复用例一同通过。修改 `use-session-stream.ts`、`api.ts` 和 `workflow-draft.test.tsx`。Web 全套 383 项、生产构建及 diff 检查通过。本次补充未重跑隔离 Compose。

随后扩展 `v12-question-matrix.spec.ts` 的隔离验收。刷新时让第二份草稿的读取返回第一份真实草稿。界面保留第一份并提示第二份身份不符。重试直连 Kernel 后，两个不同草稿均按各自身份恢复。隔离 Compose WebUI 20 项通过，零失败、跳过或 flaky。独立 E2E TypeScript 检查与 `git diff --check` 通过。日志、JSON 和截图位于本聊天的 `ui-07-draft-identity` 目录。
