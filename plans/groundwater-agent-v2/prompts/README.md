# Groundwater Agent V2.1 — AI 原生业务工作台优化提示词包

## 定位
本包不重新建设底层平台，而是围绕三个产品闭环推进：
1. WorkItem 成为用户组织业务工作的主要入口。
2. Agent 能在 WorkItem 授权范围内理解业务上下文，并安全地跨 Session 延续工作。
3. 数据、准备、执行、成果发布、审核与复用形成可恢复的业务链路。

## 使用顺序
先执行 `00_MASTER_CONTROL_PROMPT.md`，再逐阶段执行：
- `01_BASELINE_AND_STATUS.md`：最新本地代码基线和完成度复核
- `02_WORKITEM_FRONTEND.md`：WorkItem 前端产品化
- `03_WORKITEM_SESSION_CONTINUITY.md`：WorkItem 与 Session 连续工作
- `04_AGENT_WORK_CONTEXT.md`：Agent 业务上下文
- `05_DATA_AND_BUSINESS_OBJECTS.md`：数据与业务对象
- `06_RESULTS_AND_DELIVERY.md`：成果中心与交付
- `07_BUSINESS_CAPABILITY_EXPANSION.md`：业务能力扩展
- `08_WORKSPACE_INFORMATION_ARCHITECTURE.md`：工作台信息架构
- `09_END_TO_END_ACCEPTANCE.md`：端到端验收

专项提示词：`10_ARCHITECTURE_AND_API_REVIEW.md`、`11_OPERATOR_WORKFLOW_MAINTENANCE.md`、`12_UX_AND_ACCESSIBILITY_REVIEW.md`。

每次只执行一个阶段。所有状态必须基于当前本地代码、真实调用路径和实际测试更新到 `IMPLEMENTATION_STATUS.md`。

## 基线声明
本包参考一次对 GitHub main 提交 `90be0475` 的静态审查编写；不证明 Codex 当前本地工作区与该提交相同。实施前必须重新核实本地分支、HEAD、工作区和测试。
