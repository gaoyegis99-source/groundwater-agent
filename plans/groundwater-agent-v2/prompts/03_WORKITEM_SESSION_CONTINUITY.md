# 阶段 2：WorkItem 与 Session 连续工作体验

## 用户目标
一个长期业务工作可以包含多次会话。用户隔天回来，能继续同一工作，不必复制聊天记录或重述全部背景。

## 先核实
WorkItem↔Session 约束、Session owner/project 校验、Task 与 Session 唯一关系、AgentRun/WorkflowRun 权威归属、detail/lifecycle/activity 投影，以及 Web SessionNavigation 和 ProjectTaskHistory。

## 产品行为
1. 从 WorkItem 内新建 Session，成功后明确关联当前 WorkItem。
2. 加入已有 Session 前，由服务端确认主体、项目和唯一归属。
3. 打开 Session 时恢复 history、confirmation、preparation、draft、results 和 stream。
4. 离开再回来，恢复当前 WorkItem、详情和最近活动。
5. 新 Session 继承授权业务上下文，不继承前一 Session 的全部消息。
6. 不允许静默移动已归属其他 WorkItem 的 Session。
7. 冲突时明确说明并提供安全替代路径。

## 上下文继承
可继承：WorkItem 标题/说明、用户明确关联的业务对象、仍可访问的 Data/Knowledge 引用、未完成 Task 摘要、已发布成果精确引用、用户确认的目标和参数。
默认不继承：全部旧消息、过期/撤权资源、未确认推断、旧 Confirmation、旧 AgentRun 内部状态、无来源自由文本参数。
每项继承内容标明来源、时间和版本。

## 工程约束
Kernel 负责关联合法性和权威快照。UI 状态可恢复但不能作为授权依据。API 变更同步 OpenAPI、SDK、TS、Ajv、文档和测试。

## 测试
多 Session；Session 已归属其他工作；归档；新建；刷新/重新登录；项目/用户切换；资源失效；并发更新；SSE 断线。

## 验收
完成“在当前工作项继续/新建会话”旅程，证明不破坏现有 Session、Task 和执行历史。
