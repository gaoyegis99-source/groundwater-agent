# 阶段 5：成果中心、业务审核与成果复用

## 用户目标
用户不仅看到 Agent 回复或下载文件，还能查看正式成果、理解质量与限制、审核交付，并将成果作为后续分析的精确输入。

## 先核实
Result Contract 精确版本和输出；WorkflowRun named outputs；Executor 状态；Data publicationId/state；WorkItem Result reference；Task RESULT/USER_REVIEW；review token、revision 和 API；Web ResultCard/Viewer/ResourceLink/continuation。

## 状态必须分开
执行未开始、执行中、执行失败/取消/超时、执行成功但发布待处理、发布失败、已发布待审核、审核等待、已接受/完成、成果不可用/权限撤销。具体枚举以当前合同为准。

## 成果展示
业务名称、用途、输出角色、Viewer、输入资料版本、关键参数和单位、Workflow/Profile/Model 版本（渐进披露）、执行时间、发布状态、质量诊断、限制、来源追溯、Task 采用情况和审核状态。

## 审核
只有 Kernel 提供有效 review candidate/token 时才显示操作。提交携带 requestId、expectedRevision、sessionId 和服务端 token。审核前重新校验结果可见、属于当前 WorkItem/Session 且已发布。token 过期或结果变化时要求刷新。打开结果不等于接受结果。

## 复用
只允许已发布且仍可访问的精确结果。必须保留 outputName、publicationId、DataVersion identity，不能只传 runId。跨 Session 复用须重新经过权限和引用校验。

## 测试
publication pending/failed/unavailable、输出缺失、Contract mismatch、权限撤销、token stale、revision conflict、重复审核、结果续接、跨 Session/WorkItem 隔离、UI 不混淆执行与交付。

## 验收
WorkItem 成果中心、Viewer、审核和精确结果复用路径可用，并有执行—发布—审核—复用集成测试。
