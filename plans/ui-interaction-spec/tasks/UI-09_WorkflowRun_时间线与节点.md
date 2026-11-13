# UI-09：WorkflowRun 时间线与节点

## 目标
展示多次运行、节点状态、attempt 和成果归属。

## 前置依赖
UI-01, UI-02, UI-05, UI-08

## 源码范围
- `WorkspacePanel.tsx`
- `WorkflowNodeDetails.tsx`
- `Executor status APIs`
- `use-session-stream.ts`

## Codex 执行步骤
区分 Task steps、AgentPlan、WorkflowRun、Executor node；状态来自持久化事实；显示关联确认和输入快照。

## 测试命令
```bash
Executor/Kernel projection tests；SSE reconnect tests。
```
执行前核实本地 package scripts、pytest 配置、执行手册和环境，不得猜测不存在的命令。

## 验收标准
多次运行不串结果；刷新后与快照一致；无假进度。

## 完成报告
本地 HEAD、初始工作区、真实合同证据、当前缺口、修改文件、测试命令/退出码/统计、未运行项、风险、progress 更新。
