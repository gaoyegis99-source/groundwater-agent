# 原子任务索引

| ID | 任务 | 依赖 |
|---|---|---|
| UI-01 | 四对象合同审计 | 无 |
| UI-02 | 状态投影与 Selector | UI-01 |
| UI-03 | Session 导航与研究列表 | UI-01, UI-02 |
| UI-04 | Turn 时间线 | UI-01, UI-02 |
| UI-05 | Task 生命周期工作台 | UI-02, UI-03, UI-04 |
| UI-06 | 数据准备交互 | UI-01, UI-02, UI-05 |
| UI-07 | 方案摘要与技术详情 | UI-01, UI-02, UI-05, UI-06 |
| UI-08 | Confirmation 修订与审批 | UI-01, UI-02, UI-06, UI-07 |
| UI-09 | WorkflowRun 时间线与节点 | UI-01, UI-02, UI-05, UI-08 |
| UI-10 | 失败停止与恢复 | UI-08, UI-09 |
| UI-11 | 成果详情与评审 | UI-01, UI-02, UI-05, UI-09 |
| UI-12 | 视觉与响应式 | UI-03 至 UI-11 |
| UI-13 | 端到端生命周期验收 | UI-01 至 UI-12 |
