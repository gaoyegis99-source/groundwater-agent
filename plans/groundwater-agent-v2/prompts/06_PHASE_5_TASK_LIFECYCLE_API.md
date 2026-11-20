# 阶段 5：业务任务生命周期、API 与数据库
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

将 Kernel 的技术状态投影为用户可理解的业务状态，提供兼容、可恢复的 WorkItem API 和事件。

## 重点代码
- `kernel/tasks/`
- `interaction/`
- `workflow_execution/`
- `api/routes/`
- `api/schemas/`
- API v12 OpenAPI 和生成客户端
- migrations / database setup / tests
- Web `types.ts`、generated API types、Ajv validators

## 状态要求
定义 DRAFT、READY、WAITING_INPUT、WAITING_REVIEW、WAITING_CONFIRMATION、EXECUTING、RESULT_REVIEW、BLOCKED、COMPLETED、CANCELLED、ARCHIVED。
先核对现有 Task 状态合同，必要时采用投影字段而非修改旧枚举。
技术事实映射必须明确：
- Confirmation PENDING 不等于执行中；
- WorkflowRun SUCCEEDED 不等于成果发布完成；
- 发布完成不等于业务验收完成；
- STOP 请求受理不等于已停止；
- 用户拒绝不等于计算失败。

## API 实施
按现有 v12 约定新增 WorkItem 查询、创建、更新、关联 Session、关联 Asset、活动记录和归档接口。写入使用命令式 endpoint 或明确的状态转换，不允许客户端直接 PATCH 任意状态。

## 数据库要求
- migration 可重复验证；
- 幂等键；
- revision 乐观锁；
- 项目和 owner 索引；
- 事件记录；
- 软归档；
- 回滚和恢复说明；
- 不复制服务外部数据。

## 合同同步
服务端模型、OpenAPI、生成客户端、前端类型、Ajv standalone validators、文档和测试同步更新。新增字段默认兼容旧客户端。

## 验收
- 状态迁移表测试；
- 事件乱序和重复测试；
- 权限和跨项目测试；
- 幂等和并发冲突测试；
- 旧 API 客户端回归；
- 数据库迁移 dry-run/回滚；
- SSE 事件与查询快照最终一致。
