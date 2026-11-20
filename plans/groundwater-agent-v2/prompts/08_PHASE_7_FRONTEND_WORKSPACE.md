# 阶段 7：React AI 原生工作台改造
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

将现有 Web 从 Session-first 体验渐进演进为 WorkItem-first 的业务工作台，保留对话、数据、参数、执行、结果查看器和 SSE 恢复能力。

## 重点代码
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
- `product-ui.ts`
- `types.ts`
- CSS、组件测试和 E2E

## 目标页面
- 首页：项目上下文、待处理事项、业务场景、AI 助手、最近工作；
- WorkItem 列表和详情；
- 工作项概览；
- 工作区：资料、方案、参数、执行、成果；
- 成果中心；
- AI 会话与历史会话。

## 任务

1. 先做无后端破坏的 UI 垂直切片：增加业务首页和 WorkItem 概览占位，复用当前 API 数据。
2. 后端 WorkItem API 可用后，再接入真实业务工作列表和详情。
3. Session Navigation 降级为 AI 助手历史，不删除。
4. 增加 Business Context Bar，显示当前项目、工作项和业务对象。
5. 支持从对话打开数据、参数、任务、地图和成果视图。
6. 支持从地图/表格选中对象后，将焦点传递给对话。
7. 业务方案视图与专业 DAG 视图读取同一冻结 Workflow。
8. 普通用户隐藏技术 ID，专业详情仍可核对精确版本和参数。
9. 结果卡区分计算、发布、诊断和业务审核状态。
10. 保留现有 SSE + snapshot、AbortController、request ID 和断线恢复机制。

## 状态管理
分离服务端权威状态、页面状态和未提交编辑状态。切换项目/WorkItem 时取消旧请求并清除旧作用域缓存。不要让本地 sessionStorage 变成业务事实或权限来源。

## 验收
- 旧 Session 路由可用；
- 首页和 WorkItem 导航可用；
- 桌面和窄屏核心路径可用；
- 对话与工作台双向联动；
- 地图对象选择不会误触发执行；
- 切换作用域不显示旧数据；
- 断线恢复从服务端快照校正；
- 所有按钮有真实动作且服务端再次校验权限。
