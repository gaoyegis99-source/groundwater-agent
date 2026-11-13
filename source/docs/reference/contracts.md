# v12 协议与持久化

本文记录 Groundwater V12 的协议和状态所有权。科研验收条件见[科研验收](../operations/acceptance-gates.md)。

| 接口或存储 | 当前版本 |
| --- | --- |
| Public HTTP | v12 |
| Product Manifest / Product UI / Module Contract / Assembly Lock | v8 / v1 / v9 / v8 |
| Kernel HTTP / Config / SQLite | v12 / v8 / v34 |
| Session Event / IntentDecision / message request | v20 / v4 / v5 |
| AgentFinal / SubmittedReply | v5 / v6 |
| Workflow / Executable Model | v6 / v6（当前产品）；装配仍接受 Executable Model v4、v5 |
| JobRequest / JobStatus | v6 / v6 |
| Executor HTTP / Worker / SQLite | v6 / v3 / v9 |
| Data HTTP / Tools / PostgreSQL | v3 / v4 / schema v8 |
| Knowledge HTTP / Tools / PostgreSQL | v3 / v5 / schema v7 |

Gateway 公开的 Kernel、Data 和 Knowledge Schema 由各服务 OpenAPI 汇编为一个
Public API v12 合同；Web 类型从该合同生成。已知边界错误统一使用 RFC 9457
`application/problem+json`，以顶层 `code` 作为稳定机器错误分类，请求校验位置使用
JSON Pointer。Problem `type` 的 URN 命名空间由装配的 `productId` 生成；通用服务不保存
产品常量，当前 `groundwater` 产品仍保持既有 `urn:groundwater:problem:*` wire 身份。

## 静态声明

Assembler 生成七个产物：`kernel-config.json`、`executor-config.json`、`compose.json`、
`nginx.conf`、`assembly-lock.json`、`product-ui.json` 和
`product-icon.svg`。Kernel Config 只包含可调用模型投影、只读工具和草稿可用算子；Executor
Config 独占完整 Workflow DAG。Product UI 只包含产品拥有的交互展示投影，不进入 Kernel、
能力服务或 Domain 运行时。Assembly Lock v8 冻结统一 Gateway 与服务装配摘要。
声明身份与 checksum 冻结后不可变，变更必须发布新版本。

Data 与 Knowledge 不发布宿主机端口。统一 Gateway 同时提供研究页面与 `/api/v12/*`，默认仅监听 loopback；内部 tools、health 和 OpenAPI 路径不直接公开。Kernel 使用本地会话或 API Token 校验身份，再向内部服务传递项目角色；前端权限显示来自 capabilities，服务端授权仍是最终边界。

Product `publicRoutes.prefix` 只接受规范的 `/api/v12/<segment>` 路径，禁止尾斜线、
空段、点段及 Nginx 元字符；Kernel 的 sessions、agent/workflow runs、health、
capabilities、ready、audit，以及根页面、assets 和 Product UI 精确静态路径均不可被产品服务
覆盖。路由声明在写入 Nginx 前完成这项信任边界校验。

## Data 与发布

`DataVersionRefV2` wire 字段为 `datasetId/versionId/semanticId/objectId/mediaType/format/sizeBytes/checksum`；命名输出增加 `outputName`。Python 和数据库内部继续使用 snake_case。Data 在发布边界校验格式、MIME、对象大小和 checksum，并拥有 Dataset、Version、Object、Semantics、发布组、审计和 lineage。

Executor 只接受冻结的 Data 引用。读取输入时再次核验 Dataset/Version/Object/checksum，并从该精确版本及其绑定语义/转换回执形成受限的 `dataContract` 执行 metadata（命名时区、空值、物理类型和字段 ID/名称映射）。metadata 与原始对象检查点共同持久化，不改变 `DataVersionRefV2` wire 字段，也不以 query 对象替换冻结的 raw 对象。每个 WorkflowRun 固定一个幂等发布组，所有最终输出在一个事务中公开；响应丢失时查询或重放同一发布组。内容改变的重放冲突，完全相同的重放返回原回执。成功输出的 lineage 必须精确指向输入版本。

声明为 `json` 的 Data 发布在 READY 前必须通过有预算的 UTF-8、语法、重复键和有限数值校验；`FILE_ONLY` 不推断业务语义，但仍验证声明格式。任意不透明字节使用 `binary`。

`field-profile.v1` 的 `sampleValues` 和 `valueRange` 保留原有 JSON 数值。读取 `int64` 或
`uint64` 字段画像时，Data 对超出 JavaScript 安全整数范围的值附加可选 `exactIntegers`。
其中 `sampleValues` 按原样例索引对齐，安全位置为 `null`。`minimum` 和 `maximum` 各自只在
对应端点超出安全范围时出现。精确值以十进制文本传输，读取时从已保存的画像生成，适用于已发布
版本及新版本。两套 Web 优先显示这些文本，旧服务缺少对应文本时标注精度不可确认。

Data Query 的表格白名单仍为 CSV/Parquet。XLSX 与 GeoPackage 使用独立的幂等 Preview 操作，结果有有效期并保存到对象库；解析在受限 Worker 中完成，冻结并核验 `version_id/semantic_id/object_id/checksum`。Preview 不执行公式、宏、外部链接或客户端 SQL。

Data schema v8 将 Publication、Query 和 Preview 的可变事务绑定到
`owner_subject`，幂等键作用域为 `project + owner_subject`；跨主体读取、上传、准备、
提交、中止或取消统一按不存在处理。`researcher` 的 Publication 只能创建单文件、全新
Dataset 的 `USER_UPLOAD`，不能声明目标 Dataset、lineage、转换、指标或管理语义；完整
`MANAGED` 发布只允许 admin 与受信任内部服务。Session 与事务所有权是私有边界，
已提交的 Dataset/Model 只保留 owner 和 `PRIVATE/PROJECT`：owner 可修改自己的资源，admin
可管理项目全部资源，`PROJECT` 对项目成员可读，`PRIVATE` 只允许 owner/admin。不存在资源级
grant、READ/WRITE/ADMIN ACL 或 revision CAS；资源读取无权时按不存在处理，避免枚举私有资产。

质量控制面保存不可变 `QualitySuiteVersion`、修订化 `QualityBinding`、绑定精确
Data Version/SemanticRevision 的 `QualityRun`、完整 `RuleResult` 和 `QualityAlert`。规则正文直接
投影成 `table-validation-rules.v1`，验证仍由 `table.validate@1.0.0` 执行；提交结果必须完整覆盖
全部规则，严重级别只能来自冻结套件，不能由调用方改写。发布、质量告警和模型生命周期事件在
业务事务内写入 outbox，外部 OpenLineage/webhook/MLflow 发送失败只积压并可幂等重放。

实验运行冻结输入 Data Version、模型别名解析结果、算子实现摘要、参数、指标和环境摘要。模型
Registry 只引用 Data 中不可变的 `pytorch-state-dict` 对象，以 immutable experiment ID、tags、
`benchmarkStatus=NOT_ASSESSED|PASSED|FAILED` 和非授权性 alias 组织并列实验。旧 stage 只保留为
`legacyStage`/历史标签，不自动推断 benchmark 通过；执行记录继续冻结解析时的 registry version、
Data Version、object 和 checksum，之后修改标签不会改写历史运行。

## Knowledge 证据

Knowledge Next 拥有不可变知识版本、Evidence Unit、生命周期、索引和检索回执。引用身份为 `version_id/unit_id/checksum`。数据绑定精确包含 `data-primary/project_id/dataset_id/version_id/semantic_id`。Kernel 只接受当前 AgentRun 中完整读取且状态为 `OK` 的证据；错误版本、错误语义、失效或过期证据不能进入 AgentFinal。

Knowledge schema v4 在导入任务和每条生命周期修订上保存受信任 actor（主体、角色、
trace/request ID）；v5 增加人工重试代次、尝试基线和幂等请求记录；v6 曾加入细粒度 ACL，
v7 已将它收敛为 owner、PRIVATE/PROJECT 与项目 `admin/researcher/viewer` 三角色。目录、检索、
证据读取、生命周期、回执和内部工具都在服务自己的存储边界执行同一项目策略；不存在
SUBJECT/ROLE grant、READ/WRITE/ADMIN 资源权限或资源级 revision CAS。

## Agent、确认与执行

Intent Router 只判断语义与任务动作；Kernel 负责模式、控制权限和持久目标。Confirmation、AgentAction、WorkflowRun 与 JobRequest 分别保存业务参数和冻结 `input_references`。模型输出不能创建执行事实。

工具处理进度采用三方声明协议。Agent 每次调用受管工具时必须提供
`AgentProgressIntent(capability, objective, template_key)`，只声明本次要完成的具体目标，不能声明
成功或失败，也不携带业务参数。Data、Knowledge 和 Kernel 内置工具各自拥有
`ToolProgressDefinition`，其中冻结标题、运行摘要、结果处理摘要、语义角色、允许的结果码、
显示安全 facts schema 和结果摘要模板。工具完成后只返回显式
`ToolOutcome(outcomeCode, facts)`；Kernel 校验结果码和 facts 后渲染并持久化 Activity，随后使用
同一个 Agent objective 与工具声明的处理模板投影“筛选候选项”“核对版本事实”等下一阶段。
Agent 的 objective 只通过服务拥有的模板进入摘要，不作为
独立原始字段保存；业务响应中的 `status`、
`items`、`summary` 等字段不参与进度推断。重试、重复读取、预算耗尽和分页收敛只读取 Kernel
定义的控制信封。Web 原样展示 Activity 的 `title/summary`，不根据工具名、结果字段或回复内容
二次匹配文案。

`TaskPreparationV1` 是 Kernel 拥有的紧凑准备投影，按 AgentRun 保存一个当前 head 和不可变修订。head 的 `entries` 对应本次提案的一个或多个目标动作；每项包含目标身份、版本选择策略、精确 Data 引用、本轮 Knowledge 引用、参数及来源、检查、兼容的字符串 `blockingReasons`，以及供交互动作使用的结构化 `blockingIssues`。结构化项明确区分缺数据、缺参数、非法参数、未解决事项和非当前版本，并携带输入/参数名或 JSON Pointer；Web 不从错误文案猜测字段。集合状态由各项状态汇总，`disposition` 由 AgentRun、Confirmation、AgentAction 和 goal revision 派生，不建立第二份可写生命周期。Session、消息历史与 Agent 上下文读取同一记录；目标 goal revision 改变后 `fresh_for_current_turn=false`。Data/Knowledge 目录和正文仍由各自服务拥有，不写入 Kernel 的统一状态表。

MessageRequest v5 接受最多 20 个有序 `DataVersionRefV2` 输入引用。正文仅保存用户文字；引用单独进入 Router/Agent 的 Kernel 事实上下文，并投影到消息历史。相同请求 ID 修改引用会发生幂等冲突。带引用的控制词按普通任务请求处理，避免把附件分析误判为控制命令。

保存确认修订时，action 可携带 `expectedInputReferences`，以模型数据输入字段为键，值为选择时取得的精确 `DataVersionRefV2` 原始对象身份。Kernel 将它与 Data 当前返回的身份核对。字段或身份不一致时返回 `409 CONFIRMATION_DATA_VERSION_INVALID`，原批次仍可见。省略该字段沿用 v12 既有处理，旧请求的幂等指纹保持兼容。保存成功生成新的待确认批次，执行需要批准该批次。

`EXECUTION_PROPOSAL` 只有两种合法终态：已持久化可执行 Action 并进入 `WAITING_CONFIRMATION`；或返回包含真实缺项的回复。没有 Action 且没有缺项的成功声明以 `AGENT_MODEL_OUTPUT_INVALID` 拒绝。`include_execution_status`、WorkflowRun 引用和确认状态都根据持久事实校验。

用户确认后 Kernel 创建 WorkflowRun，Executor 根据 Execution Protocol v6 运行静态 DAG。Worker 使用租约与心跳；成功节点形成 checksum 地址的检查点。恢复、取消、超时和发布均以持久状态仲裁。

`GET /api/v12/workflow-runs/{runId}/nodes?projectId=…` 按 WorkflowRun 所有者授权。Kernel 用冻结的 Executor 地址和远端作业 ID 读取节点标识、算子、运行环境、状态、尝试次数、时间和稳定错误码。`NOT_DISPATCHED` 表示尚无作业 ID，`UNAVAILABLE` 表示作业记录或绑定不可用，`AVAILABLE` 表示已读取节点列表。旧 Executor 未提供尝试次数时，`attemptCount` 为 `null`。临时上游故障返回 `503 WORKFLOW_NODE_DIAGNOSTICS_UNAVAILABLE`。参数、远端状态、租约和原始日志留在 Executor 的诊断边界。`JobStatusV6` 保持原有字段。

## Provider 请求与可用性

每个 Router 或 Agent 逻辑阶段最多允许一次同 Provider、同模型协议修复，只处理 JSON、tool-call、exact-literal 或 schema 错误。网络、HTTP、超时、上下文预算、业务工具和执行错误不重试。Provider 请求表记录阶段、Provider、模型、协议、尝试序号、结果、错误分类、Token 和时延。

Agent 与 Router 可以各自配置一个同 Provider、同模型备用 base URL。Kernel 使用 `/v1/models` 做零生成 Token readiness 探测，并缓存成功 15 秒、失败 5 秒。调用开始前选择首个可用端点；真实调用的网络、超时、认证或服务错误会为后续请求熔断该端点。当前失败调用不切换、不重放，也不跨 Provider 或模型回退。

## 健康合同

- `/internal/health/live`：单组件进程存活。
- `/internal/health/ready`：单组件本地依赖就绪。Data 包含 PostgreSQL 和对象卷；Knowledge Next 包含 PostgreSQL/pgvector 和 Embedding；Executor 包含 Control 与全部配置 Worker 的新鲜心跳。
- `/api/v12/health`：Gateway 后 Kernel 本地存活，不声明产品全链路可用。
- `/api/v12/ready`：聚合 Kernel、Product Manifest 实际选择的能力服务以及 Agent/Router Provider；Knowledge 被选择时增加 Embedding 投影，Executor 被选择时包含 Worker/digest 检查。任一已装配必需组件为 DOWN 时返回 503。

## 恢复与隔离

当前源码初始化 Kernel SQLite v34、Executor SQLite v9、Data schema v8 和 Knowledge schema v7。
`gw migrate plan/apply` 只接受 Kernel v29–v33、Data v4–v7、Knowledge v4–v6，并在已停止实例的
可验证快照和影子存储上逐版本升级；普通服务启动只接受当前版本，不隐式跨版本写入。校验覆盖
schema、记录数、外键、对象 checksum、owner 映射和 lineage，全部通过后才原子切换实例指针。
任一步失败都保留旧存储和快照；不支持降级写入、双写或反向迁移。服务只通过声明的 HTTP 边界
通信，不能读取其他服务的数据库或持久目录。
