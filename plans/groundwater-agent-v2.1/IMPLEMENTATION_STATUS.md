# Groundwater Agent V2.1 — 实施状态

## 阶段 0 本地基线

- 核查日期为 2026-11-20。分支为 `main`，HEAD 为 `90be0475fb257157d62c2ca9593c56559dfa24b9`。
- 开始时暂存区为空。`git status --short` 显示 `plans/groundwater-agent-v2/` 下旧阶段文档和脚本的删除、总控与索引改动以及 V2.1 提示词和状态模板的新增。`source/` 无 Git 改动。本报告按 [阶段 0 提示词](../groundwater-agent-v2/prompts/01_BASELINE_AND_STATUS.md) 指定路径创建，保留原有计划改动。
- 产品为 `groundwater`，清单默认项目为 `groundwater-demo`。领域包为 `groundwater@6.2.0`，公共合同为 v12，Kernel SQLite 为 v38，Executor SQLite 为 v9。Data 与 Knowledge 的公共服务路由为 v3。[产品清单](../../source/products/groundwater/product-manifest.json) · [架构概览](../../source/docs/architecture/architecture-overview.md)
- 验证使用已有 Python 3.12.8、Node.js 22.23.1、依赖缓存和 PostgreSQL 16.10 镜像。合成用例使用独立数据库与运行目录、脚本 Provider 和原生执行路径。测试命令、边界与日志见文末。

## 完成度矩阵

`ABSENT` 表示受审路径缺失，`PARTIAL` 表示只有部分场景或实现，`IMPLEMENTED` 表示调用路径存在，`VERIFIED` 表示该列对应范围有本轮通过的本地验证。产品列的 `ACCEPTED` 需要真实用户或领域验收证据。本轮无 `ACCEPTED` 项。

| 能力 | Backend | API | Frontend | E2E | Product | 证据与取舍 |
| --- | --- | --- | --- | --- | --- | --- |
| WorkItem 列表、创建、详情与归档 | VERIFIED | VERIFIED | ABSENT | PARTIAL | PARTIAL | Kernel 有分页、创建、详情、元数据更新和归档，合成闭环覆盖创建、详情及归档。页面入口属 **NEW**。[路由](../../source/kernel/src/platform_kernel/api/routes/work_items.py) · [API 集成测试](../../source/kernel/tests/test_work_items.py) |
| WorkItem 导航与当前范围 | IMPLEMENTED | VERIFIED | ABSENT | ABSENT | PARTIAL | 服务端已有项目和所有者范围。主页、URL、身份切换的 WorkItem 范围属 **NEW**。[所有者](../../source/kernel/src/platform_kernel/work_items/owner.py#L411) · [App](../../source/web/src/App.tsx#L649) |
| WorkItem 与多 Session | VERIFIED | VERIFIED | PARTIAL | VERIFIED | PARTIAL | 同项目同所有者下一个 WorkItem 可关联多个 Session，每个 Session 至多归属一个 WorkItem。现有界面可恢复单个 Session；创建、关联和回到同一 WorkItem 的体验需 **EXTEND**。[关联逻辑](../../source/kernel/src/platform_kernel/work_items/owner.py#L441) · [多会话用例](../../source/tests/e2e/test_monitoring_work_item.py#L143) |
| Agent WorkItem 业务上下文 | PARTIAL | ABSENT | ABSENT | ABSENT | PARTIAL | Agent 具备 Session 级上下文和预算约束。WorkItem 来源、选择和授权投影缺失，属 **EXTEND / DECISION**。[消息合同](../../source/kernel/src/platform_kernel/api/schemas.py#L73) · [上下文读取](../../source/kernel/src/platform_kernel/agent_runtime/persistence/context.py#L37) |
| Data / Knowledge 引用 | VERIFIED | VERIFIED | PARTIAL | PARTIAL | PARTIAL | WorkItem 可挂载并重新读取精确版本。Data 撤权有 E2E，Knowledge 精确版本及失权有定向用例。现有资料入口可复用，WorkItem 关联管理需 **EXTEND**。[引用权威](../../source/kernel/src/platform_kernel/work_items/references.py) · [引用测试](../../source/kernel/tests/test_work_item_references.py) |
| 地下水业务对象引用 | VERIFIED | VERIFIED | ABSENT | PARTIAL | PARTIAL | 可关联 WELL、REGION、AQUIFER 的资料字段引用，试点覆盖井的往返与撤权。业务实体的权威身份、去重和属性仍属 **DECISION**。[对象合同](../../source/kernel/src/platform_kernel/api/work_item_schemas.py#L81) · [对象撤权用例](../../source/tests/e2e/test_monitoring_work_item.py#L368) |
| 数据目录、版本、质量检查 | IMPLEMENTED | IMPLEMENTED | PARTIAL | PARTIAL | PARTIAL | Data 有目录、历史语义、质量套件、运行和告警接口。合成链路覆盖上传和所需资料核验，门户的专门质量流程及与 WorkItem 的联动需 **EXTEND**。[Data 目录](../../source/services/data/src/platform_data_service/api/routers/catalog.py) · [质量 API](../../source/services/data/src/platform_data_service/api/routers/quality.py) |
| Preparation 与澄清 | VERIFIED | VERIFIED | PARTIAL | PARTIAL | PARTIAL | 定向用例覆盖缺资料、字段与单位、显式科学参数、歧义及同一会话补齐。WorkItem 层目前只汇总相关轨迹和原因。**EXISTING / EXTEND**。[准备工具](../../source/kernel/src/platform_kernel/agent_runtime/preparation_tool.py) · [准备测试](../../source/kernel/tests/test_business_capability_preparation.py) |
| 确认与执行进度 | VERIFIED | VERIFIED | IMPLEMENTED | VERIFIED | PARTIAL | 合成试点验证确认前无作业，确认后进入执行。既有 Session 时间线展示确认与进度，WorkItem 页面待接入。**EXISTING / EXTEND**。[时间线](../../source/web/src/TurnTimeline.tsx) · [试点](../../source/tests/e2e/test_monitoring_work_item.py#L217) |
| 成果发布 | VERIFIED | VERIFIED | PARTIAL | VERIFIED | PARTIAL | Executor 终结流程将发布交回 Data，Kernel 校验后回读。Session 可见成果，WorkItem 待接入。**EXISTING / EXTEND**。[发布终结](../../source/services/executor/src/platform_executor_service/result_finalization.py) · [Data 发布](../../source/services/data/src/platform_data_service/publication/service.py) |
| 成果查看、比较与审核 | VERIFIED | VERIFIED | PARTIAL | VERIFIED | PARTIAL | 既有 Session 成果区有预览、版本比较与继续入口。WorkItem 审核 API 已验证，合成 E2E 是直接调用，没有 WorkItem 成果页和审核按钮。**EXTEND**。[成果工作区](../../source/web/src/WorkspacePanel.tsx#L576) · [审核用例](../../source/tests/e2e/test_monitoring_work_item.py#L252) |
| 跨 Session 成果复用 | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | PARTIAL | PARTIAL | Web 已可显式把已发布成果带入新会话。试点另一个已关联会话访问与审核状态保留有证据；本轮未验证该 Web 入口执行新一轮分析。按 WorkItem 发现和授权引用需 **EXTEND**。[App 成果动作](../../source/web/src/App.tsx#L730) |
| Business Capability | VERIFIED | PARTIAL | VERIFIED | PARTIAL | PARTIAL | 仅一项监测趋势试点及一个 Profile。界面从产品配置展示，消息入口承接选择，数据库浏览等直接能力各走现有服务。更多场景属 **EXTEND / DEFER**。[产品能力配置](../../source/products/groundwater/capabilities/business-capabilities.v1.json) · [匹配测试](../../source/kernel/tests/test_business_capability_selection.py) |
| Workflow、Profile、Result Contract 与算子治理 | VERIFIED | PARTIAL | PARTIAL | PARTIAL | PARTIAL | 编译和技术治理已装配，试点走真实原生算子。已验证范围为装配合同和该试点。其他算子、运行时和科学应用的验证保留专项，属 **EXISTING / DEFER**。[编译器](../../source/packages/workflow-compiler/src/platform_workflow_compiler/graph.py) · [Profile 与成果合同](../../source/products/groundwater/workflows/workflow-platform.v1.json) · [Validation V2](../../source/products/groundwater/operator-validation.v2.json) |
| 工作台信息架构 | IMPLEMENTED | IMPLEMENTED | PARTIAL | ABSENT | PARTIAL | 正式导航以会话、历史和工作台标签为主，产品已有独立 Data、Knowledge 入口。WorkItem 的主页入口、恢复路由和跨模块导航需 **EXTEND**。[会话导航](../../source/web/src/SessionNavigation.tsx) · [项目历史](../../source/web/src/ProjectTaskHistory.tsx) |
| 权限与协作 | VERIFIED | VERIFIED | PARTIAL | PARTIAL | PARTIAL | 角色和项目/所有者隔离、引用撤权已验证。WorkItem 共享、移交、跨所有者关联或委托审核需 **DECISION**。[身份依赖](../../source/kernel/src/platform_kernel/api/dependencies.py) · [项目边界测试](../../source/kernel/tests/test_project_boundary.py) |
| 恢复与可观察性 | VERIFIED | VERIFIED | PARTIAL | VERIFIED | PARTIAL | Kernel 活动回放、还原 reset、快照和审核重放有证据，现有 Session 恢复代码可复用。WorkItem SSE 当前仅客户端模块，页面挂载及身份切换有待 **EXTEND**。[SSE 服务端](../../source/kernel/src/platform_kernel/api/routes/work_item_stream.py) · [Web 流客户端](../../source/web/src/work-item-stream.ts) |

## 实际调用链与边界

### WorkItem、Web 与多会话

1. Kernel 将 WorkItem 路由装入 v12，并以经过验证的项目、身份和角色进入 `WorkItemOwner`。创建使用 `requestId` 幂等重放。修改以 `expectedRevision` 校验，SQLite 写事务保证修订、命令和活动一致。v36→v37 建立工作容器和引用，v37→v38 补齐命令、活动及回填。Task 的交付事实继续由原有 Kernel 路径持有。[路由](../../source/kernel/src/platform_kernel/api/routes/work_items.py) · [数据库升级](../../source/kernel/src/platform_kernel/database.py#L285)
2. WorkItem 有 13 个操作。只读操作为列表、详情、审计、快照、活动分页和 SSE。写操作为创建、关联 Session、关联对象、关联资产、改元数据、审核和归档。Kernel OpenAPI、产品合同、Python SDK 和 TS 操作清单一致。12 个 JSON 响应有生成的响应验证器，SSE 帧由流客户端解析并校验活动负载。[Kernel OpenAPI](../../source/kernel/contracts/openapi-v12.json) · [产品 OpenAPI](../../source/products/groundwater/contracts/public-api-v12.json) · [SDK](../../source/packages/groundwater-api-client/src/groundwater_api_client/generated/operations.py)
3. Web 手写模块有快照、活动、更新和审核四个 JSON 客户端，以及 SSE 订阅。生产 React 页面没有导入这些模块，也没有直接调用其余 WorkItem API。列表、创建、详情、关联、归档和审核均无可达页面流程。当前 `App` 以 Session hash 路由加载 `SessionNavigation`、`ProjectTaskHistory`、`TurnTimeline` 和 `WorkspacePanel`，会话续开可用。详情已有 Session、Task、Attempt、WorkflowRun、对象与资产的相关投影，可供新页面使用。[客户端](../../source/web/src/work-item-api.ts) · [App 会话入口](../../source/web/src/App.tsx#L649) · [会话路由](../../source/web/src/use-project-sessions.ts) · [详情 Schema](../../source/kernel/src/platform_kernel/api/work_item_schemas.py#L212)
4. 快照在 SQLite 读事务中取当前 WorkItem 修订、活动头和关联 Session 事实，随后解析外部发布或可读性。活动序号可随 Session 事实前进，即使 WorkItem 修订未变。SSE 首次用 `afterEventId`，浏览器续连的 `Last-Event-ID` 优先，服务端按已提交活动重放，周期 refresh 补读外部事实，游标超头或还原丢失时发送 reset。Web 片段先取快照，从其 `eventCursor` 开流，去重校验事件并重拉快照，回退时等待 reset 重新建立视图。历史活动仍按活动 API 分页。网关 SSE 路由关闭缓冲。组件接入时需在身份、项目或 WorkItem 切换时关闭旧订阅和清除旧快照。[所有者](../../source/kernel/src/platform_kernel/work_items/owner.py#L131) · [SSE](../../source/kernel/src/platform_kernel/api/routes/work_item_stream.py) · [Web 续连](../../source/web/src/work-item-stream.ts) · [Gateway](../../source/platform/assembler/assembler_gateway.py#L133)
5. (WorkItem, project, owner) 与 Session 的项目、所有者都由 Kernel 校验。路由要求 researcher 或 admin，所有者条件对 admin 仍成立。同项目其他用户无法用角色替代所有权。只读 WorkItem、多人协作、移交和审核委派需定义角色与资源的组合授权，阶段 1 沿用现有边界。[角色与路由](../../source/kernel/src/platform_kernel/api/routes/work_items.py#L40) · [关联约束](../../source/kernel/src/platform_kernel/application/work_item_links.py) · [API 负例](../../source/kernel/tests/test_work_item_lifecycle_api.py#L156)

### Agent 与当前业务工作

- 消息入口接受明确的资料引用、运行模式及可选工作流选择。`create_message_request` 在当前 Session 内绑定精确选择并记录路由状态；上下文加载器按 `session_id` 与目标修订读历史、记忆、Task、最新工作流事实等。上下文预算对原文和摘要实行压缩和限额，视图记录支持追溯。消息、启动和上下文构造路径没有按 WorkItem 查找关联，也没有把对象、关联资料、别的 Session 的缺项或审核自动交给 Agent。[请求保存](../../source/kernel/src/platform_kernel/interaction/message_requests.py) · [上下文预算](../../source/kernel/src/platform_kernel/agent_runtime/context_policy.py) · [上下文视图测试](../../source/kernel/tests/test_context_views.py)
- 已有跨会话能力是同一 WorkItem 汇总多个 Session 轨迹，以及显式传递已发布成果。每个会话继续保有自己的历史、Task、Preparation、待确认和目标修订。补答被限定在当前会话的新鲜阻塞准备状态。后续应先定义服务器解析的 WorkItem 范围和冲突策略，按需取有限事实、附来源，保留对引用的权限和版本复核。[事实投影](../../source/kernel/src/platform_kernel/application/work_item_lifecycle_facts.py) · [同会话补答](../../source/kernel/src/platform_kernel/interaction/message_requests.py#L221)

### 资料、执行、结果与验收

- Data 的目录、上传、查询、版本、语义和质量 API 可直接用于浏览和检查。Knowledge 维护文档版本、证据单元和检索记录。WorkItem 可记录 DATA、KNOWLEDGE、WORKFLOW、RESULT 引用，挂载及详情复核可见性，旧语义可按固定版本重读。对象引用验证资料身份和字段存在，`externalId` 的井、区或含水层实体归属与行值尚无独立权威验证。质量与知识事实继续来自各自服务。[Data](../../source/services/data/src/platform_data_service/api/routers/catalog.py) · [Knowledge 检索](../../source/services/knowledge-next/src/platform_knowledge_next/routers/retrieval.py) · [引用归属](../../source/kernel/src/platform_kernel/work_items/references.py#L40)
- Preparation 校验当前显式采用的精确资料与科学参数，Confirmation 绑定不可变执行方案，Executor 拥有运行、节点和检查点。WorkItem 生命周期按关联会话和当前 Task 修订收集当前 Preparation、待确认、AgentRun、WorkflowRun 最新尝试、停止请求及 Task 交付检查。已关联资料、实际采用、执行成功、发布成功、验收分别有自己的事实。执行成功后需要可核验的 Data 发布、成果证据和相应审核；命名成果绑定运行、输出名、发布 ID 和 DataVersion 身份。存在新的活动轨迹时整体状态由各轨迹优先级汇总。[Preparation](../../source/kernel/src/platform_kernel/agent_runtime/preparation_tool.py) · [生命周期事实](../../source/kernel/src/platform_kernel/application/work_item_lifecycle_facts.py#L37) · [状态推导](../../source/kernel/src/platform_kernel/work_items/lifecycle.py#L121)
- 监测试点绑定 Workflow 1.1.0、Model 3.1.0 及各为 1.0.0 的 Profile 和 Result Contract。成果合同定义 21 个命名输出角色、类型、单位、适用 Viewer 和质量来源。Assembler 校验工作流与清单约束，并把业务能力预览投影到 Product UI。Session 成果面向已发布 Data 资源展示表格、地图、栅格和图表，也可跳转版本比较、附加资料或带成果新开会话。WorkItem 审核依据当前任务交付和精确结果链接重算审核 token，并在提交时复核。成果契约已有装配约束，统一的 WorkItem 成果中心和跨工作成果目录仍待产品化。[成果合同](../../source/products/groundwater/workflows/workflow-platform.v1.json) · [装配检查](../../source/platform/assembler/assembler_workflow_platform.py) · [审核](../../source/kernel/src/platform_kernel/work_items/owner.py#L252) · [Viewer](../../source/web/src/ArtifactViewer.tsx)
- 合成监测旅程先确认再执行，真实发布 21 个成果，Task 完成后 WorkItem 进入 `RESULT_REVIEW`，明确审核后才进入 `COMPLETED`。第二会话咨询保留审核状态，重启可回读权威快照及活动；另一用例检查 Data 访问撤销后隐藏对象和资产详情。验证覆盖本地服务链路，Provider 理解、浏览器操作和真实水文科学验收仍需独立证据。[两个端到端用例](../../source/tests/e2e/test_monitoring_work_item.py)

### 专业能力覆盖

- Assembler 从 manifest 加载 Operator Validation V2，按算子版本、实现修订、Manifest 摘要和证据文件校验。显式 V2 清单登记 5 个算子版本记录，产品组合白名单有 62 个条目，历史技术证据另行引用。静态 Operator Pack 与 Workflow Compiler、装配清单继续复用。组合白名单与科学适用性需要各自的证据。[Validation V2](../../source/products/groundwater/operator-validation.v2.json) · [证据校验](../../source/platform/assembler/operator_metadata.py) · [组合编译](../../source/packages/workflow-compiler/src/platform_workflow_compiler/composition/execution.py)
- 产品仅挂载 `groundwater.monitoring-trend@1.0.0`，其一个 Profile 覆盖监测趋势、季节变化和阈值事件。AUTO 按当前角色、可见性、技术验证状态、必需词组和排除词筛选。零匹配或多匹配保守交回路由，缺资料、未定或否定的参数、冲突单位进入阻塞准备。已装配 Profile/Model/Workflow 固定身份和确认仍适用。产品中其他模型、数据浏览与知识查询的存在不扩大该业务能力覆盖。[装配](../../source/platform/assembler/assembler.py#L210) · [能力筛选](../../source/kernel/src/platform_kernel/business_capabilities.py#L162) · [单位和字段检查](../../source/kernel/src/platform_kernel/agent_runtime/preparation_tool.py#L412)

## 优先级与风险

| 优先级 | 用户问题 | 后续动作与边界 |
| --- | --- | --- |
| P0 · 阶段 1 | 首页无法发现、创建或恢复一个业务工作，用户只能按会话查找 | 使用现有 13 个 API 中的必要操作把 WorkItem 接到主导航和详情。优先复用快照、活动分页与 SSE，保留原有 Session 路由 |
| P0 · 阶段 1 | 旧身份的详情或晚到快照可能串入切换后的页面。归档、冲突、资源撤权要可感知 | 按 product/project/subject 隔离并清除旧请求、订阅和缓存。409 刷新服务端修订，资源失权隐藏引用。回退由 reset 和新快照恢复 |
| P1 · 阶段 2–3 | 用户新开关联会话后需重复交代范围和资料 | 分开实现显式的关联/恢复与 Agent 的 WorkItem 上下文。先评审授权、有限上下文与来源，再实现 |
| P1 · 阶段 4–5 | 质量、对象和成果分散在会话与服务门户 | 为对象权威及标识冲突形成 ADR，评估 Data 深链与精确资产引用。后续交付页承接现有发布和审核 |
| P2 · 阶段 6–8 | 专业覆盖和真实用户验收仍只有试点证据 | 以业务需求决定新能力，保留装配身份；分别补浏览器旅程、角色、断线、真实数据及领域验收。共享或委派另行决策 |

## 阶段 1 最小可验收计划

目标旅程为从首页打开工作列表，创建业务工作，查看权威状态及资料，进入一个已关联会话，刷新页面后回到同一个工作。已有会话入口继续可访问。

1. 在 `source/web/src/` 新建 WorkItem 列表、详情和 controller。扩展 `work-item-api.ts` 接入生成合同的 list、create、detail、archive，复用现有 update、review、activity 和 stream。主应用只增加入口、独立 hash 路由分支和回到工作链接。可用的审核动作以当前快照的审核 token 为准；多会话关联、批量组织和 Agent 继承分别按后续阶段实施。
2. 列表使用 `nextAfter` 分页，展示标题、说明、更新时间和归档状态。当前列表合同按 WorkItem ID 排序，`status` 表示 active/archived，业务状态与 Session 数由快照 state/tracks 提供。只给可见行发有界的补读请求，保留加载、不可用和过期状态。若真实数据下读放大妨碍验收，单独测量并评审聚合 API。详情分别读取 detail、snapshot 和活动，展示进度、会话、资料与对象、成果和当前可用动作。
3. 创建为每次用户动作固定 `requestId`，按回执导航。网络超时用原命令幂等恢复。归档、编辑和审核携带当前 `expectedRevision`，过期时刷新并展示冲突。请求支持 AbortSignal，作用域切换关闭订阅，SSE 以重拉权威快照确认进度。处理未授权、禁止、不可见、冲突、输入无效、限流和服务故障。
4. 预计影响 `App.tsx`、`work-item-api.ts`、新页面与其 controller/测试，以及必要的导航、样式和可访问性文案。优先复用 `work-item-stream.ts`、`SessionNavigation.tsx` 和既有 Viewer。当前方案无需 API、Schema 或 SQLite 改动。
5. 验收需覆盖列表分页、创建超时及重复提交、回执跳转、详情中的多会话和不可用引用、归档和 revision 冲突、项目与用户切换、事件重复及乱序、游标回退与 reset、刷新恢复、键盘焦点和无权限时的操作可见性。先跑新增组件/客户端测试与现有 WorkItem API/SSE 回归，再执行 `../bin/dev check --scope web`，并在隔离本地实例验证从入口到恢复的浏览器旅程。若需要新的服务端合同，先评审并追加 `kernel`、`contracts` 检查及兼容性验证。

## 阶段 0 本轮验证

以下 Python 命令在 `source/` 使用已有工具链并在无外网的命名空间执行。Web 命令在 `source/web/` 使用本地 Vitest。避免与运行实例共用数据库。

| 类型 | 命令及范围 | 结果 |
| --- | --- | --- |
| 单元、API 集成、Schema 迁移、装配和公开合同 | `python -B -m pytest -q -p no:cacheprovider kernel/tests/test_work_items.py kernel/tests/test_work_item_{lifecycle,lifecycle_api,lifecycle_facts,references}.py kernel/tests/test_{schema_contract,project_boundary,business_capability_selection,business_capability_preparation}.py tests/assembler/test_{business_capabilities,operator_validation,workflow_platform}.py tests/contracts/test_public_api_contract.py` | 187 通过，0 失败。[日志](../../.pytest_cache/gw-v21-phase0-m0br2Ugv/python.log) |
| 上下文与结果回读回归 | `python -B -m pytest -q -p no:cacheprovider kernel/tests/test_{context_compiler,context_views,session_results,confirmation_recovery}.py` | 29 通过，0 失败。[日志](../../.pytest_cache/gw-v21-phase0-m0br2Ugv/context.log) |
| Web 客户端、组件与合同 | `../node_modules/.bin/vitest run src/work-item-api.test.ts src/work-item-stream.test.ts src/business-capability.test.tsx src/SessionNavigation.test.tsx src/WorkspacePanel.test.tsx src/ArtifactViewer.test.tsx src/api-contract.test.ts` | 7 个文件，55 通过，0 失败。WorkItem HTTP/SSE 覆盖为夹具测试。[日志](../../.pytest_cache/gw-v21-phase0-m0br2Ugv/web.log) |
| 合成服务链路 E2E | `python -B -m pytest -q -p no:cacheprovider tests/e2e/test_monitoring_work_item.py`，使用缓存镜像、仅 Unix socket 的隔离 PostgreSQL、脚本 Provider 和原生算子 | 2 通过，0 失败，临时数据库已退出。[命令](../../.pytest_cache/gw-v21-phase0-m0br2Ugv/run-monitoring.sh) · [日志](../../.pytest_cache/gw-v21-phase0-m0br2Ugv/monitoring.log) |
| 静态核对 | 对比 Kernel OpenAPI、产品合同、生成 SDK、TS 与 12 个 JSON 响应验证器，并追查 WorkItem 模块的生产引入路径 | 13 个操作对应，SSE 走帧验证路径。现有生产组件无 WorkItem 客户端接入 |

全量发布门禁、正式浏览器旅程、真实 Provider、远程 Worker、领域科学验收和共享权限场景未运行。阶段 0 只新增本报告，生产代码、接口、数据库和部署未改动。已完成要求的基线、调用链、完成度矩阵、风险、优先级和阶段 1 验收计划。

## 阶段 1 WorkItem 前端产品化

阶段 1 已通过本地技术验收。基线为 `main@90be0475fb257157d62c2ca9593c56559dfa24b9` 和阶段 0 报告，原有 V2.1 提示词改动保留。

- Groundwater 首页现有「业务工作」入口。列表按服务器游标分页，工作状态、会话数和下一步来自快照，补读并发上限为 4。支持查看已归档工作、刷新及重试。
- 创建根据服务器回执进入详情。当前标签页按 product/project/subject 保留待确认创建请求，响应丢失后可跨刷新重放同一命令。作用域切换中止请求、订阅，并丢弃晚到旧响应。
- 详情展示进展、关联会话、资料对象、可读成果、活动和操作。会话 URL 携带返回工作的导航目标，现有会话与工作台路由仍可使用。活动由 API 分页，SSE 触发权威快照及详情重新读取。访问不可用时隐藏引用详情。
- 编辑、归档和验收使用现有请求 ID、修订和审核 token。审核确认期间若交付身份变化，需要用户重新核对。列表状态随实际读取或刷新更新，详情随 SSE 更新。
- 改动集中在 Web。入口及会话路由接入涉及 `App.tsx`、`use-project-sessions.ts`、`use-workspace-controller.ts`。新增页面、命令和读取逻辑在 `WorkItemWorkspace.tsx`、`WorkItemDetails.tsx`、`WorkItemForms.tsx`、`use-work-item-command.ts`、`use-work-item-queries.ts` 及同目录路由、创建缓存、展示和样式文件。合同客户端扩展在 `work-item-api.ts`、`types.ts`，回归在 `work-item-workspace.test.tsx`。服务端 API、合同与数据库沿用现有版本。

| 验证 | 本轮结果 |
| --- | --- |
| `../bin/dev check --scope web` | 45 个测试文件，453 个用例通过。TypeScript、生产构建、Ruff 和空白检查通过。[日志](../../.pytest_cache/gw-v21-phase1-IrGQDN8O/web-final.log) |
| `python -B -m pytest -q -p no:cacheprovider kernel/tests/test_work_items.py kernel/tests/test_work_item_{lifecycle_api,references}.py` | 26 个用例通过，覆盖边界、并发、精确引用和 SSE。[日志](../../.pytest_cache/gw-v21-phase1-IrGQDN8O/kernel-final.log) |
| 隔离真实 Kernel 与 Chromium | 用生产 Web 构建、独立 SQLite 和无外网的回环接口测试。首次创建服务端实际提交，浏览器丢弃回复，刷新后重放并确认库里仅一个工作。核对双会话、实际 SSE、会话返回、详情刷新、归档、焦点及 1440/768/390 像素宽度。使用已有 Chromium 可执行文件。[脚本](../../.pytest_cache/gw-v21-phase1-IrGQDN8O/run-browser.sh) · [结果](../../.pytest_cache/gw-v21-phase1-IrGQDN8O/browser.log) |
| VCS 检查 | `git diff --check` 通过，既有暂存内容和计划文件的跟踪改动保持一致。 |

浏览器用例通过现有 API 写入两条测试关联。阶段 2 需要交付关联操作、新会话与同一业务工作的持续恢复，并验证冲突以及跨 owner 的拒绝。真实领域验收和部署属于后续范围。

阶段 2 前置核查发现，当前「新建 Session」和「关联到 WorkItem」由两个命令完成。Session 创建合同只有 `projectId`，关联合同具备 `requestId` 和 `expectedRevision`。阶段 2 需明确创建已提交但回执丢失，以及创建成功但关联冲突时的恢复语义，再确定实现与同步合同范围。[Session 命令](../../source/kernel/src/platform_kernel/api/routes/session_commands.py#L44) · [关联命令](../../source/kernel/src/platform_kernel/api/routes/work_items.py#L99)

## 阶段 2 WorkItem 与 Session 连续工作

### 实施方案

- **EXISTING**。WorkItem 详情与活动、成对的主体和项目校验、Session 唯一关联以及会话流已经具备。继续复用已有 Session 的历史、准备、确认、草稿、成果及 SSE。
- **EXTEND**。WorkItem 详情提供新建会话与加入已有会话。关联冲突时显示可理解的原因，并保留在工作内新建会话的路径。受认证的会话入口按服务端关联恢复工作的背景和往返链接。
- **NEW**。Kernel 增加受控的新建并关联命令，以及面向当前 Session 的只读背景投影。创建、关联、修订和命令回执采用同一个现有 SQLite 事务。区分普通关联与原子创建的请求指纹，保留既有 `LINK_SESSION` 关联审计语义。只读投影以精确、可见引用及有来源的任务和审批证据为边界。
- **DEFER**。自动把业务上下文装配到 Agent 交由阶段 3。阶段 2 在会话内提供可见工作背景，并允许用户明确采用可用的资料。执行授权及确认流程依照当前 Session。
- 影响 Kernel WorkItem 和 Interaction owner、WorkItem/Session HTTP 路由及合同、OpenAPI/SDK/Web 类型和校验器、Web 会话工作区、文档与测试。复用现有存储结构。核心风险为请求回执丢失、关联并发、权限撤销和晚到旧响应。
- 先做 Kernel 否定用例及 Web集成，再运行 `../bin/dev check --scope kernel`、`contracts`、`web`，并以隔离 Kernel 与 Chromium 走双会话链接、重放、恢复和原有会话兼容验收。

### 实现与边界

- 从工作详情可新建并关联会话，也可搜索、分页选择本人已有会话。重复提交同一新建命令返回原会话，回执丢失后刷新可继续重放。跨 WorkItem 的关联冲突显示原归属受保护，并提供改选或在当前工作新建会话的路径。
- 新增两个 v12 操作。WorkItem 创建子会话时，Session、Session 事件、关联、WorkItem 修订和回执在同一事务提交。只读背景按当前 Session 的服务端关联恢复，登录身份或项目变化时重新验证。旧 API 操作、既有 DTO 和 SQLite schema 经对照保持原样。
- 背景展示业务工作标题与说明、可读对象和版本、待继续任务、精确发布成果及有确认事件和可读冻结输入的方案参数。资料限最近 24 条，任务和已确认动作各限 8 条。来源会话及版本可追踪。用户通过现有资料查看器选择是否用于当前会话。Agent 自动读取背景、权限撤销时的装配级剔除及上下文预算属于阶段 3。
- 影响文件包括 Kernel 的 `work_items/owner.py`、`work_items/handoff.py`、两个 WorkItem 应用事实投影、Interaction 持久化与装配、两个 HTTP 路由及 Schema。更新运行时 OpenAPI、产品合同、Python SDK、前端 TS/Ajv、《HTTP API》和《Contracts》。Web 新增 `WorkItemSessions.tsx`、`SessionWorkContext.tsx`，并扩展详情、工作区、请求恢复和路由入口。测试扩展 `test_work_items.py` 与 `work-item-workspace.test.tsx`。
- 架构检查指出 Task 和确认事实应由现有的只读生命周期投影读取。查询已移到该投影，存储所有权规则沿用原有约束。Web 入口改用已装配的研究工作台布局配置，保持产品中性。移动端验证发现会话侧栏挤出页面，已针对带续作背景的窄屏会话将导航、对话和工作台纵向排布。

### 本轮验证

| 检查 | 结果 |
| --- | --- |
| `../bin/dev check --scope kernel` 与 `bash scripts/checks/check-boundaries.sh` | 1006 个 Kernel 用例、类型、Ruff、空白及核心导入检查通过。全量命令末尾发现 Web 入口硬编码产品身份，修正后独立架构检查通过。[Kernel 日志](../../.pytest_cache/gw-v21-phase2-Fuz665K3/kernel-final.log) · [架构复验](../../.pytest_cache/gw-v21-phase2-Fuz665K3/architecture-final.log) |
| `../bin/dev check --scope contracts` | 191 个合同用例通过。运行时 OpenAPI、Python SDK、TS/Ajv 生成一致。首次运行的 3 个 HTTP 夹具因隔离命名空间的回环未启用而失败，启用回环后重跑通过，外网仍隔离。[重跑日志](../../.pytest_cache/gw-v21-phase2-Fuz665K3/contracts-loopback.log) |
| `../bin/dev check --scope web` | 45 个测试文件共 455 个用例通过，类型、生产构建、Ruff 与空白检查通过。[日志](../../.pytest_cache/gw-v21-phase2-Fuz665K3/web-exit.log) |
| 真实本地 Kernel 和已缓存的 Chromium | 用独立 SQLite、两个测试账号及生产 Web 构建验证加入已有、归属冲突、新建回执丢失后的原命令重放、直接链接恢复、退出再登录、不同主体隔离、会话草稿恢复、SSE 断线重连、390 像素布局及标准布局兼容。[脚本](../../.pytest_cache/gw-v21-phase2-Fuz665K3/run-browser.sh) · [日志](../../.pytest_cache/gw-v21-phase2-Fuz665K3/browser-standard.log) |
| `python -B -m pytest -q -p no:cacheprovider tests/e2e/test_monitoring_work_item.py` | 2 个端到端回归通过，覆盖原有会话、Task、运行、发布和访问撤销事实。临时 PostgreSQL 已退出。[日志](../../.pytest_cache/gw-v21-phase2-Fuz665K3/monitoring.log) |

阶段 2 已达到本地技术退出条件。后续进入阶段 3 时，需复用当前会话的权威关联，用限量、带来源的投影装配 Agent 上下文，专项验证权限撤销、跨会话隔离和工具范围。

## 阶段 3 Agent 业务上下文与安全装配

### 实施方案

- **EXISTING**。Kernel 已拥有 Session/WorkItem 归属、持久消息身份、当前 Task 与 Preparation 投影、审批和执行发布事实。Data、Knowledge、Executor 为底层资源权威。Agent 已有模型输入计数与无正文的 context manifest。
- **EXTEND**。在模型调度前逐次读取有效的 Session、项目、当前用户和关联工作。意图分类继续只以当前请求及原有 Session 上下文为输入。当前意图与授权范围确定后，补入带来源的 WorkItem 背景。调用完成、暂停或重试后重新读取，输入正文留在当前模型调用。
- **NEW**。内部 `work-item-agent-context.v1` 只读分层。L0 记录持久请求及受校验的执行范围。L1 分开表达整个工作与当前会话的状态，并带当前 Task、准备问题、确认和发布事实。L2 按当前任务核对关联的精确可读版本及已选业务能力。L3 在需要续作或复盘时读取少量已关联会话的事实摘要，带时间、版本和类别。原会话的内存摘要和完整消息保持自身作用域。
- 背景以 JSON 数据消息送入模型，固定说明把用户资料字段与工具授权分开。用现有 tokenizer 和请求容量检测限制附加包，最高 1,200 token 与 4,096 UTF-8 字节。可选资料和历史按优先级省略，保留当前用户请求。资源校验不可用时省略相应背景。流程沿用旧的 public API 与 SQLite。
- 影响 WorkItem owner、会话身份查询、Agent Runtime 的模型输入过滤、工具权限、上下文 manifest 及测试。主要风险是晚到的撤权、恢复重放旧材料、自由文本越权和模型输入超限。验证包含实际 provider 调用、同次重试、跨 Session/主体/项目、资源失效、老会话、只读任务、预算和 trace 检查。阶段内运行 Kernel、合同与隔离链路回归。

### 实现与行为

- 本阶段基线为本地 `main@7a6e621162a51c55340e44167df42c334290f1c9`。交付在 Kernel 当前 Session 的 Agent 调用路径。通过持久 AgentRun、当前或待启动运行指针、项目、目标修订和成员角色找回关联 WorkItem。异步校验完成后再检查工作修订、当前运行与成员身份。
- `work-item-agent-context.v1` 用来源分块表达四层内容。WorkItem 汇总和当前 Session/Task 的进度分开。关联资料按当前请求选取，并由 Data、Knowledge 及 Kernel 的成果绑定校验精确版本。已选能力复用本轮解析好的版本身份。续作和复盘按需读取至多四条带时间和修订的其他会话事实摘要。
- SDK 在每次主模型调度前装配独立数据消息。协议重试和确认恢复重新装配。模型收到的附加包保留原始用户消息，限制为 4,096 字节和 1,200 token，同时受完整请求容量及可用输入预算八分之一约束。单个资源慢时省略该项，整次装配超过 2.5 秒时继续处理原请求。清单和追踪只记录来源、哈希、处置和用量。
- 公开持久请求的只读工具清单去掉 `update_task`。任务登记和证据续作走自动意图路由，只有相应业务意图及任务动作才开放写工具。历史内部调用兼容既有行为。意图路由没有接收 WorkItem 数据；原确认审批和执行绑定继续由 Kernel 校验。
- 公开 API/OpenAPI、生成客户端和数据库结构沿用当前版本。内部存储清单只增加受白名单约束的来源、分层与省略原因。新数据消息不进入 SDK 检查点和跨轮记忆编译。
- 复核后统一了只读提示词与工具准入。L3 记录快照时间与 WorkItem 事件游标，并区分 Session 当前目标修订和被保留业务事实的来源修订。能力和档位的冻结版本在发送模型请求前按当前角色重新授权。Knowledge 撤回、过期或失去读取权限时从模型引用中剔除。

| 影响范围 | 文件 |
| --- | --- |
| 新增 Kernel 源码和专项测试 | `application/work_item_context.py`、`work_items/agent_context.py`、`agent_runtime/sdk_execution/work_item_context.py`、`kernel/tests/test_agent_work_item_context.py` |
| WorkItem、身份与装配 | `work_items/{owner,references}.py`、`application/work_item_lifecycle_facts.py`、`access/research_auth.py`、`interaction/access.py`、`business_capabilities.py`、`composition.py`、`scripts/checks/import_boundaries/rules.py` |
| Agent 请求与工具策略 | `agent_runtime/{mode,prompts}.py`、`persistence/{context,run_queries}.py`、`tooling/{factory,task_tools}.py`、`sdk_execution/{types,execution_builders,initial_execution,resumed_execution}.py` |
| 上下文清单与追踪 | `agent_runtime/persistence/context_views.py`、`sdk_execution/{usage_capture,usage_context_views,usage_model_start}.py`、`agent_runtime/tracing.py` |
| 回归和文档 | `kernel/tests/{test_kernel_lifecycle,test_workflow_task_runtime_probe}.py`、`tests/contracts/test_domain_package_simulation.py`、`tests/e2e/test_monitoring_work_item.py`、`docs/capabilities/agent-discovery.md`、本报告 |

### 验证与风险

| 检查 | 结果 |
| --- | --- |
| `../bin/dev check --scope kernel` | 1,017 个 Kernel 用例通过，类型检查、Ruff、空白、核心导入和架构边界检查通过。[日志](../../.pytest_cache/gw-v21-phase3-arsgEXUc/kernel-reviewed.log) |
| `../bin/dev check --scope contracts` | 191 个合同用例通过，运行时 OpenAPI、Python SDK、TS/Ajv 生成一致。[日志](../../.pytest_cache/gw-v21-phase3-arsgEXUc/contracts-reviewed.log) |
| `python -B -m pytest -q -p no:cacheprovider kernel/tests/test_agent_work_item_context.py kernel/tests/test_agent_context_efficiency.py kernel/tests/test_work_item_references.py kernel/tests/test_workflow_task_runtime_probe.py` | 33 个用例通过。覆盖主体/项目/会话隔离、只读工具和提示词、历史来源、归档、角色撤销、过期或撤回知识、历史语义修订、重试、恢复、预算和日志正文隔离。[日志](../../.pytest_cache/gw-v21-phase3-arsgEXUc/focus-reviewed.log) |
| `python -B -m pytest -q -p no:cacheprovider tests/e2e/test_monitoring_work_item.py` | 2 个用例通过。隔离的 PostgreSQL、Data、Executor 和本地 Worker 验证已选能力、真实监测流程产物、精确发布身份、跨会话状态及读取权限撤销。临时数据库容器已停止。[日志](../../.pytest_cache/gw-v21-phase3-arsgEXUc/monitoring-reviewed.log) |
| 本地 Chat Completions 适配器与新增模块类型检查 | DeepSeek 和 MiniMax 转换器保留原用户消息与单独的数据消息。三个新增生产模块的专项类型检查通过。[适配器日志](../../.pytest_cache/gw-v21-phase3-arsgEXUc/adapter-roles.log) · [类型检查](../../.pytest_cache/gw-v21-phase3-arsgEXUc/context-types.log) |
| 架构复核、文档及变更检查 | 独立复核确认所提权限、Knowledge 生命周期、只读提示词和 L3 来源问题已修正。文档校验与 `git diff --check` 通过。 |

红灯用例确认原调用没有 WorkItem 数据。首次回归发现监测和任务探针使用只读对话写 Task，旧合同用例也期望该工具。已让登记和证据续作走现有的任务意图入口，并按新规则检查只读工具清单。阶段最终验收使用本地脚本模型和已物化工具链。真实模型供应商、生产数据与部署留待后续独立验证。

大型 WorkItem 的汇总仍需读取完整生命周期。整体装配超时会省略背景，追踪保留省略原因。跨会话只装配有来源的状态事实。之前会话的完整用户陈述和模型推断继续留在各自会话。

阶段 3 达到本地技术退出条件。当前工作区包含经核验的改动，供后续阶段继续使用。

## 阶段 4 数据工作空间与业务对象关联体验

### 实施方案

- **EXISTING**。Data 拥有资料、精确版本、字段画像、语义修订、质量记录与上传发布。Knowledge 拥有文档及版本。Kernel 的 WorkItem 关联命令已校验项目、主体、工作修订、精确资料以及历史语义。会话已有按输入角色绑定、结构化准备问题及冻结确认。
- **EXTEND**。工作详情展示关系与来源身份，支持选择精确资料、新上传后核对、知识版本、运行及发布成果。井、区和含水层使用三个既有对象种类，由用户选择来源字段并填写业务键。已有对象只按该来源解释。工作资料与会话的待发送输入、准备校验和确认采用分别呈现。
- **NEW**。复用数据库查看器承接工作资料检查和选择。在工作资料入口加载选中版本的质量与语义页，显示 Data 返回的缺失、异常、覆盖范围、CRS、垂向基准和已有质量运行。只采用匹配资料版本与语义版本的质量运行，未知保持待核验。会话选择输入角色时提供当前工作的可读资料，提交与重新准备继续使用现有 Session 命令。
- **DECISION**。使用现有公共合同和生成校验器。工作资料选择保留精确版本并沿用 WorkItem 修订及幂等请求 ID；跨项目、撤权、未完成发布和不可用服务均由原所有者复核。状态标签与字段事实来自服务，当前工作不建立主数据或全局业务键。
- 分项实施与验证。在 `DatabaseWorkbench.tsx` 与新增质量组件先验证精确版本、历史语义、检查状态及分页。在 `WorkItemMaterials.tsx`、引用选择组件与 `work-item-api.ts` 验证绑定、来源、对象表单、不可用和幂等恢复。通过 `App.tsx`、`SessionWorkContext.tsx` 接入角色选择与现有准备流程。覆盖撤权、跨项目、资料缺失、单位/坐标检查与窄屏浏览，并执行 Web、合同和本地集成回归。

### 实现与影响

- 基线为本地 `main@7a6e621162a51c55340e44167df42c334290f1c9` 加阶段 3 已验收的工作区改动。工作详情复用 Data 目录和上传发布。选择后先核对资料和质量，再提交 WorkItem 关联。回执不确定时重放相同的请求 ID、修订及载荷。关联列表分页展示，逐份查看才加载详细资料，不会为列表中的每个对象请求质量历史。
- 业务对象表单使用 WELL、REGION、AQUIFER，由用户指定来源字段与业务键。显示来源数据集、资料版本、语义修订、对象和校验和。知识目录和历史版本按页读取，撤回、过期或不可访问的知识显示安全状态。运行和命名成果关联走原有 Kernel 命令，成果选择同时核对发布及 Data 精确身份。
- 角色选择器提供本工作的可读资料。现有 Kernel 准备和 Executor 读入合同要求该资料版本的当前语义与唯一原始对象。历史关联可以检查和追溯，计算时用户须主动查看并选择当前可校验的候选。原关联保留精确历史身份。工作关联、会话草稿、准备校验及确认采用分别显示，当前角色和精确输入随现有消息合同提交。
- 独立复核定位了会话切换、撤权、模态框焦点和历史较深时的容量问题。选择器生命周期绑定当前会话与身份，回到页面时重新校验候选。质量查找一次读取最多两页，并允许继续向前查找，未知状态保持待核验。批量候选的附加使用 Data 对象唯一键，已绑定的历史语义优先保留供用户核对。
- 公开 API、OpenAPI、生成类型和数据库结构沿用现有版本。本阶段不增加 Data、Knowledge、Kernel 或 Executor 的资源所有权。能力校验和字段科学语义复用原准备合同。

| 修改范围 | 文件 |
| --- | --- |
| 新增资料工作区及检查组件 | `WorkItemMaterials.tsx`、`WorkItemOtherAssets.tsx`、`DataSemanticHistory.tsx`、`DataVersionQuality.tsx`、`LinkedWorkData.tsx` |
| 既有资料界面与精确读取 | `DatabaseWorkbench.tsx`、`UploadDialog.tsx`、`api.ts`、`resource-item.ts`、`types.ts` |
| WorkItem、Session 和交互 | `App.tsx`、`WorkItemDetails.tsx`、`WorkItemWorkspace.tsx`、`WorkItemSessions.tsx`、`SessionWorkContext.tsx`、`work-item-api.ts`、`use-drawer-controller.ts`、`workbench-focus.ts`、`work-item-workspace.css` |
| 测试与文档 | `DatabaseWorkbench.test.tsx`、`work-item-workspace.test.tsx`、`data-version-quality.test.tsx`、`work-item-other-assets.test.tsx`、《Agent 发现、理解与任务准备》及本报告 |

### 验证与退出条件

| 检查 | 结果 |
| --- | --- |
| `../bin/dev check --scope web` | 47 个测试文件、486 个用例通过。TypeScript、生产构建、Ruff 和差异空白检查通过。 |
| `../bin/dev check --scope contracts` | 191 个合同用例通过。运行时 OpenAPI 和 Python、TypeScript/Ajv 生成校验通过。 |
| `python -B -m pytest -q -p no:cacheprovider kernel/tests/test_internal_modules.py kernel/tests/test_work_item_references.py kernel/tests/test_work_items.py kernel/tests/test_business_capability_preparation.py` | 116 个 Kernel 用例通过，覆盖工作引用、权限、输入角色、字段、单位和空间条件。 |
| `python -B -m pytest -q -p no:cacheprovider tests/e2e/test_monitoring_work_item.py tests/e2e/test_composed_workflow.py::test_monitoring_trend_publishes_exact_results tests/e2e/test_composed_workflow.py::test_monitoring_trend_capability_requires_user_threshold` | 6 个端到端用例通过。使用隔离的 PostgreSQL、Data、Executor、本地 Worker 和脚本模型，覆盖准备阻塞、补参、确认和精确成果发布。 |
| 本地 Chromium 与生产构建 | 用真实 Kernel 会话与 Data 测试响应核对历史语义、质量及未知状态、业务对象来源、精确角色请求、390 像素窄屏和撤权。无 JavaScript 页面异常。真实服务的准备与发布由上述端到端用例核对。 |
| `python -B -m scripts.checks.check_documentation`、`git diff --check` | 文档与变更检查通过。 |

首轮回归确认旧界面缺少资料选择入口。最终回归覆盖切换会话时清除旧选择、重新聚焦后重新核验候选、质量历史的有限自动读取，以及同格式对象中明确选择原始对象。

历史语义和其他对象仍可按原身份追溯。用于计算时，需显式选择该数据版本的当前语义和唯一原始对象。未取得匹配质量记录时保持待核验。真实业务数据、外部模型供应商和生产环境留待独立发布验证。

阶段 4 达到本地技术退出条件。
