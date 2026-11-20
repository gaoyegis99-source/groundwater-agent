# 阶段 2　Workflow 平台

## 开始时的基线

本地 `main` 位于 `bd5d58ab63e16b2f5a59ceabcc7faf1c4c71f411`。阶段 0 和阶段 1 的报告、Operator 治理代码及证据留在工作区，作为本阶段输入。Groundwater 产品使用 `groundwater@6.2.0`，并保有 `6.1.0`。公开 HTTP 使用 v12。参见[阶段 1 报告](01_OPERATOR_GOVERNANCE.md)。

## 实施方案

| 范围 | 基线及处置 |
| --- | --- |
| EXISTING | 装配器和 Compiler 校验不可变 Workflow DAG 与精确 Operator。Executable Model 提供调用、入参、发布映射；Kernel 和 Executor 原有准入路径处理确认、运行与发布。 |
| EXTEND | 在现有产品装配中验证并发布可选的 Profile 和结果合同目录。从实际引用生成依赖图，使用已编译的节点与运行时容量评估资源上界。 |
| NEW | 新增版本化 Profile、结果合同及本地选择与结果查看入口。选择结果生成现有 v12 `workflowSelection` 和精确输入引用；查看结果读取现有 Workflow Run 和发布快照。 |
| DECISION | 试点全年监测趋势、季节趋势和用户自定阈值事件这一组合，沿用签入 Golden 的实际计算配置与原有 21 项输出。两项开关在此 Profile 中固定开启，由使用者决定阈值、对比方向、周期、字段及时间窗。暂设一个 Profile 档位。 |

替代方案之一是在 HTTP 消息协议中新增 Profile 标识，并由 Kernel 持久化。这需要独立批准的 API 和数据合同，且当前 Model 入口已有精确身份和参数冻结能力。本阶段采用装配产物与现有请求适配。执行确认仍由 Kernel 权威流程承担，Profile 可见角色仅用于本地入口，Kernel 按会话身份、数据访问及任务准入权限独立校验。结果投影要求调用者显式提供已知 Profile 身份，再核对快照中的精确 Model、Workflow 和固定参数。投影检查结果清单及发布状态。

变更覆盖装配器与运行时 schema 打包、Groundwater 产品配置和本地使用入口、阶段报告与回归测试。先覆盖无效引用、漂移的输出、不可调整参数、历史读取和发布不完整的反例，再实现。验收以使用现有预设的真实 Native 执行、Data 发布与精确读取为准，另运行原有组合拒绝、编译和产品装配门禁。合成 Golden 用于技术回归，地下水专业适用结论仍须对应业务证据。

## Workflow、Model 与产品入口

活动包有 18 个 Workflow、18 个 Executable Model、95 个节点、54 个被预设使用的精确 Operator 版本及 117 个具名输出。每个预设有一个 Model。Model 通过精确 `workflowRef` 绑定 DAG，另外声明工具、调用参数、数据角色映射、字段和语义检查以及发布配置。装配时注入已编译 Workflow，源 Model 定义没有节点与边。完整清单记录每个工作流的输出、绑定、参数来源、产品入口、组合限制及可用案例。参见[清单](02_WORKFLOW_INVENTORY.md)和[生成器](workflow_inventory.py)。

装配目录的 `workflow-platform.json` 从现有 Workflow、Operator、Model、Profile 和 Product UI 引用生成依赖图。Operator 边记录版本、实现摘要和节点位置。Workflow 通过直接的 Model 边支持原有预设，同时可经 Profile 关联同一个精确 Model。Model 边关联现有的产品 Model 入口。独立的 Business Capability 定义属于阶段 3。影响分析可从 `workflows[].operators` 查找变更版本并沿节点对应的 `profiles`、`models` 与 `entries` 查到入口。参见[装配实现](../../source/platform/assembler/assembler_workflow_platform.py)。

## Compiler 检查与缺口

| 检查项 | 当前行为 | 阶段 2 处理与限制 |
| --- | --- | --- |
| DAG、循环、可达输出和无用节点 | v6 Compiler 校验精确算子、节点绑定、环和所有节点到最终输出的可达性。v7 Composition 另验证多输入绑定、已用输入与参数。 | 直接消费现有编译结果。Profile 只配置同一个 DAG。不同拓扑要有独立 Workflow 定义。 |
| 端口 media type、format 与条件输出 | Compiler 传播格式对，按输入格式和参数解算条件输出。Executor 按实际绑定与参数重新执行合同验证。 | 结果合同核对编译端口及 Model 发布格式。试点用固定 Parquet 和 JSON 发布格式，未将它视为其他工作流条件格式的验证。 |
| 输入角色、数据绑定和参数完整性 | Model 声明绑定、Schema、字段类型和语义前提。Kernel 用当前轮精确 Data 证据准备并冻结参数，组合仍要求独立的规则和数据事实。 | Profile 校验本地参数名、既有 Model Schema、ISO 时间窗及两段比较期关系。本地角色用于入口可见性。Kernel 重新核对权限、当前 DataVersion、单位和时间覆盖。 |
| 资源预算 | Composition 有节点数、单节点内存与超时规则。Executor 使用模型并行度和 Worker 的运行时 slots、活跃内存预算与作业截止时间。 | Graph 对每个 Model 按运行时计算并发和声明内存的保守上界，列出大于运行时内存的节点。默认监测趋势 Model 并行度为 4，Native 2 slots、16 GiB，节点声明 8 GiB，上界为 2 个节点和 16 GiB。依赖关系、同租户其他运行或资源争用可使实际并发更低。观测峰值和预测时长仍需测量。 |
| 重复计算 | Compiler 校验拓扑和身份，Executor 按现有节点执行。 | 清单以精确实现、输入绑定和参数映射指纹提示结构相同节点。告警不替代编译，也不判定不同来源数据实际相同。 |
| 组合白名单与外部业务前提 | Kernel 与 Executor 使用同一装配白名单，候选编译验证语义证据。未满足的 CRS、单位或规则事实会生成明确诊断。 | 现有 62 个组合成员保持精确版本。预设可调用资格与动态组合资格独立。按对象的季节和频率周期唯一性、真实业务适用性及足够样本仍依赖源数据和节点行为。 |

实现参见 [Compiler 图](../../source/packages/workflow-compiler/src/platform_workflow_compiler/graph.py)、[条件格式](../../source/packages/workflow-compiler/src/platform_workflow_compiler/formats.py)、[组合编译](../../source/packages/workflow-compiler/src/platform_workflow_compiler/composition/execution.py)、[Kernel 准备](../../source/kernel/src/platform_kernel/agent_runtime/preparation_tool.py)与 [Executor 容量](../../source/services/executor/src/platform_executor_service/task_execution.py)。

## 监测趋势 Profile 与结果合同

| 项目 | 试点 |
| --- | --- |
| Profile | `groundwater.monitoring-trend.seasonal-and-threshold@1.0.0`，内容摘要 `sha256:5e75f667edf48f4c1ac19823480463d70841c4791b838aa2ef17d40f2611bbed` |
| Workflow | `groundwater.monitoring-trend@1.1.0`，定义摘要 `sha256:156c558d24d5495124cb1735d9b1c2ba0baffafa42a2dc296425891150a5b07e` |
| Model | `groundwater.monitoring-trend@3.1.0`，内容摘要 `sha256:43566799121f36c726bc0746308095866a79510e5c057a96b8b543f6dd19e259` |
| Result Contract | `groundwater.monitoring-trend.results@1.0.0`，内容摘要 `sha256:bf0cd63ec9bad85390725387c403d6d0adcd400212a0186dc4dfeabc40e7e4bb` |
| 参数与角色 | `admin`、`researcher` 可从本地入口选择。模型提供 `alpha=0.05` 和 MAD 阈值 `3.5` 的可调整默认值。Profile 固定启用季节趋势和阈值事件。事件阈值与比较方向由使用者填写。 |
| 输入 | 精确的 `observations` DataVersion，支持 CSV、Parquet、Arrow IPC。字段提供组标识、时间和值，Data 语义声明单位和覆盖时段。两次比较期不得重叠。按对象和时间桶满足两个直接读取原始观测的节点的唯一性要求。 |
| 输出 | 保留原有 21 个输出，合同按名称固定角色、种类、格式、业务含义、查看器、适用单位和时间范围。空间范围对本试点无适用项。去向包括趋势和事件明细、数据完整性及异常诊断、JSON 摘要。 |

合同角色接受 `PRIMARY_RESULT`、`DIAGNOSTIC`、`VISUALIZATION`、`SUMMARY`。试点产出数据表和摘要，部分数据表支持图表查看。每项输出保留质量证据入口。部分摘要只统计覆盖规模，具体可评价性以明细状态为准。技术验证使用合成 Parquet。Golden 快照验证场景参数、件数和内容摘要。另从通过的 replay 生成 scope 证据，标记实际运行的 10 个节点的精确实现版本，并绑定原 Golden 摘要。装配按已使用节点验证 scope，因此对验收范围以外的 Operator 添加版本时仍可保留该证据。参见[产品 Profile 和合同](../../source/products/groundwater/workflows/workflow-platform.v1.json)、[Schema](../../source/platform/assembler/workflow-platform.v1.schema.json)、[Golden scope](../../source/products/groundwater/workflows/evidence/monitoring-trend-golden-scope.v1.json)。

### 本地选择和结果查看

在 `source` 下使用锁定的项目 Python 运行下列命令。把 `bundle` 指向本地编译输出的内容寻址目录。

```bash
bundle=/absolute/path/to/assembly
profile_id=groundwater.monitoring-trend.seasonal-and-threshold
profile_version=1.0.0
profile_checksum=sha256:5e75f667edf48f4c1ac19823480463d70841c4791b838aa2ef17d40f2611bbed
python -m scripts.development.workflow_profile --assembly "$bundle" list
python -m scripts.development.workflow_profile --assembly "$bundle" select \
  --id "$profile_id" --version "$profile_version" --checksum "$profile_checksum" --input selection.json
python -m scripts.development.workflow_profile --assembly "$bundle" results \
  --id "$profile_id" --version "$profile_version" --checksum "$profile_checksum" --input snapshots.json
```

`selection.json` 包含 `role`、`inputs` 和 `parameters`。`inputs.observations` 是从获授权的 Data 读取中取得的完整 v12 Data 引用。将生成的 `inputReferences` 和 `workflowSelection` 放入现有 Session 消息请求，使用 `requestedMode=DIALOGUE`。工具只组装请求，用户仍通过现有 Session 确认。

`snapshots.json` 包含授权读取的 `run` 和 `publication` 两份 v12 快照。结果入口核对运行身份、Model 参数和输出合同，保留每个结果的精确 `dataRef`。仅当运行成功、发布为 `PUBLISHED` 且具名成果的格式及身份齐全时标记完整。业务验收状态为 `NOT_ASSESSED`。现有运行快照保存精确 Model 和 Workflow 以及执行参数。调用者保存本次使用的精确 Profile 引用，结果入口依据该引用判定兼容性。参见[选择和投影实现](../../source/scripts/development/workflow_profile.py)。

## 版本兼容和运行历史

| 变化 | 兼容规则 |
| --- | --- |
| 相同 ID、版本和摘要 | 允许重现同一契约。装配和请求经精确身份校验。过期摘要报错。 |
| 新增其他 Operator 版本、Profile 或产品入口 | 可和原版本并存。现有选择使用原绑定。Profile 的 Golden scope 锁定实际依赖节点。 |
| 向后兼容的字段补充、展示或新增可选能力 | 以新版本发布变更内容。保留已使用的旧版安装与装配或归档文件，使已有选择和历史读取仍能使用原合同。 |
| 工作流拓扑、计算含义、默认参数、必填输入、输出身份或格式变更 | 先确定受到影响的精确依赖，发布版本化定义及验证证据。拓扑和 Model 调用合同分别升级。已有执行仍使用其原有 DataVersion 和执行身份。 |
| 组合缺少许可或必需事实 | 保留现有 Kernel 和 Compiler 的明确拒绝或待补充状态。 |

内容寻址装配发布读写有锁保护。装配检测与当前本地发布相比的同版本内容变更。`workflow-platform.json` 和 `assembly-lock.json` 成对归档供历史解释使用。换用空缓存或者清理了旧包时，版本比较需使用保留的发布证据。历史 Workflow Run API 仍从持久化记录读取；旧版本可否再次派发取决于是否仍安装该精确 Model、Workflow 及算子。试点无公共 HTTP、OpenAPI、生成 SDK、前端类型或 SQLite/PostgreSQL Schema 变更。参见[发布存储](../../source/platform/assembler/assembler_publication.py)与[归档](../../source/scripts/maintenance/research_artifacts.py)。

## 影响文件

| 变更 | 文件 |
| --- | --- |
| 新增产品契约 | [Workflow 平台 Schema](../../source/platform/assembler/workflow-platform.v1.schema.json)、[Profile/Result 定义](../../source/products/groundwater/workflows/workflow-platform.v1.json)、[证据 scope](../../source/products/groundwater/workflows/evidence/monitoring-trend-golden-scope.v1.json) |
| 新增装配验证及本地入口 | [装配校验与依赖图](../../source/platform/assembler/assembler_workflow_platform.py)、[本地 Profile 入口](../../source/scripts/development/workflow_profile.py) |
| 装配、打包、保留和清单 | [产品 manifest](../../source/products/groundwater/product-manifest.json)、[manifest 加载](../../source/platform/assembler/assembler_manifests.py)、[装配主程序](../../source/platform/assembler/assembler.py)、[wheel 配置](../../source/platform/assembler/pyproject.toml)、[wheel 审计](../../source/scripts/release/check_wheel_contents.py)、[迁移资产清单](../../source/scripts/maintenance/migration_runtime.py)、[研究运行归档](../../source/scripts/maintenance/research_artifacts.py)、[当前能力文档](../../source/docs/development/current-capability-inventory.md) |
| 测试与夹具 | [Profile/合同](../../source/tests/assembler/test_workflow_platform.py)、[装配基线](../../source/tests/assembler/test_groundwater_generated_baseline.py)、[产品装配](../../source/tests/assembler/test_product_manifest.py)、[跨领域装配](../../source/tests/assembler/test_domain_packages.py)、[跨领域合同](../../source/tests/contracts/test_domain_package_simulation.py)、[跨领域 Web 夹具](../../source/tests/e2e/build_air_quality_web_fixture.py)、[wheel 审计测试](../../source/tests/architecture/test_wheel_contents.py)、[研究归档测试](../../source/tests/cli/test_research_artifacts.py)、[端到端回归](../../source/tests/e2e/test_composed_workflow.py) |

## 验证结果

使用锁定 CPython 3.12.8、已有离线 wheelhouse 和私有网络命名空间。端到端运行用已缓存的 PostgreSQL 16.10 镜像，禁用网络和镜像拉取，只通过工作区中的 Unix Socket 访问。模型提供方使用脚本样例，Kernel、Executor、Native 进程和 Data 发布走实际实现。临时数据库容器已移除。

| 命令或检查 | 结果 |
| --- | --- |
| `../bin/dev check --scope assembler` | 171 项测试通过，包括 30 项新 Profile、结果、证据与准入测试。装配、Ruff、导入和架构边界门禁通过。 |
| `../bin/dev check --scope kernel` | 912 项测试通过。Kernel 类型、Ruff、导入和架构边界门禁通过。 |
| `../bin/dev check --scope contracts` | 190 项跨服务测试通过。运行时 OpenAPI 快照、TypeScript 与 Python SDK 合同检查通过。 |
| `python -m pytest -q packages/workflow-compiler/tests packages/protocol-contracts/tests tests/architecture/test_wheel_contents.py tests/cli/test_research_artifacts.py tests/cli/test_migration_runtime.py` | 196 项通过。 |
| `workflow-golden --case monitoring-trend --output-dir stage-2-verified`，使用锁定 Python 的 `dev.main()` | Native Golden 通过，与签入快照差异为 0。使用的包目录摘要为 `sha256:3881423fa69deae67a4d31bbab2c202541b8c7ccaf7c1182497da74a67b672ab`。 |
| 下方四个精确端到端用例 | Profile 预设、无 Profile 历史精确引用、CRS 不匹配及缺失 CRS 全部通过，4 项 Rasterio 待弃用警告。 |
| 使用签入构建锁、缓存 wheelhouse 的 `uv build --wheel --offline --no-index` 及 `python -m scripts.release.check_wheel_contents platform-assembler` | wheel 可离线构建，模块与新 Schema 的归档内容和源码一致。 |
| `python ../plans/groundwater-agent-v2/workflow_inventory.py --check`、生成能力清单校验和 Profile CLI `list` | 清单可重现，目录可读取精确 Profile 引用。 |
| 本地装配对照 | Executor 配置、产品 UI、图标及 nginx 逐字节一致。Compose 仅替换本地装配路径后相同。Kernel 配置仅 manifest 摘要变化。62 条动态组合白名单保留，历史包的 18 个 Model 和 18 个 Workflow 文件仍与相应活动定义逐字节一致。 |

```bash
python -B -m pytest -q -p no:cacheprovider --tb=short --basetemp "$test_tmp/pytest" \
  tests/e2e/test_composed_workflow.py::test_monitoring_trend_publishes_exact_results \
  'tests/e2e/test_composed_workflow.py::test_agent_composes_confirms_executes_and_publishes[crs]' \
  'tests/e2e/test_composed_workflow.py::test_agent_composes_confirms_executes_and_publishes[missing-crs]'
```

两次趋势研究均使用相同的签入合成数据，确认待定时 Executor 作业数为零。确认后各成功执行 10 个 Native 节点，发布并精确读取 21 项结果。Kernel 重新启动后可读出相同的快照和 Data 引用，通过 10 项 Golden 摘要断言，包括 1 个可评价季节组及 3 个高于 10 m 的事件。Profile 结果完整状态为 `COMPLETE`，业务验收为 `NOT_ASSESSED`。两个 CRS 组合均明确拒绝，作业数为零。另用活动装配的 Kernel 规则入口验证 `timeseries.aggregate@1.0.0` 在原有预设可用，并且组合返回 `COMPOSITION_OPERATOR_UNSUPPORTED`。

初轮测试发现测试临时存储容量不足，以及 root 映射影响权限断言。增大私有临时空间、去掉权限测试的 DAC 覆盖能力后，相关检查复跑通过。早期 E2E 用项目基础解释器启动 Native 子进程时缺少 NumExpr，后续使用锁定的 Native 环境通过。最终运行记录可在本地查看。参见[端到端日志](../../.pytest_cache/gw-v2-phase2-QvsuOh7N/e2e-refinement.log)、[执行证据](../../.pytest_cache/gw-v2-phase2-QvsuOh7N/e2e-refinement-evidence/summary.json)、[Golden](../../.pytest_cache/gw-v2-phase2-QvsuOh7N/golden-runs/stage-2-verified/summary.json)与[装配对照](../../.pytest_cache/gw-v2-phase2-QvsuOh7N/assembly-parity.json)。

## 阶段退出

阶段 2 退出条件全部满足。已完成核心工作流的准备、编译、确认、执行、发布和精确读取，验证了重复选择、输出含义和 Data 身份、历史运行读取以及不支持组合的明确拒绝。

本阶段的证据来自合成技术回归。真实场地的科学适用性与业务验收需要独立证据。Profile 身份由本地调用者保留，当前 API 快照记录的是既有 Model、Workflow 和冻结入参。目录层可报告结构重复与容量上界，实测峰值和调度时长仍待独立验证。历史语义投影依赖保留的版本化目录及装配锁。Web 选择界面、在线模型场景及其他工作流的 Profile 验证未运行。

工作仅在当前本地工作区离线进行。阶段 1 变更保留，Git HEAD 和暂存区未变。本阶段在此结束。
