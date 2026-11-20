# 专项审查：架构、API、状态和数据所有权

对 WorkItem、Session、Task、AgentRun、WorkflowRun、Result 的权威关系进行审查。检查生命周期是否由真实事实推导；资源引用在写入和读取时是否验证；API 是否重复表达状态；OpenAPI/SDK/TS/Ajv 是否一致；requestId、revision 和并发重试是否安全；migration 是否有摘要、备份、恢复；SSE 是否有丢失、重复、乱序和 cursor 回退风险；前端是否跨用户/项目缓存；是否存在绕过 Data/Knowledge/Executor 权威 API 的数据库读取。

每项标记 PASS/RISK/FAIL/UNKNOWN。FAIL/RISK 必须给复现条件、影响、最小修复和回归测试。不要因此自动重写模块；涉及所有权或权限变化先写 ADR。
