# 阶段 4 · 监测趋势 WorkItem 与业务对象关联

## 基线与实施方案

基线为本地 `main@bd5d58a` 加阶段 0–3 的未提交改动。以阶段 3 已验证的季节趋势与阈值分析能力为试点。阶段 3 的能力、Profile、Workflow、Model 和结果合同继续由原有装配与执行链路提供。

| 分类 | 现状与本阶段处理 |
| --- | --- |
| EXISTING | `PLATFORM_PROJECT_ID` 是单部署的租户范围。Kernel 从服务端校验的身份取得角色、项目和主体。现有服务间身份传递及当前项目校验继续适用。 |
| EXISTING | `tasks.session_id UNIQUE` 约束每个 Session 最多一个 Task，Task 不能跨 Session。任务交付由 Task 的修订、条件、证据和显式审阅决定。AgentRun 归属于 Session。只有 `task_runs` 中的记录证明某 AgentRun 对应某个 Task 修订。WorkflowRun 归属于发起它的 AgentRun 和 Session，Workflow 成功、Data 发布与 Task 验收各有独立的状态。 |
| EXTEND | Kernel 增加受项目与所有者双重约束的 WorkItem 和显式 Session 链接。一个 WorkItem 可组织多个 Session，一个 Session 最多归属一个 WorkItem。TaskLink、TaskRunLink、AgentRunLink、WorkflowRunLink 从既有关系读取，无需在两处写入同一状态。 |
| DECISION | 本地代码没有可作为 Region、Well、Aquifer 主数据的权威登记表。业务对象保存来源版本、语义修订、对象标识、字段及用户给出的来源键。Data 校验版本、语义、对象和字段。来源键是业务定位信息，阶段 4 不声明它已通过行级或跨数据集身份核验。 |
| NEW | 业务资产保存可追溯的 Data、Knowledge、Workflow、Result 引用。发布成果绑定所选 WorkflowRun 的唯一具名输出、发布 ID 和精确 Data 引用。归档保留底层关联和成果。 |

### 最小模型与接口

WorkItem 保存标题、可选说明、项目、所有者、修订和时间。生命周期包括 `ACTIVE` 与 `ARCHIVED`。归档的 WorkItem 可查询和审计。普通咨询仍由现有 Session API 创建。历史关联只接受用户明确提供的 Session ID，包括已归档会话。数据库迁移后，旧会话保留原有状态，待用户显式关联。

Kernel `/api/v12/work-items` 提供列表、创建、详情、审计和 Session、对象、资产、归档命令。列表和审计有游标。命令使用 `projectId`、`requestId`，更新命令同时使用 `expectedRevision`。查询和写入均使用服务端校验的身份。工作项复用 Session 的私有主体边界。每个 WorkItem 独立分配连续审计序号，记录已接受命令的主体、结果修订、变更标记和关联 ID。

SQLite v37 增加 `work_items`、`work_item_sessions`、`work_item_references`、`work_item_commands`。关系约束、修订推进与命令回执在同一写事务中生效。幂等键按项目和主体唯一，相同规范化命令重放原回执，键重用或新命令使用过期修订返回冲突。重复关联在当前修订可返回已有链接及无变化回执。显式 v36→v37 升级先保存备份，原表和数据得以保留。旧版恢复使用升级前备份。

Data 和 Knowledge 按当前用户权限验证精确版本。Workflow 只允许引用已关联 Session 的自有运行，沿用冻结的 Workflow 标识、版本和校验和。Result 关联前先核实 WorkflowRun 成功、实际发布成功、Data 版本同属该发布。查询重读资源权威，权限撤销、引用失效或服务暂时不可用时只返回链接状态。普通咨询、Task 和执行授权使用已有入口。

### 方案选择与影响范围

Session 内增加一个标签无法组织多会话，扩展 Task 会把跨 Session 的业务组织混入既有交付状态机。采用 Kernel 内的专用 WorkItem 持久化，复用其项目身份、私有权限、SQLite 写入协议与现有运行关系。WorkItem 也不引入独立微服务或主数据权威。

- Kernel 的 WorkItem 持久化、资源权威适配、装配、路由、Schema 和测试。
- SQLite v37 迁移、旧版迁移演练和版本化本地工件读取；Data、Knowledge、Executor 的存储结构沿用现状。
- Kernel 和产品 OpenAPI、Python SDK、Web TypeScript 与运行时校验、文档和合同测试。
- 基于阶段 3 监测趋势的离线验收，覆盖发布成果、Task 与运行关系、权限、历史迁移与归档。

主要风险是 WorkItem 关系被误用为执行授权、权限撤销后间接泄露资源、历史语义引用被当前语义替换，以及旧 SQLite 被静默升级。测试分别覆盖主体与跨项目隔离、读时重验、精确历史版本、事务回滚与升级回执。

## 实施结果

- **WorkItem** 持久化自身元数据、Session 归属、精确引用和命令审计。详情在同一个 SQLite 读事务中投影已关联 Session 当前的任务修订、TaskRun、AgentRun 和 WorkflowRun。任务交付继续读取已有 Task 合同。显式 Session 附加、命令回执和修订在同一写事务提交，Session 归属由服务端与 SQLite 校验。见 [WorkItem 所有者](../../source/kernel/src/platform_kernel/work_items/owner.py)、[任务和运行关系](../../source/kernel/src/platform_kernel/application/work_item_links.py)及 [测试](../../source/kernel/tests/test_work_items.py)。
- **Data 引用** 附加时核对 Dataset、Version、SemanticRevision、Object、格式、媒体类型、大小与校验和。历史语义修订走 Data 精确读取路由。对象附加同时验证来源字段，井号等业务键作为用户提供的定位信息。Knowledge 绑定文档、版本和校验和。Workflow 与 Result 的身份来自已关联 Session 的自有运行，Result 还要求真实发布为 `PUBLISHED`。读取时重验资源当前权限，隐藏不可读取的引用内容并保留链接 ID。见 [参考资源适配](../../source/kernel/src/platform_kernel/work_items/references.py)及 [测试](../../source/kernel/tests/test_work_item_references.py)。
- v12 新增八个 **WorkItem API** 操作。原有路径、操作和全部原有 Schema 逐项对比均与本地基线一致。Gateway 保留 WorkItem 前缀，Kernel DTO、所有者 OpenAPI、产品 OpenAPI、生成 Python 模型与操作、TypeScript 和按需 Ajv 校验随同更新。独立复核后，WorkItem 审计改为按单项编号，SDK 和 Kernel 同样对修订号与引用字节数执行严格整数校验。见 [HTTP API](../../source/docs/capabilities/http-api.md#workitem)、[路由](../../source/kernel/src/platform_kernel/api/routes/work_items.py)、[公开合同](../../source/products/groundwater/contracts/public-api-v12.json)及 [Web 校验回归](../../source/web/src/api-contract.test.ts)。
- **Kernel SQLite v37** 的冻结结构摘要为 `d647e55c4092e2a7650fa6f5c96705f8adef18e22427c5bed372c2489c897dee`。v36→v37 校验旧版本签名、创建内容寻址备份，并在提交前检查外键。工作项列表和审计采用索引游标。已确认且目标为 v36 的旧迁移计划需要重新生成，以便确认摘要绑定到当前目标。v37 兼容性修改覆盖快照校验、上下文报表、意图路由报表、Task 交付报表和旧版迁移演练。见 [表定义](../../source/kernel/src/platform_kernel/schema/work_items.py)、[迁移入口](../../source/kernel/src/platform_kernel/database.py)、[升级测试](../../source/kernel/tests/test_schema_contract.py)及 [维护步骤](../../source/docs/operations/deployment/operations.md)。
- **装配锁** 记录新增 API 对应的 Kernel HTTP 合同摘要，源码装配锁 SHA256 为 `754b3e3e8f1594e134f9d02d7384a02fde212d921d72358775049fdfc98ecc11`。阶段 3 的监测趋势 Capability 与 Profile，以及所选 Model、Workflow 和 21 项 Result Contract 沿用冻结定义。见 [装配冻结测试](../../source/tests/assembler/test_groundwater_generated_baseline.py)与 [阶段 3 精确身份](03_BUSINESS_CAPABILITY.md#能力合同与分层)。

逐文件范围见[阶段 4 变更清单](../../.pytest_cache/gw-v2-phase4-bbB7qfJs/changed-files.json)。

## 迁移与回读证据

使用阶段 3 成功监测运行的 SQLite 副本验证 v36→v37。普通初始化拒绝读取 v36，显式升级生成可打开的 v36 备份。升级后再次执行迁移可安全返回。对比原有 Session、Task、TaskRun、AgentRun、WorkflowRun、Confirmation、消息和事件的留存记录，外键检查通过。历史 Workflow 保留 `sha256:156c558d24d5495124cb1735d9b1c2ba0baffafa42a2dc296425891150a5b07e` 与 21 个输出引用。

通过生成的 Python SDK 创建 WorkItem、显式附加原 Session 并引用历史 WorkflowRun。重复附加返回原始修订 2 回执，工作项最终修订为 3，审计序号为 1、2、3。原有 SQLite 记录再次对比一致。见 [本地迁移演练结果](../../.pytest_cache/gw-v2-phase4-bbB7qfJs/historical-v36-final.log)。

## 验证结果

验证使用本地锁定的 CPython 3.12.8、Node、缓存依赖及 Docker 镜像，测试进程运行在隔离网络命名空间。监测端到端使用合成资料和脚本 Agent，实际运行 Kernel、Data、Executor 和原生算子。PostgreSQL 通过工作区 Unix Socket 提供临时存储。测试对实际发布的摘要内容核对大小、SHA256 及预期成果文件。

| 本地验证 | 最终结果 |
| --- | --- |
| `../bin/dev check --scope kernel` | 959 项通过。类型、Ruff、核心导入和架构边界检查通过。另对新模块运行 mypy，共 6 个文件通过。[日志](../../.pytest_cache/gw-v2-phase4-bbB7qfJs/kernel-exit.log) |
| `../bin/dev check --scope assembler` | 190 项通过。装配、冻结摘要、路由保留及架构检查通过。[日志](../../.pytest_cache/gw-v2-phase4-bbB7qfJs/assembler-exit.log) |
| `../bin/dev check --scope contracts` | 191 项通过。运行时 OpenAPI、公开合同、生成 TypeScript、Ajv 和 Python SDK 检查通过。[日志](../../.pytest_cache/gw-v2-phase4-bbB7qfJs/contracts-exit.log) |
| `../bin/dev check --scope web` | 42 个测试文件共 435 项通过。类型检查和生产构建通过。[日志](../../.pytest_cache/gw-v2-phase4-bbB7qfJs/web-exit.log) |
| `python -m pytest -q kernel/tests/test_work_items.py kernel/tests/test_work_item_references.py kernel/tests/test_schema_contract.py tests/contracts/test_public_api_contract.py` | 42 项通过，含并发写入、审计隔离、权限、历史语义和 SDK 严格输入。[日志](../../.pytest_cache/gw-v2-phase4-bbB7qfJs/focused-exit.log) |
| `python -m pytest -q kernel/tests/test_schema_contract.py tests/cli/test_{migration_runtime,research_artifacts,report_context_metrics,report_intent_routing_metrics,report_task_success_metrics}.py` | 38 项通过，覆盖备份、事务回滚、重复升级和维护工具。[日志](../../.pytest_cache/gw-v2-phase4-bbB7qfJs/cli-exit.log) |
| `python -m pytest -q tests/architecture` 分两次运行 | 230 项架构用例通过，隔离 Postgres 停止后单独复跑 doctor 用例通过。共 231 项完成。[架构日志](../../.pytest_cache/gw-v2-phase4-bbB7qfJs/architecture-masked.log)、[doctor 日志](../../.pytest_cache/gw-v2-phase4-bbB7qfJs/doctor-exit.log) |
| `python -m pytest -q tests/e2e/test_monitoring_work_item.py tests/e2e/test_composed_workflow.py` | 17 项通过，包括两个 WorkItem 验收。QGIS 水文回归因缺少本地镜像参数在此轮跳过。[组合回归](../../.pytest_cache/gw-v2-phase4-bbB7qfJs/e2e-final.log) |
| `python -m pytest -q 'tests/e2e/test_composed_workflow.py::test_agent_composes_confirms_executes_and_publishes[hydrology]'` | 按本地缓存镜像 ID `sha256:98b6fb79b7c12a1cf048a43d5ddcddcf7342b85892d4bd1101c4ceb7e660b917` 补跑，1 项通过。端到端共 18 项实际通过。[QGIS 补跑日志](../../.pytest_cache/gw-v2-phase4-bbB7qfJs/e2e-qgis-exit.log) |
| `git diff --check` 与新增文件逐一空白检查 | 通过。Git HEAD 和暂存区与起始一致。保留 41 个原有跟踪文件的 diff，另外 6 个已有改动文件仅扩展本阶段涉及的内容。[清单](../../.pytest_cache/gw-v2-phase4-bbB7qfJs/changed-files.json) |

监测趋势运行的 10 个节点均成功，Data 原子发布 21 项输出。WorkItem 记录一个已归档旧 Session 和一个活动 Session。Task 在原 Session 达到修订 2 和 `COMPLETE`。重启后重开 WorkItem 并读取阈值事件摘要，`threshold-event-summary` 为 398 字节，SHA256 为 `acf7ba6d9cd3f644dae94929fe6bd6be0bf82aa515e400dd7e52796160e50779`。归档后再次读取仍获得同一内容。另一验收将 Data 的实际权限从 PROJECT 改为 PRIVATE，再恢复为 PROJECT，确认引用依次可读、隐藏、恢复原有精确版本。见 [本次端到端 SQLite 证据](../../.pytest_cache/gw-v2-phase4-bbB7qfJs/e2e-final-evidence/summary.json)及 [测试](../../source/tests/e2e/test_monitoring_work_item.py)。

最初的定向测试复现了 WorkItem 路由缺失。首轮真实 Data 验收要求在合成数据中补充井编号的语义字段。独立复核复现的跨主体审计序号侧信道与 SDK 布尔值转换均已修正并复跑。广泛架构扫描首次遇到阶段 3 生成物目录、测试临时路径继承 Git 根和活动测试 Socket，最终分别以私有挂载、Git 搜索边界和停容器后的复跑验证。组合回归仍有上游 Rasterio、NumPy 弃用提示。

## 退出条件与适用范围

| 阶段 4 验收 | 结果 |
| --- | --- |
| 一个 WorkItem 可关联多个 Session | 通过。实际试点中同时链接两个 Session，并读取原 Task 与运行关系。 |
| Session 和 WorkItem 可各自归档 | 通过。Session 归档后保留 WorkItem，WorkItem 归档后保留全部链接和 Data 原内容。 |
| 普通咨询继续工作 | 通过。咨询可使用独立 Session，WorkItem 按显式需要创建。 |
| 历史关联迁移幂等 | 通过。阶段 3 原始副本保留历史，重复命令回放原响应和修订。 |
| 资源权限由权威服务核验 | 通过。真实 Data 撤权及恢复测试通过，Knowledge、Workflow 和发布身份由适配器测试核验。 |
| 权限与项目隔离 | 通过。覆盖认证、角色、跨主体、伪造身份、跨项目选择和关联。 |

阶段 4 退出条件满足。当前业务对象定位需要 Data 中明确的语义字段，用户提供来源键。区域、监测井、含水层的主数据归属仍待业务决策。测试结论覆盖合成数据下的业务组织、现有执行链路和发布回读。真实场地适用性、浏览器体验及实际部署升级留待相应验收，本阶段的 SQLite 升级仅使用本地副本。改动保留在当前工作区。
