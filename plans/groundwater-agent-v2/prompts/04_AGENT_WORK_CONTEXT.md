# 阶段 3：Agent WorkItem 业务上下文与安全装配

## 用户目标
用户在某 WorkItem 内提问时，Agent 能理解业务目标、当前进度和可用资料，提出合适下一步；不能把不相关 Session 历史混入当前工作。

## 上下文分层
L0 请求必需：鉴权 subject/roles、projectId、sessionId、WorkItem ID（若有）、requestId、goalRevision、当前意图和权限决策。
L1 当前业务状态：WorkItem 标题/说明、生命周期快照、当前 Task、Preparation blocker、pending Confirmation、Execution/Publication 状态。
L2 按需资料：明确关联业务对象、可访问 DataVersion/SemanticRevision、Knowledge version、已发布 Result、用户选定 Capability/Profile。
L3 历史摘要：仅当前目标需要时检索其他关联 Session 摘要。标注来源 Session、时间、版本和事实/用户陈述/模型推断类别。摘要不是权威状态。

## 必须遵守
- WorkItem 关联不自动授予资源读取权限。
- 所有资源读取通过 owner service 重新校验。
- 只读意图不得提供写工具。
- 状态由 Kernel 权威投影，模型不能生成伪状态。
- 区分当前 Session 状态与 WorkItem 汇总状态。
- 不将全部历史消息/结果字节塞进 prompt。
- 明确 token/字节预算、优先级、截断和可观测指标。
- 权限撤销、项目切换、归档或资源不可用时剔除上下文。
- 不暴露隐藏推理、系统提示词或凭据。

## 实施前
先给出 context schema/装配流程提案、复用机制表和风险。若需要持久化摘要，先明确 owner、版本、过期、撤销和审计；不得未经决策增加存储系统。

## 测试
跨 Session 不串数据；跨 WorkItem/主体/项目隔离；撤权资源剔除；历史语义版本；归档；无 WorkItem 兼容；只读工具范围；prompt injection；超预算降级；trace 只记录来源元数据。

## 验收
WorkItem context assembly 可运行、可追溯、限量，旧 Session 兼容，安全边界有测试。
