# 阶段 1：WorkItem 前端产品化

## 用户目标
用户打开产品后能看到自己正在处理的业务工作，而不是只能从 Session 列表猜测进度。可以创建、打开、查看状态、进入会话并恢复进度。

## 先核实
Kernel 当前 WorkItem API、OpenAPI、生成客户端、`work-item-api.ts`、`work-item-stream.ts`、App、SessionNavigation、ProjectTaskHistory。不要臆造路径或字段。

## 交付体验
### 列表
展示标题、说明、业务状态、最近更新时间、关联 Session 数量、待办/下一步动作（仅有权威事实时）、归档状态。支持创建、分页、打开、归档和错误重试。

### 创建
标题必填、说明可选。创建成功后按服务端回执导航。requestId 对同一动作保持稳定。超时先安全重放/查询，不得盲目重复创建。

### 详情
分区：概览、当前进展、会话、资料与业务对象、成果、活动、可用操作。显示业务语言；内部 ID/checksum 仅在专业详情中显示。

## 工程约束
- 新增清晰页面/组件，不把全部逻辑继续塞进 App.tsx。
- 当前 WorkItem UI scope 绑定 product/project/subject；切换身份时清理旧状态。
- 请求支持 AbortSignal、竞态保护、错误分类和重试。
- 写操作使用 requestId、expectedRevision。
- SSE 是通知/增量信号，必须通过快照/活动 API 恢复权威状态。
- 乐观 UI 不得伪造业务状态。
- 处理 403/404/409/422/429/5xx、网络断开、空状态和 stale 数据。

## 测试
列表分页；创建重复提交和超时恢复；多 Session/资产详情；revision 冲突；身份切换；SSE 重连、重复、乱序、cursor 回退和 reset；键盘与焦点；不可用引用。

## 验收
用户可创建 WorkItem、进入详情、看到权威状态、打开关联 Session，刷新后仍能恢复。现有 Session 入口保持兼容。
