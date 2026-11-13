# Task UI Contract
用户心智：一个持续科研交付目标。字段需按真实 API 核验：original objective、current objective、goal revisions、scope、constraints、criteria、business steps、unresolved items、blockers、lifecycle/state、results、linked WorkflowRuns。
普通咨询不建 Task；沿用一个 Session 最多一个主 Task 的当前合同。
Task Steps != AgentPlan != Executor DAG。完成条件未全部满足时不能显示全部完成。禁止前端百分比估算。
