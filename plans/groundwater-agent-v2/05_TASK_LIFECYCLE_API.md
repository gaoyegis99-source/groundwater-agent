# 阶段 5 · 业务任务生命周期、API 与数据库

## 基线与实施方案

基线为 `main@bd5d58ab63e16b2f5a59ceabcc7faf1c4c71f411` 加阶段 0–4 的当前工作区改动。阶段 4 的精确成果引用、WorkItem 事务及授权继续供本阶段使用。[阶段 4 报告](04_WORKITEM_BUSINESS_OBJECTS.md)

| 分类 | 现状与选择 |
| --- | --- |
| EXISTING | Kernel Task 拥有交付条件、修订和证据。现有 Task 状态适合 Session 级读法，状态计算包含当前 Session 的技术事实。它可以因机器条件全部成立而完成。 |
| EXTEND | WorkItem 按关联 Session 的有效 Task 修订分别投影业务状态，读取原有 Task 条件和证据。精确关系来自 Task 修订的作者运行、同修订的关联运行、确认与已经绑定的成果证据。同一逻辑执行只跟踪最新 attempt，旧输出身份和历史仍可查询。没有 Task 的 Session 可从已形成的业务准备、执行或成果得到状态。 |
| NEW | 兼容新增业务快照、元数据更新、明确成果验收、分页活动和 SSE。WorkItem 所有者验收精确的当前交付范围。快照返回各关联 Session 状态、原因、发布状态和可验收证据。 |
| DECISION | 对同一工作项里的多个 Session 分别显示原因，并按明确优先级汇总。普通咨询和过时运行未进入当前 Task 因果范围。保留原来的列表、详情和审计响应，冻结 SDK 可继续解码。 |

旧生成的 Python SDK 对响应使用 `extra=forbid`。新增端点可保留原有契约。把业务状态存为另一份可写状态会引入与 Task、Confirmation、Executor 和 Data 的同步及恢复问题。采用服务端只读投影，持久保存 WorkItem 命令、验收依据和轻量活动。执行、停止和确认使用当前权威入口。

### 状态规则

| 状态 | 依据 |
| --- | --- |
| DRAFT | 建立 WorkItem 后尚未形成有效业务准备或需交付的 Task。分析中会显示对应原因。 |
| READY | 有可执行的当前准备项，或已形成未阻塞的 Task 目标。 |
| WAITING_INPUT | 现有 Task 存在未决项或当前精确准备需要数据、参数或可由用户修正的输入。 |
| WAITING_REVIEW | 还有明确的 Task 用户审阅条件，或已产生的非计算交付物等待业务接受。 |
| WAITING_CONFIRMATION | 当前关联的执行确认仍待用户或待处理其明确决定。 |
| EXECUTING | 当前相关执行已排队或实际运行。停止申请受理后显示处理中，直到权威终态。 |
| RESULT_REVIEW | 发布权威核实已提交结果，等待选择精确输出或明确业务验收。 |
| BLOCKED | 当前相关执行失败、确认被拒绝、结果的发布尚无可靠证明，或存在技术阻碍。原因分别可见。 |
| COMPLETED | 明确验收与当前 Task 修订和交付证据一致。已有明确用户审阅且满足其他条件的非成果 Task 可以直接投影完成。 |
| CANCELLED | 已有关联的真实取消终态，或未开始的业务目标实际停止。竞态中以该执行最终事实为准。 |
| ARCHIVED | WorkItem 已被显式软归档。其来源与记录可查。 |

汇总优先显示执行、待确认、待输入、阻塞、成果评审、待审阅、可开始。未结束的有效范围全部解决之后，取消的交付范围优先显示，所有要求都有验收证据时完成。纯咨询或尚无业务事实的草稿不阻挡其他关联交付。详情保留各范围状态。

### 公共接口

| 方法和子路径 | 合同 |
| --- | --- |
| `GET /{workItemId}/snapshot` | `workItemId`、`revision`、`eventCursor`、`state`、`reason`、`tracks`。每个 track 提供 `sessionId`、可空的 `taskId/taskRevision`、`state/reason`、`confirmationId`、`stopPending`、`publications`、`review`、`acceptedAt`。 |
| `POST /{workItemId}/update` | 命令式元数据修改，提交 `projectId, requestId, expectedRevision` 及可选的 `title`、可清空的 `description`。复用 WorkItem 回执。 |
| `POST /{workItemId}/review` | 提交同样的命令身份、`sessionId` 和快照给出的 `reviewToken`，接受该范围的精确当前交付。 |
| `GET /{workItemId}/activity` | 可分页的活动，`entries, nextAfter`。每个条目由当项的 `sequence` 标识，命令条目含修订和主体，Session 条目含所属会话和事件类型。 |
| `GET /{workItemId}/events` | SSE 使用同一活动条目。固定帧名 `work_item.activity`，按该项 sequence 恢复，`Last-Event-ID` 优先于 `afterEventId`。`work_item.reset` 处理恢复旧备份后的超前游标，`work_item.refresh` 提示核对外部 Data/Executor 事实。客户端用事件推进读游标并重读服务端快照。 |

验收令牌绑定单个 Session 的当前 Task 修订、相关执行、明确选择的精确成果与必要证据。选择历史 Task 成果可按当前 Task 的权威证据精确追溯。未关联成果时按实际发布状态提示后续选择。新验收须具备当前读取权限，事务写入前再次核对当前任务和结果身份。已持久验收的历史可读，资源内容的读取权限实时校验。过期令牌返回冲突，网络重试复用同一请求键。

### 存储、迁移与影响

SQLite v38 在同一显式迁移中保留命令序号和回执，增加 UPDATE、REVIEW 命令种类与验收范围。新增按 WorkItem 编号的活动表。旧命令用于填充已存在的活动，随后在 WorkItem 命令和关联 Session 的关键持久事件提交时同步追加。活动只记录业务所需的 ID、类别和时间。旧 `/audit` 继续返回原有五种命令，完整阶段 5 活动由新端点提供。升级在本地 v37 副本演练、验证可重复与事务失败，并核对原始备份恢复。Data、Knowledge 和 Executor 原表继续由它们拥有。

改动涉及 Kernel 的跨所有者只读关系、WorkItem 状态与命令、权限、事件路由、SQLite 迁移及版本读取工具。同步生成 v12 OpenAPI、Python SDK、Web 类型、Ajv 校验并保留 Gateway SSE 配置。验证包含状态矩阵、旧确认与重试去歧义、权限、幂等、并发、SSE 回放、乱序重读、冻结客户端和实际监测趋势发布验收。

主要风险为旧轮次干扰当前交付、审批已过期成果、事件跨主体暴露以及旧数据库迁移丢失回执。分别以精确修订关联、验收时事务复核、单工作项事件游标和冻结 v37 回放验证覆盖。验收执行 `../bin/dev check --scope kernel|assembler|contracts|web` 的各个离线检查，并运行定向迁移、实际监测端到端和启动快照复读测试。

## 实施结果与文件

| 范围 | 实施结果 |
| --- | --- |
| 生命周期 | 在同一 SQLite 快照读取通过 WorkItem 权属校验的 Session、Task 修订、准备、确认、控制与运行事实。精确匹配当前 Task 后分 Session 推导状态。现有 Task 证据仍由 Task 校验。[会话事实](../../source/kernel/src/platform_kernel/application/work_item_lifecycle_facts.py) · [状态推导](../../source/kernel/src/platform_kernel/work_items/lifecycle.py) |
| WorkItem 写入与验收 | 元数据更新与专用验收命令沿用项目、主体、请求键和修订。验收先重新验证精确发布结果、对象和当前访问权限，再在 SQLite 写事务内重新计算交付令牌。既有 Task 的完成、实际发布和明确验收都有各自证据。用户拒绝确认、运行失败、实际取消分别返回不同的原因。[WorkItem 所有者](../../source/kernel/src/platform_kernel/work_items/owner.py) · [资源核验](../../source/kernel/src/platform_kernel/work_items/references.py) |
| 活动与恢复 | SQLite 在提交 WorkItem 命令或关联 Session 事件时按项追加最小活动。SSE 帧用于重新读取快照，客户端按序号去重、忽略乱序响应，并在恢复旧备份时采用服务器的 reset 信号重定向游标。外部发布变化通过读权威快照和周期刷新反映。Gateway 对 WorkItem SSE 禁用代理缓冲与缓存。[活动路由](../../source/kernel/src/platform_kernel/api/routes/work_item_stream.py) · [Web 订阅](../../source/web/src/work-item-stream.ts) · [Gateway](../../source/platform/assembler/assembler_gateway.py) |
| v12 合同兼容 | 新增 5 条路径。原有路径、操作与 Schema 与阶段 4 本地快照一致。新路径同步 Kernel DTO、产品 OpenAPI、Python SDK、TypeScript 和 Ajv。Web 请求与活动帧共用生成校验。旧 `/audit` 读取旧种类，完整命令及关联事件通过 `/activity` 查询。[HTTP 契约](../../source/docs/capabilities/http-api.md#workitem) · [路由及输入](../../source/kernel/src/platform_kernel/api/routes/work_items.py) · [Web 调用](../../source/web/src/work-item-api.ts) |
| SQLite v38 | 新增每项独立序号的活动表、命令与 Session 活动触发器和验收索引。在事务内保存旧命令并填充活动，保留原命令记录及其序号。迁移目标、快照检查和三类报表同步 v38。当前 schema 的冻结 SHA256 为 `558f8fe888941ed6e5c82d49663fbd33204e8010f6c0aefef3f6f5043684b5e3`。[DDL](../../source/kernel/src/platform_kernel/schema/work_items.py) · [迁移](../../source/kernel/src/platform_kernel/database.py) · [回滚说明](../../source/docs/operations/deployment/operations.md) |
| 装配 | 源码装配的变动为 Gateway SSE 路由和对应装配锁。阶段 4 的 Kernel/Executor 配置、产品能力、Workflow、Model、结果合同和发布定义经对照保持精确身份。[装配对照](../../.pytest_cache/gw-v2-phase5-QjUrRGYY/assembly-parity.log) |

逐文件范围见[阶段 5 变更清单](../../.pytest_cache/gw-v2-phase5-QjUrRGYY/changed-files.json)。

## 升级、恢复和客户端兼容

在阶段 4 监测证据的 v37 SQLite 副本执行显式升级，核对冻结签名、内容寻址备份、结构与外键，再次执行保持同一状态。对比原有 1 个 WorkItem、2 个关联、4 项精确引用、8 条命令和所属 Task、Workflow、确认及 Session 历史。8 条已有命令生成同序活动。恢复到独立 v37 副本后再次核对签名和原始记录，阶段 4 原文件摘要一致。[演练记录](../../.pytest_cache/gw-v2-phase5-QjUrRGYY/rehearse-v37-final.log)

冻结的阶段 4 Python SDK 从 v38 读取列表、详情、旧审计，并以原请求键重放归档，返回原修订 8。测试另经新 API 编辑标题，旧 SDK 可读取编辑结果，完整活动可用新接口查询。对照源代码 OpenAPI 与全量生成物通过合同检查。现有已确认但目标为 v37 的旧迁移计划需要重新生成，使确认摘要绑定当前 v38。

## 运行证据

合成监测资料经当前 Kernel、Data、Executor 和原生算子实际运行，意图及 Agent 回复使用脚本夹具。PENDING 时为 `WAITING_CONFIRMATION`，Executor 未建作业。10 个节点完成并真实发布 21 项成果后，Kernel Task 在修订 2 达到 `COMPLETE`，WorkItem 仍为 `RESULT_REVIEW`。明确验收精确 `threshold-event-summary` 后 WorkItem 为 `COMPLETED`。验收重放返回原回执，另一个已关联 Session 中的普通咨询保留业务验收状态。重启回放验收活动，并按权威快照核对状态和游标。最终验收为活动序号 33，归档为序号 35。归档后原始摘要仍为 398 字节，SHA256 为 `acf7ba6d9cd3f644dae94929fe6bd6be0bf82aa515e400dd7e52796160e50779`。[实际链路测试](../../source/tests/e2e/test_monitoring_work_item.py) · [运行与发布证据](../../.pytest_cache/gw-v2-phase5-QjUrRGYY/monitoring-exit-evidence/summary.json)

独立复核通过定向测试修正了几处边界。已废弃或过期的准备不作为就绪事实，已发布结果可继续验收，真实取消终态可见，拒绝恢复与旧运行失败分开。关闭的订阅遇到旧备份会主动使用最高游标建立探测流，再按服务端重置信号应用旧备份的快照。

## 验证结果

验证使用本地锁定的 CPython 3.12.8、Node 和缓存依赖。端到端经隔离网络命名空间和 Unix Socket 访问无网络的临时 PostgreSQL。Git 状态、装配、合同、数据库备份和测试结果均在本地核对。

| 本地命令或检查 | 最终结果 |
| --- | --- |
| `../bin/dev check --scope kernel` | 1003 项通过。Kernel 类型检查、Ruff、核心导入和架构边界通过。[日志](../../.pytest_cache/gw-v2-phase5-QjUrRGYY/kernel-final.log) |
| `../bin/dev check --scope assembler` | 191 项通过。WorkItem SSE Gateway 和源配置冻结摘要通过。[日志](../../.pytest_cache/gw-v2-phase5-QjUrRGYY/assembler-exit.log) |
| `../bin/dev check --scope contracts` | 191 项通过。运行时 OpenAPI、产品合同、Python SDK、TypeScript 和 Ajv 生成检查通过。[日志](../../.pytest_cache/gw-v2-phase5-QjUrRGYY/contracts-exit.log) |
| `../bin/dev check --scope web` | 44 个测试文件共 446 项通过。TypeScript、生产构建、Ruff 和空白检查通过。[日志](../../.pytest_cache/gw-v2-phase5-QjUrRGYY/work-item-closed-restore-web.log) |
| `python -m pytest -q kernel/tests/test_work_item_lifecycle{,_api,_facts}.py kernel/tests/test_work_items.py` | 43 项通过。覆盖状态、并发更新、验收时权限及竞态复核、游标回放和存量 API。[日志](../../.pytest_cache/gw-v2-phase5-QjUrRGYY/focused-final.log) |
| 新增业务模块单独运行 `mypy --follow-imports=silent` | 8 个源码文件通过。[日志](../../.pytest_cache/gw-v2-phase5-QjUrRGYY/work-item-mypy-final.log) |
| `python -m pytest -q kernel/tests/test_schema_contract.py tests/cli/test_{migration_runtime,research_artifacts,report_context_metrics,report_intent_routing_metrics,report_task_success_metrics}.py` | 44 项通过。覆盖 v37/v38 签名、旧链升级、重放、注入故障回滚、快照和维护工具。[日志](../../.pytest_cache/gw-v2-phase5-QjUrRGYY/maintenance-final.log) |
| `python -m pytest -q tests/architecture` 分两次运行 | 230 项架构测试通过，待临时 PostgreSQL 退出后单独运行的 doctor 用例通过。共 231 项。[架构](../../.pytest_cache/gw-v2-phase5-QjUrRGYY/architecture-masked.log) · [doctor](../../.pytest_cache/gw-v2-phase5-QjUrRGYY/doctor-exit.log) |
| 离线 `python -m pytest -q tests/e2e/test_monitoring_work_item.py` 及监测趋势、阈值输入、表格组合回归 | 7 个端到端用例通过，两个 WorkItem 试点用例在最终源码上再次通过。测试选择器与退出码保留在日志。[组合回归](../../.pytest_cache/gw-v2-phase5-QjUrRGYY/monitoring-final.log) · [最终试点](../../.pytest_cache/gw-v2-phase5-QjUrRGYY/monitoring-exit.log) |
| `git diff --check` 和本阶段未跟踪文件空白检查 | 通过。Git HEAD 和暂存区与起始一致，新增和延伸文件见变更清单。 |

初次定向测试复现阶段 5 路由缺失。复核先复现上述状态和订阅问题，再以回归确认修复。升级演练首次使用旧 SDK 状态枚举对象直接与字符串比较，改为检查其实际 wire 值后完整演练通过。最终试点和所选组合回归零失败。

## 退出条件与适用范围

| 验收项 | 结论 |
| --- | --- |
| 十一种业务状态和权威事实 | 通过。状态表与真实试点覆盖确认、执行、实际发布、验收和归档。单测验证停止申请与终态、拒绝与失败、历史准备与当前尝试的区分。 |
| 活动、乱序、重放和 SSE 与快照一致 | 通过。HTTP SSE 核对 header 优先和游标回放。Web 验证重复事件、越序响应、断开和恢复旧库。真实工作项重启后重新核对游标与验收。 |
| 权限、跨项目、幂等与并发 | 通过。覆盖新查询、SSE 和写命令，确认验收重校精确 Data 权限，旧 Task 修订在读写竞态时冲突。 |
| 旧客户端与数据库兼容 | 通过。冻结旧 SDK 回归读取和重放，v37 副本升级、回滚副本、数据库故障回滚和重复执行有实证。 |

阶段 5 退出条件满足。外部发布暂不可核实时，业务快照保留待核验原因。已验收历史和当前资源可读性各有独立证据。此次验证覆盖合成监测链路、离线 SQLite 升级与 Web 合同，实际部署、真实场地科学适用性和浏览器体验需各自的验收环境。阶段 6 若使用这些状态，应继续从服务端快照读取当前精确范围，并通过资源所有者核验引用。改动保留在当前工作区。
