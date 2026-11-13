# 系统工程原子任务索引

| ID | 任务 | 依赖 |
|---|---|---|
| SYS-01 | 本地基线与激活版本核验 | 无 |
| SYS-02 | 架构边界与接口合同审计 | SYS-01 |
| SYS-03 | 缺陷台账与历史审计校准 | SYS-01, SYS-02 |
| SYS-04 | Agent 状态机与交互恢复回归 | SYS-01, SYS-02 |
| SYS-05 | 上下文、路由和工具调用边界验证 | SYS-04 |
| SYS-06 | Data 目录分页版本和查询链路 | SYS-01, SYS-02 |
| SYS-07 | Knowledge 检索证据读取和引用追踪 | SYS-01, SYS-02 |
| SYS-08 | Workflow DAG 合同和编译能力验证 | SYS-02, SYS-05 |
| SYS-09 | Executor 节点状态恢复与成果发布 | SYS-08 |
| SYS-10 | ConfirmationEditor 数据集分页与错误状态 | SYS-06 |
| SYS-11 | Workflow Draft DAG 展示与缺项交互 | SYS-08 |
| SYS-12 | 地图和图表查看器通用缺陷修复 | SYS-01, SYS-06 |
| SYS-13 | 结果查看比较和复用通用体验 | SYS-06, SYS-07, SYS-09 |
| SYS-14 | 分层回归性能与工程验收 | SYS-03 至 SYS-13 |
