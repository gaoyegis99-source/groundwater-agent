# 阶段 1：Operator 能力治理
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


全局遵循 `00_MASTER_CONTROL_PROMPT.md`。本阶段优先完善算子治理，不新建 Operator Registry 服务，不重写 Operator Pack。

## 目标

让 Operator 成为可发现、可验证、可版本治理、可安全组合的专业计算资产，并为 Workflow 和产品配置提供稳定元数据。

## 重点代码

- `source/packages/operator-pack/README.md`
- `source/packages/operator-pack/catalog/index.json`
- `catalog/operators/**/manifest.json`
- `catalog/operators/**/smoke.json`
- `catalog/implementation-lock.json`
- `source/kernel/src/platform_kernel/application/operator_catalog.py`
- `source/kernel/src/platform_kernel/agent_runtime/tooling/catalog_tools.py`
- Assembler 的 operator catalog / validation / implementationRevision 检查
- Operator Pack tests、对应 Worker image smoke tests

## 设计要求

### 1. 先盘点，不要改 Manifest 身份
统计：
- manifest 数量；
- 唯一 operator ID；
- operator ID + version 数量；
- runtime 分布；
- Workflow 引用数；
- composition whitelist 覆盖情况；
- validation 状态；
- scientific review 状态。

### 2. 验证证据分层
明确区分：
- Schema/Contract Validation；
- Unit Test；
- Smoke Test；
- Integration Test；
- Domain Case Validation；
- Regression Evidence；
- Scientific/Professional Review。

不得用单一 `PASSED` 代表全部专业验证。若扩展状态合同，先做版本化设计和迁移兼容。

### 3. 业务元数据
评估将业务元数据放在 Domain/Product sidecar 还是升级 Operator Manifest。优先 sidecar，除非有明确跨产品复用价值。
建议字段：display name、purpose、use cases、input role、output meaning、limitations、maturity、required evidence。元数据不能改变算子执行身份。

### 4. 实现依赖图
审查 implementation lock 的共享源文件扩散问题。只做依赖图原型和影响对比：
- 静态 import；
- 动态 dispatch；
- 静态映射表；
- runtime 公共文件；
- Worker image 内容。
未知依赖必须保守包含。没有证明完整性前不得替换现有 lock 算法。

### 5. 目录发现
检查 `list_operators` 和 `get_operator` 的分页、过滤、权限和输出。让普通 Agent 优先按业务能力查找，不向普通用户暴露庞大的原子算子目录。高级动态组合路径仍可使用 Catalog Tools。

## 不做的事
- 不新增网络 Registry；
- 不改变 Executor/Worker 所有权；
- 不批量升级所有 Operator；
- 不因一个算子修改就随意刷新无关版本；
- 不把业务描述塞入执行参数；
- 不把未验证算子自动开放给用户。

## 输出
- Operator 治理模型；
- 元数据 Schema 提案；
- 验证证据矩阵；
- lock 依赖图原型报告；
- 当前 Catalog 健康度报告；
- 最小必要代码改动及测试。

## 验收
- 旧 Operator 精确身份仍可解析；
- stale implementation 不会被误标为已验证；
- 业务元数据不影响执行身份；
- 所有 runtime 对应的 smoke 验证可追溯；
- Catalog 查询不会绕过组合白名单；
- 所有新增合同有版本和回归测试。
