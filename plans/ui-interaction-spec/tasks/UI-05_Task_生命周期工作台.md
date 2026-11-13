# UI-05：Task 生命周期工作台

## 目标
展示目标、修订、criteria、business steps、blockers 和 review。

## 前置依赖
UI-02, UI-03, UI-04

## 源码范围
- `TaskCard.tsx`
- `WorkspacePanel.tsx`
- `Task API/types`
- `chat-state.ts`

## Codex 执行步骤
区分 lifecycle/state；原目标与当前目标分开；Task steps 不混 AgentPlan/DAG；无 Task 不渲染空卡。

## 测试命令
```bash
Task selector/Workspace tests。
```
执行前核实本地 package scripts、pytest 配置、执行手册和环境，不得猜测不存在的命令。

## 验收标准
普通咨询无 Task；criteria 未满足不显示全部完成；历史修订只读。

## 完成报告
本地 HEAD、初始工作区、真实合同证据、当前缺口、修改文件、测试命令/退出码/统计、未运行项、风险、progress 更新。
