# 阶段 4：WorkItem 与业务对象关联
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

将业务工作从 Session 中解耦，但不替代 Kernel Task，也不在 Kernel 复制 Data、Knowledge、Executor 的权威数据。

## 重点代码
- `source/kernel/src/platform_kernel/interaction/`
- `tasks/`
- `workflow_execution/`
- `api/routes/session_queries.py`
- `api/schemas`
- `source/web/src/types.ts`
- `use-project-sessions.ts`
- `TaskCard.tsx`
- `ProjectTaskHistory.tsx`

## 先决审查
必须明确：
- 当前 Project ID 的实际语义；
- Task 的持久化模型和交付条件；
- 一个 Session 是否已有一个 Task；
- 一个 Task 能否跨 Session；
- AgentRun/WorkflowRun 与 Task 的关系；
- 业务对象（Region/Well/Aquifer）当前是否有权威来源；
- Asset 是否只是精确资源引用投影。

若业务对象主数据所有者不清楚，第一阶段只实现 BusinessObjectReference，不建立监测井主数据系统。

## 目标对象
- WorkItem：用户长期可查找的业务工作容器；
- BusinessObjectReference：区域、监测井、含水层等精确引用；
- BusinessAssetReference：Data/Knowledge/Workflow/Result 精确资源引用；
- SessionLink / TaskLink：关联关系。

WorkItem != Task。WorkItem 是产品层业务组织单位；Task 是 Kernel 任务/交付状态事实。二者必须通过明确关联和投影协作。

## 实施任务
1. 设计 WorkItem 最小模型和生命周期。
2. 明确 owner、project scope、revision、幂等和并发策略。
3. 设计 Session、Task、Run、Asset 关联表。
4. 只保存精确引用和必要业务元数据。
5. 为纯咨询 Session 设计不强制创建 WorkItem 的规则。
6. 设计历史 Session 延迟迁移，不按标题猜业务任务。
7. 提供查询和审计 API。
8. 增加权限和跨项目隔离测试。

## 不做
- 不将 WorkItem 当作第二套 Task；
- 不复制 DataVersion 内容；
- 不删除或重写历史 Session；
- 不因关联 WorkItem 自动授予执行权限；
- 不强制所有普通咨询生成业务工作项。

## 验收
- 一个 WorkItem 可关联多个 Session；
- Session 归档不删除 WorkItem；
- WorkItem 归档不删除底层资产；
- 纯咨询会话正常工作；
- 历史关联迁移幂等；
- 资源权限仍由资源所有者服务校验。
