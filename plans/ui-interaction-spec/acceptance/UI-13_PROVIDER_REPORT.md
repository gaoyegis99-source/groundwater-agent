# UI-13 外部 Provider 验收

状态为 **FAIL**。在隔离 Groundwater Compose 的 `groundwater-demo` 项目运行现有回复质量门禁。真实 Agent 与 Intent Router 使用配置的 DeepSeek Responses / deepseek-flash；知识检索用研究配置中的 Embedding 端点。提交冻结合成输入，逐次新建会话。自动门禁记录 24 个唯一区分的会话及 AgentRun，Provider fallback 为 0，13 次通过、11 次失败。

| 用例 | PASS / 执行 | 失败码 |
|---|---:|---|
| 简单问题与回复结构 | 3 / 3 | |
| 知识证据与事实 | 2 / 3 | `AGENT_MODEL_OUTPUT_INVALID` |
| 未检索到证据时的回复 | 3 / 3 | |
| 地下水澄清与选项跟进 | 2 / 3 | `OPTION_AUTO_FOLLOWUP_TERMINAL_INVALID` |
| 方法比较 | 3 / 3 | |
| Workflow Draft | 0 / 3 | 2 次 `AGENT_MODEL_OUTPUT_INVALID`，1 次 `WORKFLOW_DRAFT_OUTPUT_MISMATCH` |
| 确认、执行与成果解释 | 0 / 3 | 2 次 `AGENT_MODEL_OUTPUT_INVALID`，1 次 `CONFIRMATION_ARGUMENTS_MISMATCH` |
| 拒绝执行 | 0 / 3 | 3 次 `AGENT_MODEL_OUTPUT_INVALID` |

完整记录在 `ui-13-final/provider.json`。测试通过 Gateway 调用 POST Session、POST messages、GET Session、GET messages；根据用例另请求 Knowledge、草稿读取、执行确认或拒绝。准备阶段失败的执行样本未进入结果解释，结果解释独立验收为 `NOT_RUN_PROVIDER`。研究目标理解仅有简单问答和澄清证据，持续科研 Task 的目标理解为 `NOT_RUN_PROVIDER`。参数修改、浏览器刷新后的多轮恢复和人工领域盲审为 `NOT_RUN_PROVIDER`；澄清选项跟进已执行。

建议优先按失败类别复现并核对 Kernel 接收的结构化输出、草稿及确认参数；确认哪些为 Provider 回复质量问题，哪些为服务端解析或规则问题后再提变更。维持冻结用例、断言与真实 Provider 配置重新验收。
