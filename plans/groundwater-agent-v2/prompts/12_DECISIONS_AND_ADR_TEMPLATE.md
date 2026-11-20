# V2 架构决策记录（ADR）模板
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


每个 DECISION 项必须形成独立 ADR，避免 Codex 在实现时自行猜测。

## ADR-NNN：标题

- 状态：PROPOSED / ACCEPTED / REJECTED / SUPERSEDED
- 日期：
- 决策负责人：
- 关联阶段：
- 关联代码路径：

### 1. 背景
说明当前代码和产品问题，引用实际文件、合同和测试。

### 2. 需要决策的问题
描述具体选择，不使用模糊目标。

### 3. 备选方案
每个方案说明：
- 所有权；
- API/Schema 影响；
- 数据迁移；
- 权限；
- 运行和恢复；
- 前端影响；
- 维护成本。

### 4. 决策
记录最终选择和原因。

### 5. 后果
记录收益、代价、风险和后续工作。

### 6. 验收
列出可以验证的测试、命令和业务场景。

## V2 首批必须形成的 ADR

1. WorkItem 与 Kernel Task 的职责边界。
2. BusinessObject 主数据权威所有者。
3. WorkItem 与 Session 的多对多关联规则。
4. Business Capability / Workflow Profile 的版本所有权。
5. Operator validation 与专业准入状态的分层。
6. Workflow Result Contract 的字段和权威来源。
7. API v12 的兼容与迁移策略。
8. 历史 Session 的映射和保留策略。
