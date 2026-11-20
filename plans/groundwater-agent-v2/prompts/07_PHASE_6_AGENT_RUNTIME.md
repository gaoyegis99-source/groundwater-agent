# 阶段 6：Agent Runtime 业务化改造
## LOCAL WORKSPACE ONLY（强制约束）

本任务只针对 Codex 当前打开的**本地工作区代码**。

### 禁止远程连接和远程操作
- 不连接 GitHub、GitLab、Bitbucket 或任何远程代码托管平台。
- 不调用 GitHub/GitLab API、浏览器、远程 MCP/Connector、远程代码搜索或远程文件读取工具。
- 不执行 `git fetch`、`git pull`、`git push`、`git clone`、`gh`、`glab`、远程 PR/Issue 操作或任何会访问远程仓库的命令。
- 不访问仓库配置的 remote URL；不要为了核实代码而联网。
- 不将本地文件上传到远程服务，不创建远程分支、提交或 PR。
- 不通过网络下载依赖、工具、模型、数据或文档。若环境缺少依赖，报告缺失项和离线解决建议。

### 允许的本地操作
- 读取和修改当前工作区内文件。
- 使用本地 `git status`、`git branch --show-current`、`git log`（仅本地已有对象）、`git diff`、`git diff --check`。
- 运行本地已有的测试、格式化、静态检查、构建和装配命令。
- 搜索当前工作区文件；优先使用 `rg`、`find`、本地 IDE 搜索和仓库已有脚本。
- 创建本地文档、测试、迁移文件和报告。

### 离线失败处理
- 若本地仓库不是完整工作树、子模块未初始化、LFS 文件缺失、依赖缓存不存在或测试需要外部服务，停止相关验证并准确报告。
- 不得用远程仓库内容补齐本地文件。
- 不得声称未运行的测试已通过。
- 不得假设当前本地代码等于远程默认分支；一切以当前工作区实际内容为准。

### 本地数据保护
- 开始前检查 `git status --short`，不得覆盖用户未提交改动。
- 不执行 `git clean`、`git reset --hard`、批量删除或破坏性迁移，除非用户明确授权。
- 不擅自切换分支或改写提交历史。
- 默认不创建 Git commit；只有用户明确要求时才提交。
- 所有输出保存在当前工作区指定目录；不上传、不同步。


全局遵循 `00_MASTER_CONTROL_PROMPT.md`。

## 目标

让 Agent 围绕业务目标、WorkItem 和 Capability 组织上下文和回答，保留 Kernel 的工具授权、确认和执行边界。

## 重点代码
- `agent_runtime/prompts.py`
- `context_policy.py`
- `memory.py`
- `reply_schema.py`
- `reply.py`
- `tooling/reply_submission.py`
- `tooling/catalog_tools.py`
- `workflow_draft.py`
- `model_tool.py`
- `interaction/message_requests.py`
- 相关 reply、context、resume 和 workflow tests

## 任务

### 1. Business Request Context
为每轮请求构建只读业务上下文：project、work_item、相关对象引用、精确资产引用、当前焦点、当前任务状态和本轮用户目标。
所有引用由服务端重新校验。浏览器上下文不构成权限。

### 2. 意图和能力选择
让意图分类识别咨询、数据操作、业务任务、分析、Workflow、执行、结果动作、会话控制和澄清。
路由只决定处理路径，不授予执行权。
常见需求先选择 Business Capability/Profile；动态 Operator 组合是受限的高级路径。

### 3. 上下文策略
保留当前 Context Policy 的权威来源分层：
- Kernel/Execution/Data/Knowledge 事实；
- 用户明确要求；
- 非权威 Memory Summary。
摘要可以保留业务目标、决策、对象引用和待办，但必须回查当前资源和状态。

### 4. 回答类型
实现 DIRECT_ANSWER、GUIDANCE、CLARIFICATION、TASK_OVERVIEW、TASK_PROPOSAL、PROGRESS_UPDATE、EXECUTION_DECISION、DELIVERY、RECOVERY 等结构化业务回复投影。
先兼容当前 `SubmittedReplyV6` 和 `ReplyPresentationV1`，避免破坏旧 Provider 协议。
服务端校验来源、资源身份、动作、WorkItem 作用域和执行事实。

### 5. 执行后结果解释
从精确发布成果读取可解释摘要和诊断。若结果缺少摘要或无法读取，应明确说明，不让模型猜文件内容。
把执行成功、发布成功、质量检查和业务验收分开表达。

### 6. Prompt 模块化
将产品角色、能力选择、业务语言、权限边界、回复类型和特定 Workflow 指令拆分为可测试的模块。不要继续把所有规则堆进一个超长提示词，也不要删掉必要的安全规则。

## 验收
- 简单咨询无需不必要的目录搜索；
- 常见业务目标优先走预定义 Capability；
- 多轮追问继承正确 WorkItem；
- 多个候选工作项时澄清；
- 动态组合不绕过编译和确认；
- 模型不能伪造执行或发布状态；
- 所有回复类型通过 Schema 和回归测试。
