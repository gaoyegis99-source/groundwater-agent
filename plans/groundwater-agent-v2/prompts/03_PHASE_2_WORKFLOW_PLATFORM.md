# 阶段 2：Workflow 平台、Profile 与结果合同
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


全局遵循 `00_MASTER_CONTROL_PROMPT.md`。复用现有 Workflow Compiler、Workflow Draft、Composition Contract 和 Executor。

## 目标

让 Workflow 成为可复用的执行定义；Profile 成为经过验证的业务配置；输出合同成为 Agent 和 UI 可可靠解释的依据。

## 重点代码
- 活动 Domain 下 `workflows/**`
- `models/**`
- `domain-manifest.json`
- `source/kernel/src/platform_kernel/agent_runtime/workflow_draft.py`
- `agent_runtime/tooling/catalog_tools.py`
- `interaction/workflow_drafts.py`
- `source/packages/workflow-compiler/` 实际路径
- Executor Workflow validation / execution / publication
- Workflow tests 和 domain cases

## 任务

### 1. Workflow/Model/Operator 关系审计
对每个 Workflow 输出：
- workflow ID/version/checksum；
- 输入和输出；
- 节点与 Operator 精确版本；
- 参数来源；
- composition support；
- 使用它的 Executable Model；
- 产品能力入口；
- 验证用例。

确认 Executable Model 只承担业务调用适配、输入绑定、参数合同和输出声明，不复制 Workflow DAG。

### 2. Profile 设计
先选一个核心场景作为垂直切片，例如 monitoring-trend 或 flow-field。
评估同一 Workflow 是否有真正不同的用户档位。只有当不同档位有明确的计算语义、输出差异和验证案例时才引入 Profile。
Profile 至少声明：
- profile ID/version；
- 精确 workflow ref/checksum；
- 输入角色和必需条件；
- 参数集合及默认值来源；
- 可由用户调整的参数；
- 输出集合；
- 适用条件和限制；
- validation evidence；
- 可见用户角色。

不要仅通过关闭节点或删输出制造“基础版”。若计算拓扑不同，必须形成独立、可验证的定义。

### 3. Compiler 质量检查
盘点现有 compiler 能力，避免重复实现。评估：
- DAG 拓扑和循环；
- 端口 media type / format；
- 输入角色与数据绑定；
- 参数完整性；
- 可达输出和无用节点；
- 节点重复计算；
- 资源预算；
- 组合白名单；
- 条件输出格式；
- 业务前提的外部验证结果。

资源预算必须考虑并行度和运行时约束，不得简单累加所有节点 memory limit。

### 4. Workflow 输出合同
标准化：
- PRIMARY_RESULT；
- DIAGNOSTIC；
- VISUALIZATION；
- SUMMARY。
每个输出包含稳定名称、类型、格式、业务含义、可用查看器、单位/空间/时间范围（适用时）和质量状态来源。
保留底层精确 Data Reference。

### 5. 版本兼容和影响分析
明确兼容/向后兼容/不兼容规则。建立 Operator → Workflow → Profile → Model → Capability 的自动依赖图。不能人工维护重复依赖表。

## 不做的事
- 不重写 Compiler；
- 不复制 Workflow 成第二套模型定义；
- 不修改历史版本；
- 不让 Profile 绕过 composition validation；
- 不把执行成功等同输出完整或业务完成。

## 输出
- Workflow inventory；
- Profile Schema 和首个真实 Profile；
- Compiler 缺口分析；
- Result Contract；
- 版本兼容策略；
- 最小代码实现与测试。

## 验收
- 至少一个核心 Workflow 从准备、编译、确认、执行到结果读取完整通过；
- Profile 选择结果可复现；
- 输出身份和含义稳定；
- 旧版本历史运行可读取；
- 不支持的组合明确失败，不做静默替代。
