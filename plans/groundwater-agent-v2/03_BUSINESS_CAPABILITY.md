# 阶段 3　Business Capability 与 Agent 能力选择

## 基线与实施计划

在本地 `main@bd5d58ab63e16b2f5a59ceabcc7faf1c4c71f411` 上续作，保留阶段 0 至 2 的工作区内容。活动领域包是 `groundwater@6.2.0`。监测趋势已有精确的 Workflow、Executable Model、季节趋势和阈值事件 Profile、21 项成果合同及离线执行证据。参见[阶段 2 报告](02_WORKFLOW_PLATFORM.md)。

| 分类 | 实施 |
| --- | --- |
| EXISTING | Intent Router 决定当前轮意图与提案权限。Kernel 精确选择 Model 并准备 Data，冻结 Confirmation 后派发。Compiler 和 Executor 分别验证现有预设及动态组合。Web 已有首页示例、详情抽屉、身份和项目能力快照。 |
| EXTEND | 将业务定义从装配映射到 Kernel 和 Product UI。利用现有选择记录保存精确 Capability、Profile、Model、Workflow 和结果合同身份。对已匹配业务请求使用现有唯一 Model 路径，确保准备阻塞时仍保持该范围。 |
| NEW | 试点 `groundwater.monitoring-trend` Business Capability，把监测趋势、季节变化和用户阈值事件作为同一明确业务目标。定义关键决策来源、首页入口、请求选择、准备检查和 trace 投影。 |
| DECISION | 采用产品显式声明的 CORE、SPECIALIZED、ADVANCED、EXPERIMENTAL 分层。仅此次指定且有阶段 2 证据的试点声明为 CORE 默认入口。其余现有 Model 继续通过原入口使用，后续分层以产品方批准为准。 |

方案比较了在公共消息协议加入 Capability ID、单独建立 Agent 或工具搜索能力，以及在既有意图路由中使用产品装配的业务匹配。采用装配配置加已有路由与选择存储。服务端通过经校验的项目角色和产品定义匹配，阶段 2 的固定参数及精确引用进入同一准备、确认与历史读取路径。现有 v12 和数据库结构继续使用。

业务计划须说明资料、字段、已知时段、来源和剩余的重要选择。只使用请求中明确给出的科学参数、已验证的唯一 Data 元数据和精确 Model 默认值。用测试覆盖不完整参数、角色与版本、无 Catalog 回退、准备绕过、确认期间修改绑定及传统预设兼容。Web 从同一装配得到详情和成果预览，按服务端返回身份和装配模型过滤。最终使用合成资料验证实际 Native 执行并复跑组合准入。

影响范围为产品 capability 声明及装配、Kernel 配置与 Agent 准备和确认边界、Web 首页与详情、相关测试及本报告。

## 能力合同与分层

产品声明以内容摘要装配。每项能力包含 ID、版本、名称、业务目标、适用角色、展示策略、依赖服务、限制和验证状态。输入角色、格式、前置条件及输出含义由阶段 2 的 Profile 与结果合同投影。装配要求精确引用存在，并将参与者角色限制为 Capability 与 Profile 的交集。生成的依赖边带 Capability 和 Profile 的精确引用。同一 Model 可以链接到不同 Profile 对应的能力入口。参见[产品定义](../../source/products/groundwater/capabilities/business-capabilities.v1.json)、[契约 Schema](../../source/platform/assembler/business-capabilities.v1.schema.json)和[装配校验](../../source/platform/assembler/assembler_business_capabilities.py)。

| 类型 | 展示与执行规则 |
| --- | --- |
| CORE | 具备技术验证、声明默认可见且角色、依赖、精确 Profile 均满足时进入首页和自动选择。 |
| SPECIALIZED | 可在符合角色及装配约束的能力目录查看详情。 |
| ADVANCED | 能力详情需要同时给出输入、依赖和使用限制，通过原有入口显式使用。 |
| EXPERIMENTAL | 从默认展示与自动执行路径排除。 |

本阶段登记一个 CORE 试点。其业务范围是同时分析监测趋势、季节变化和使用者指定的阈值事件。只请求一般监测趋势时可以继续使用历史预设及其默认参数。阶段 2 的合成验证支撑 `TECHNICALLY_VALIDATED`。真实场地适用性和业务验收需各自的证据。

| 身份 | 精确选择 |
| --- | --- |
| Business Capability | `groundwater.monitoring-trend@1.0.0` · `sha256:8f88b0d269b128748a08c6b30af689811930ac19199a9cc0a4ee3ad57ca27300` |
| Workflow Profile | `groundwater.monitoring-trend.seasonal-and-threshold@1.0.0` · `sha256:5e75f667edf48f4c1ac19823480463d70841c4791b838aa2ef17d40f2611bbed` |
| Executable Model | `groundwater.monitoring-trend@3.1.0` · `sha256:43566799121f36c726bc0746308095866a79510e5c057a96b8b543f6dd19e259` |
| Workflow | `groundwater.monitoring-trend@1.1.0` · `sha256:156c558d24d5495124cb1735d9b1c2ba0baffafa42a2dc296425891150a5b07e` |
| Result Contract | `groundwater.monitoring-trend.results@1.0.0` · `sha256:bf0cd63ec9bad85390725387c403d6d0adcd400212a0186dc4dfeabc40e7e4bb` |

## Agent 选择、澄清和确认

1. Kernel 接收现有 v12 `AUTO` 请求，用已通过鉴权的项目、会话和角色筛选装配的业务范围。产品词组匹配工作目标，并识别用户已明确填写的科学值。现有 Intent Router 仍决定是执行请求、咨询、比较还是工作流草稿。比较及高级草稿使用原有路由范围。
2. 在适用的业务执行轮，Kernel 只向 Agent 提供一个精确 Model 的提案路径。能力咨询仍按只读权限处理。匹配后缺少输入或字段也保持同一个范围，Catalog 及组合工具不进入该轮工具集。动态组合沿用既有编译、白名单、事实核验和确认。
3. `prepare_task_inputs` 按当前会话和项目核对精确 DataVersion、语义、对象与摘要。合法的输入格式来自 Profile 端口。能从结构类型和单位语义唯一确定的字段、以及已声明的时间覆盖可作为参数。保留 `DATA`、`USER`、`WORKFLOW_PROFILE`、`MODEL_DEFAULT` 来源。重复候选、缺失材料或不可核验的单位和时间生成结构化阻塞。季节、频率、统计方法、两个比较期及阈值方向中的缺失选择以当前准备问题为准。
4. 阈值只接受明确的当次赋值或有边界的事件筛查指令。紧接当前阻塞准备时，也接受完整的简短补答。旧报告中的阈值和标明尚未确定的值保持待输入。显式单位保留并与资料声明比较。续接会保留该准备的精确版本与已提供参数，重新核验资料后再准备。请求 ID 重放按原消息身份处理。
5. 提案必须对应本轮 READY 的准备记录。业务计划采用通过核验的输入、参数、结果预期和限制。Confirmation 冻结旧路径所需的精确 Model、Workflow、参数与输入，确认前作业数为零。如果修改了已准备的业务参数，需重新准备后确认。启动前发现 Capability 或 Profile 已更换时，保存明确的失败状态。已形成的待确认方案保留其冻结定义，实际派发仍验证精确 Model、Workflow 和 Data。

已有授权的 Agent trace 返回精确的 Capability、Profile、Model、Workflow 与 Result Contract 引用。配置选择与历史读取共用当前持久化结构。参见[业务选择器](../../source/kernel/src/platform_kernel/business_capabilities.py)、[选择与续接存储](../../source/kernel/src/platform_kernel/interaction/message_requests.py)、[权威准备](../../source/kernel/src/platform_kernel/agent_runtime/preparation_tool.py)、[提案校验](../../source/kernel/src/platform_kernel/agent_runtime/result_processing.py)与[历史 trace](../../source/kernel/src/platform_kernel/api/routes/agent_runs.py)。

## 首页与成果预览

同一装配生成 Product UI 的首页入口和业务详情。从服务端取得当前项目身份、角色、服务和已安装模型后，Web 以模型和工作流的完整身份匹配可见能力。详情给出可选 Profile、资料格式、前置条件、成果含义和使用限制。填入对话只生成可编辑草稿，可撤销并恢复原工作流选择。业务参数阻塞显示具体需补充事项。参见[装配投影](../../source/platform/assembler/assembler_ui.py)、[首页](../../source/web/src/App.tsx)、[业务详情](../../source/web/src/ProductDrawers.tsx)与[Web 可见性过滤](../../source/web/src/product-ui.ts)。

## 影响范围与兼容

- 产品和装配新增业务能力声明与 Schema，扩展 [产品 manifest](../../source/products/groundwater/product-manifest.json)、[装配](../../source/platform/assembler/assembler.py)、[依赖图](../../source/platform/assembler/assembler_business_capabilities.py)及[当前能力清单](../../source/docs/development/current-capability-inventory.md)。wheel 配置和内容检查纳入新模块与 Schema。
- Kernel 扩展静态装配配置、持久化选择读写、工具范围、数据准备、待确认动作校验和权威 trace。继续使用现有 SQLite 表与 v12 HTTP Schema、OpenAPI 和生成 SDK。产品中未装配本目录的旧路径仍可使用。
- Web 扩展 Product UI v1 的可选业务配置及生成验证器。配置随本次 Web 构建装配，旧产品配置仍可读取。前端角色过滤与服务端鉴权分别校验。
- 针对 Capability 新增[装配与版本化规则](../../source/tests/assembler/test_business_capabilities.py)、[Kernel 选择和生命周期](../../source/kernel/tests/test_business_capability_selection.py)、[权威准备](../../source/kernel/tests/test_business_capability_preparation.py)、[Web 可见性与输入](../../source/web/src/business-capability.test.tsx)及[离线端到端](../../source/tests/e2e/test_composed_workflow.py)回归。跨领域夹具按其自身产品配置装配。逐文件范围见[变更清单](../../.pytest_cache/gw-v2-phase3-DJgQbDhm/changed-files.json)。

阶段 2 对照验证表明，Executor 运行配置、62 项组合准入和历史领域版本均保留。Product UI 新增业务投影，Kernel 新增业务注册表与当前 manifest 摘要，既有 Workflow Profile、Result Contract 和 Model 身份保持精确。参见[本地装配对照](../../.pytest_cache/gw-v2-phase3-DJgQbDhm/assembly-parity.json)。

## 验证与证据

验证在私有网络命名空间中使用锁定 CPython 3.12.8、已有 Node 工具链和离线 wheelhouse。端到端以已缓存的 PostgreSQL 16.10 容器和工作区 Unix Socket 隔离状态，模型使用脚本样例。测试使用了真实 Kernel、Executor、Native 子进程与 Data 发布。Compose 开发配置、原有实例与 Git 暂存区没有参与测试写入。

| 本地命令 | 结果 |
| --- | --- |
| `../bin/dev check --scope assembler` | 188 项测试通过，含 17 项 Capability 装配、来源与阈值识别用例。Ruff、导入及架构边界已通过。 |
| `../bin/dev check --scope kernel` | 936 项测试通过。Kernel 类型、Ruff、导入及架构边界检查通过。 |
| `../bin/dev check --scope contracts` | 190 项通过。运行时 OpenAPI 与 TypeScript、Python SDK 合同检查通过。 |
| `../bin/dev check --scope web` | 42 个测试文件共 434 项通过。Web 类型检查、构建、Ruff 通过。 |
| `python -m pytest -q kernel/tests/test_business_capability_selection.py kernel/tests/test_business_capability_preparation.py tests/assembler/test_business_capabilities.py` | 41 项通过，含期望输出与业务身份装配、角色过滤、参数引用、比较和草稿范围、过期启动、强制 READY 以及可信资料推断。 |
| 缓存构建锁与 wheelhouse 下的 `uv build --wheel --offline --no-index`，随后使用 `scripts.release.check_wheel_contents` 和 `tests/architecture/test_wheel_contents.py` | 实际 wheel 内容核对通过。打包架构测试 49 项通过。 |
| 生成能力清单及装配对照，使用 Node 加载生成的 Product UI 验证器 | 清单可重现。实际 Product UI 摘要与装配锁相同，业务预览覆盖 21 项精确合同成果。 |

端到端验证命令在 `source` 下由[本地隔离脚本](../../.pytest_cache/gw-v2-phase3-DJgQbDhm/e2e.sh)运行。

```bash
python -B -m pytest -q \
  tests/e2e/test_composed_workflow.py::test_monitoring_trend_publishes_exact_results \
  tests/e2e/test_composed_workflow.py::test_monitoring_trend_capability_requires_user_threshold \
  'tests/e2e/test_composed_workflow.py::test_agent_composes_confirms_executes_and_publishes[table]' \
  'tests/e2e/test_composed_workflow.py::test_agent_composes_confirms_executes_and_publishes[crs]' \
  'tests/e2e/test_composed_workflow.py::test_agent_composes_confirms_executes_and_publishes[missing-crs]'
```

上述 7 项均通过，存在 4 条 Rasterio 待弃用警告。自然语言 `AUTO` 请求未填写 `workflowSelection`，由 Kernel 选择唯一监测趋势 Model 与精确 Profile。Agent 工具集不出现 Operator Catalog 或其他可执行 Model，同请求 ID 重放保留同一个 Run。准备快照记录权威参数来源。修改已准备阈值被拒绝，原确认完成后才创建作业。实际完成 10 个 Native 节点，发布并精确读取 21 项成果，Kernel 重启后可继续读取，10 项 Golden 摘要一致。技术样例得到 1 个可评价季节组、3 次高于 10 m 的事件。

缺失阈值时，脚本 Agent 即使传入完整计算参数，服务端仍生成 `CAPABILITY_PARAMETER_REQUIRED`。用户只补答当次阈值后重新准备，沿用原 Data 版本、对象及语义，无须再次附加。补答用例停留在确认，Executor 作业数为零。原有表格动态组合实际创建并执行作业，CRS 不匹配和缺失 CRS 都在准入时拒绝并保持零作业。参见[端到端日志](../../.pytest_cache/gw-v2-phase3-DJgQbDhm/e2e-definitive.log)与[SQLite 执行证据](../../.pytest_cache/gw-v2-phase3-DJgQbDhm/e2e-definitive-evidence/summary.json)。

最终意图范围改动后再次运行 `test_monitoring_trend_publishes_exact_results[capability]` 与 `test_monitoring_trend_capability_requires_user_threshold`，2 项均通过。临时数据库和 Socket 已清理。参见[末轮日志](../../.pytest_cache/gw-v2-phase3-DJgQbDhm/e2e-intent-gates.log)。

初轮回归发现请求引用需要使用现有 camelCase wire 字段，以及旧报告数值可能被误解为当次阈值。前者按已有 Data 引用边界修正，后者收紧为当次赋值、明确事件指令和针对当前问题的简短补答，并增加历史引用和未定阈值反例。独立复查发现过期选择的启动失败需要留下可投影的 Run，现已通过回归验证失败落库和会话恢复。

## 风险与验证边界

- 匹配范围是当前产品中声明的监测趋势试点词组与参数语句。模糊输入保留待确认或待补充状态。批次范围外的能力暂由原有 Model 入口和动态组合处理。
- 技术验证使用合成监测数据和脚本化模型。全表周期唯一性及数值条件仍由既有 Native 算子验证。科学适用性和业务验收需要各自的现场证据。
- 展示与服务端选择由同一装配生成。权限变化以重新鉴权和装配时的精确接口为准。历史解释依赖保留的装配锁和配置。
- Web 使用 Vitest、静态验证器和生产构建核查。离线约束下未运行浏览器或真实在线模型。

## 阶段退出

阶段 3 验收通过。当前监测业务请求优先选择预定义 Capability 和精确 Profile。缺失资料和科学参数有可操作的解释，匹配轮没有反复 Catalog 搜索。原有动态组合继续通过当前 Compiler、白名单和确认。自然语言入口与生成的首页、资料要求和成果预览均已验证。

工作仅在当前本地工作区离线进行。阶段 0 至 2 的成果保留，Git HEAD 和暂存区未变。阶段 3 在此结束。
