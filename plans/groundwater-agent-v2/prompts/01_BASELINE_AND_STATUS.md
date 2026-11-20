# 阶段 0：最新本地代码基线与实施状态复核

本阶段只审计，不改生产代码。

## 必查区域
- Kernel：`work_items/`、`api/routes/work_items.py`、`work_item_schemas.py`、`work_item_lifecycle_facts.py`、SQLite migration、reference authority、SSE。
- Web：`App.tsx`、`SessionNavigation.tsx`、`ProjectTaskHistory.tsx`、`WorkspacePanel.tsx`、`work-item-api.ts`、`work-item-stream.ts`、`product-ui.ts`、`ProductDrawers.tsx`、`TurnTimeline.tsx`、Viewer。
- Agent：`interaction/message_requests.py`、`business_capabilities.py`、`preparation_tool.py`、`result_processing.py`、context construction、tool scope、trace、confirmation、recovery。
- Data/Knowledge/Executor：版本、语义、质量、发布、执行、named outputs 和结果回读。
- 对应 tests、OpenAPI、SDK、validators、Assembler 生成配置。

## 必须回答
1. WorkItem 前端实际接入哪些 API？哪些 API 存在但 UI 无调用？
2. 创建、关联 Session、切换、详情的调用链是否完整？
3. 生命周期快照与 SSE cursor 如何对齐、重连和 reset？
4. Agent 每轮是否知道当前 WorkItem？来源、作用域和权限校验在哪里？
5. 跨 Session 延续哪些上下文？哪些没有？
6. 业务状态由哪些 Task/Preparation/Confirmation/WorkflowRun/Publication/Review 事实推导？
7. Result Contract 如何映射到 UI、质量、发布和审核？
8. Capability 匹配如何处理歧义、否定、缺资料、单位冲突？
9. 当前 owner/role 模型能否支撑协作？不要擅自扩大权限。
10. 哪些测试是单元、合同、集成、E2E？哪些只是静态验证？

## 状态定义
Backend/API/Frontend/E2E/Product 分别记录：
ABSENT / PARTIAL / IMPLEMENTED / VERIFIED / ACCEPTED。
IMPLEMENTED 表示代码路径存在；VERIFIED 必须有本地实际测试证据；ACCEPTED 必须有产品或领域验收证据。

## 输出
新增或更新 `plans/groundwater-agent-v2.1/IMPLEMENTATION_STATUS.md`，记录本地 branch/HEAD/status、架构调用链、完成度矩阵、EXISTING/EXTEND/NEW/DECISION/DEFER、风险、优先级、源码与测试证据、未运行验证。
不得只凭文档标记完成。无生产代码改动。
