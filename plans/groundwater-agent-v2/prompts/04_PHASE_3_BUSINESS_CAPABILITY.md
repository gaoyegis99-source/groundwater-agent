# 阶段 3：Business Capability 与 Agent 能力选择
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

建立用户业务语言与 Workflow Profile / Executable Model 之间的稳定映射，让 Agent 默认走经过验证的预定义业务路径；只有确实需要时才进入动态 Operator 组合。

## 重点代码
- `source/domains/groundwater/<active-version>/domain-manifest.json`
- `models/**`、`workflows/**`
- `source/products/groundwater/`
- Product UI schema/装配
- `agent_runtime/prompts.py`
- `agent_runtime/model_tool.py`
- `workflow_selection.py`
- `agent_runtime/tooling/catalog_tools.py`
- `source/web/src/product-ui.ts` 等能力展示代码

## 设计任务

### 1. Capability Contract
定义：
- capability ID/version；
- 用户可读名称和说明；
- 目标业务场景；
- 适用角色；
- 输入资料角色；
- 前置条件；
- 可选 Profile；
- 预期成果；
- 限制和风险；
- 对应 Model/Workflow/Profile 精确引用；
- 验证状态；
- 是否默认展示。

### 2. 能力分层
建议 CORE / SPECIALIZED / ADVANCED / EXPERIMENTAL。分层规则由产品负责人批准，不能根据目录或模型名称推断成熟度。
普通用户默认只看到核心能力；高级能力明确说明输入、外部依赖和使用限制；实验能力不进入默认执行路径。

### 3. Agent 选择策略
优先顺序：
1. 精确匹配用户业务目标；
2. 检查当前项目和资料；
3. 检查 Capability 前置条件；
4. 选择可用 Profile；
5. 显示业务方案；
6. 需要执行时走现有 Confirmation。

只有没有合适预定义能力，且用户目标允许自定义时，才进入 Operator Catalog 动态组合。不能为了“智能”每轮都搜索全量 Operator。

### 4. 澄清策略
只询问影响业务结果的关键选择。对可由可信元数据确定的字段、单位、时间范围和默认参数，按现有规则准备并展示来源；不允许模型猜测缺失的科学参数。

### 5. Product UI
从 Capability Contract 生成首页业务场景入口、能力详情、输入要求和输出预览。不能让 UI 展示未装配或无权限的能力。

## 验收
- 常见业务需求优先命中预定义 Capability；
- 同一需求不会在无必要时反复 Catalog 搜索；
- 选择的 Profile、Model 和 Workflow 版本可追溯；
- 不满足前置条件时给出可操作解释；
- 高级动态组合仍受现有编译、白名单和确认约束。
