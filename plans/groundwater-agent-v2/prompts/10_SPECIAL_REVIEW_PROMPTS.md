# 专项审查提示词
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


本文件包含可在各阶段之间单独调用的审查提示词。审查时不自动修改代码。

## A. Workflow / Operator 版本影响审查

请审查当前仓库 Operator → Workflow → Profile → Executable Model → Business Capability 的真实依赖链。

要求：
1. 从 Manifest、Domain Manifest、Model workflowRef、Assembler 和 Compiler 自动提取依赖。
2. 统计唯一 ID、版本、checksum、引用者和 validation evidence。
3. 对一个指定 Operator 版本变化，列出受影响 Workflow、Profile、Model、Product UI 和测试。
4. 区分直接依赖、传递依赖和运行时共享依赖。
5. 不完整的依赖必须标为 UNKNOWN 并保守处理。
6. 不修改 lock 或历史版本。
输出影响矩阵、生成脚本建议和测试证据。

## B. Workflow Profile 合理性审查

请判断一个候选 Profile 是否值得独立存在。
检查：
- 用户是否有真实不同业务目标；
- 计算拓扑是否真的不同；
- 是否有明确输入/输出差异；
- 是否有独立适用条件；
- 是否有独立 Domain Case；
- 是否能复用已有 Workflow；
- 是否只是 UI 参数预设，不应成为新 Profile。
没有证据时给出 NO-GO，不要为了丰富产品目录制造 Profile。

## C. Agent 动态组合安全审查

请检查 `list_operators`、`get_operator`、`validate_workflow`、Workflow Draft、composition whitelist、reply submission 和 Confirmation 的边界。
重点寻找：
- 是否可能使用未允许组合的 Operator；
- 是否能绕过精确 Data/Knowledge evidence；
- 是否存在模型自造参数或默认值；
- 是否可能把草稿误当执行授权；
- 是否存在 Provider 协议适配绕过 Schema；
- 是否会把用户意图错误映射为执行。
输出可复现负例和最小修复建议，不要直接放宽验证。

## D. 结果交付合同审查

从 Workflow outputs、Executor publication、Data result metadata、Result UI 和 Assistant Reply 追踪一个核心成果。
检查：
- 主成果是否明确；
- summary 是否可读；
- diagnostics 是否可用；
- visualization 是否有稳定引用；
- 单位、CRS、时间范围和质量限制是否可追溯；
- 计算成功、发布成功和业务审核是否分离。
输出字段差距和端到端证据链。

## E. WorkItem / Task 边界审查

检查 WorkItem 是否变成第二套 Task。
逐字段说明：
- 谁拥有权威值；
- 是否可由现有 Task 投影；
- 是否需要独立持久化；
- 跨 Session 关联规则；
- 生命周期和权限；
- 删除/归档语义。
如果无法说明独立价值，建议不新增字段或表。

## F. 前端上下文泄漏审查

检查切换 Project、WorkItem、Session、DataVersion、地图图层和结果时：
- 旧请求是否 abort；
- 迟到响应是否可能覆盖新状态；
- sessionStorage/cache 是否按身份和项目隔离；
- 历史确认是否只读；
- 当前焦点是否可能被误当执行授权；
- 断线快照是否最终校正 UI。
输出竞态时序和测试方案。
