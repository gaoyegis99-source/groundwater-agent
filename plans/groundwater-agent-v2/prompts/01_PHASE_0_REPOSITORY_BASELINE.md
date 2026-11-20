# 阶段 0：仓库基线与架构核查
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


将 `00_MASTER_CONTROL_PROMPT.md` 作为全局约束。本阶段只审计和设计，不修改生产代码、不迁移数据库、不改 Domain/Operator/Workflow 版本。

## 目标

建立 Groundwater Agent V2 的真实实现基线，核对此前产品设计与当前代码差异，识别重复建设风险、缺失合同和阶段依赖。

## 必须检查

### 产品与 Web
- `source/docs/capabilities/webui.md`
- `source/web/src/App.tsx`
- `SessionNavigation.tsx`
- `TurnTimeline.tsx`
- `WorkspacePanel.tsx`
- `TaskCard.tsx`
- `ChatCards.tsx`
- `AssistantContent.tsx`
- `use-workspace-controller.ts`
- `use-session-stream.ts`
- `ui-selectors.ts`
- Product UI schema/config 和实际装配产物

### Kernel
- `source/kernel/src/platform_kernel/interaction/`
- `agent_runtime/`
- `tasks/`
- `workflow_execution/`
- `application/operator_catalog.py`
- API routes、schemas、persistence 和相关 tests

### Workflow / Operator / Domain
- `source/packages/operator-pack/README.md`
- Catalog index、每个 Operator Manifest、Smoke 和 implementation lock
- `source/packages/workflow-compiler/` 实际目录
- 活动 Groundwater Domain Manifest
- 全部 Workflow、Executable Model、Knowledge Model Cards
- Assembler 对 Domain、Operator Catalog、Workflow、Model 的装配和身份校验逻辑
- `source/products/groundwater/` 中能力组合和 UI 声明

### 既有设计与评估
- `plans/repository-assessment/09_TARGET_ARCHITECTURE.md`
- `10_REFACTORING_ROADMAP.md`
- `source/docs/capabilities/webui.md`
- 已存在的 Product/Domain/Workflow/Operator 设计文档

## 必须回答的问题

1. 当前活动 Product 和 Domain 版本分别是什么？由何处选择？
2. 当前 Operator Catalog 的真实算子 ID、版本、运行时数量是什么？区分 manifest 条目、唯一 ID 和版本数。
3. Workflow、Executable Model、Operator、Composition Contract 的实际关系是什么？
4. 是否已有 Workflow Profile、Capability Profile 或等价机制？不能仅凭目录名判断不存在。
5. 哪些 Workflow 是核心、专题、高级、实验？根据现有证据分类，证据不足标记 UNKNOWN。
6. Operator validation/scientific review 证据如何产生、保存、失效和被消费？
7. Workflow Compiler 已经检查哪些内容？缺哪些质量检查？
8. 结果读取、发布、诊断和 UI 解释链路在哪里？
9. Session、Task、AgentRun、WorkflowRun、Result 的实际权威关系是什么？
10. 已有计划与代码之间哪些已实现、部分实现、未实现、文档漂移？

## 输出

新增或更新一份审计文档（建议 `plans/groundwater-agent-v2/00_BASELINE_AND_GAPS.md`），至少包含：
- 当前架构图；
- 文件/模块责任表；
- Operator/Workflow/Model 统计口径和脚本；
- EXISTING / EXTEND / NEW / DECISION 矩阵；
- 风险清单和优先级；
- 关键接口和测试命令；
- 阶段依赖图。

## 验收

- 每个结论有实际代码路径或测试支撑。
- 统计数字由脚本/当前文件产生。
- 不把历史评估数字说成当前状态。
- 没有生产代码改动。
- 明确列出需要产品/架构负责人决策的问题。
