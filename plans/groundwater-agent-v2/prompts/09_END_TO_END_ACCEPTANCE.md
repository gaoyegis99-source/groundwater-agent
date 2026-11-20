# 阶段 8：完整业务旅程 E2E 与发布验收

## 主旅程
1. 登录并选择项目。
2. 创建 WorkItem。
3. 在 WorkItem 内创建 Session。
4. 用自然语言描述业务需求。
5. Agent 在当前授权范围理解目标。
6. 发现/上传/选择资料。
7. Data 检查精确版本、字段语义、单位、时间范围、CRS 和质量。
8. 缺失项结构化澄清。
9. 用户补充资料/参数。
10. Preparation 重新验证并 READY。
11. 用户确认精确执行方案。
12. Executor 执行 Workflow。
13. 分别展示执行和发布状态。
14. 用户查看结果、质量、限制和来源。
15. WorkItem 显示待审核。
16. 用户审核接受。
17. 新 Session 基于同一 WorkItem 和已发布成果继续。
18. 刷新、断线重连、重新登录后状态正确。
19. 归档后仍可追溯历史。

## 失败旅程
Data/Knowledge 不可用、Worker down、Provider timeout、上传中断、Preparation blocked、Confirmation stale、Workflow failed/cancelled/timed out、Publication pending/failed、Result unavailable、Review token stale、revision conflict、SSE reset/reconnect、权限撤销、项目切换、WorkItem 归档和 migration 恢复。

## 安全验收
跨项目/主体访问拒绝；未关联 WorkflowRun 不可挂入；未发布 Result 不可作为正式成果；撤权资源不泄露；viewer 无写操作；前端伪造状态无效；Agent tool scope 匹配意图；不能通过提示词绕过确认或资源校验。

## 指标
WorkItem 创建到首个 Session 成功率、恢复成功率、Capability 选择/澄清率、Preparation 一次通过率、Confirmation 后执行成功率、执行到发布成功率、发布到用户接受率、结果复用率、SSE 恢复时间、拒绝测试结果、完成旅程步骤数和等待时间。

## 验收报告
提供 E2E 测试、失败场景、安全矩阵、性能/观测指标、发布阻断项。没有真实领域数据或专家评审时，不得宣称科学有效。
