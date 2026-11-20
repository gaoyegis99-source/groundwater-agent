# Groundwater Agent V2 — Local Workspace Codex Prompt Pack

本提示词包专门用于 Codex 的**本地工作区模式**。

## 强制限制
- 只读写当前本地工作区。
- 不连接 GitHub/GitLab 等远程代码托管平台。
- 不调用远程 API、Connector、MCP 或远程代码搜索。
- 不执行 fetch/pull/push/clone、PR/Issue 或远程同步。
- Git 仅用于本地状态、分支、已有提交和 diff 检查。
- 不联网下载依赖；缺失项如实报告。

## 执行顺序
00 总控 → 01 本地基线 → 02 Operator → 03 Workflow → 04 Business Capability → 05 WorkItem → 06 生命周期/API → 07 Agent Runtime → 08 前端 → 09 E2E 发布。

`10_SPECIAL_REVIEW_PROMPTS.md` 可在阶段之间使用；`12_DECISIONS_AND_ADR_TEMPLATE.md` 用于记录决策。

## 使用方法
1. 将整个目录放入当前本地仓库，例如 `plans/groundwater-agent-v2/prompts/`。
2. 在本地 Codex 会话先加载 `00_MASTER_CONTROL_PROMPT.md`。
3. 每次只执行一个阶段提示词。
4. 每阶段检查本地 diff、运行本地测试并记录结果。
5. 阶段验收通过后再继续。

## 注意
本包是实施提示词，不代表代码已经修改。所有设计事实必须从当前本地工作区重新核实；不得假设本地分支与远程默认分支一致。
