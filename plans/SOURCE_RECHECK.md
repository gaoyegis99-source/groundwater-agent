# Groundwater V6 本地源码复核

## 1. 复核结论

- 复核日期：2026-11-10（Asia/Shanghai）。
- 本地 HEAD：`3354fc2d959219f399ed667b993dcbdf3d0db89e`，与计划基线一致。
- Git 工作区：保留本目标持续实施形成的未提交源码修改与用户提供的未跟踪 `plans/`；没有 reset、覆盖用户文件或切换远程源码。
- 当前领域清单：10 个 executable models、10 个 workflow definitions、7 个 knowledge documents、1 个 execution case。
- 当前 Operator catalog：109 个条目，其中 native 83、qgis 15、remote_http 11。
- 计划中的 P0、已有模型完善、新增 Kriging/规则分类/DRASTIC、Pastas/MF6 独立 spike、快速反馈卡、文档一致性及三轮宿主 gate 收口均已完成；实施卡剩余 0。
- 当前仍有三组条件性门禁：未物化的锁定工具链/wheelhouse、受用户边界保护的默认 Native/QGIS runtime，以及 Compose/live-browser/真实 Provider/远程服务/发布验收。

首次只读复核的历史证据保留在下文；当前完成度和最新权威命令以各卡状态及 `90_PROGRESS.md` 的 GATE-03 日志为准。

## 2. 实际源码结构与当前功能

| 模块 | 当前职责与已实现能力 | 本地规模快照 |
|---|---|---|
| `source/web/` | 会话、Confirmation、任务工作台、数据目录、结果卡、表格/图表/空间查看器、SSE 与快照恢复 | `src` 234 个文件，28 个测试文件 |
| `source/kernel/` | Session、Intent Router、Agent、Preparation、Confirmation、Task、WorkflowRun 投影、恢复与结果终结 | `src` 185 个文件，147 个测试文件 |
| `source/services/data/` | 不可变 DatasetVersion、descriptor/semantics、preview/query、publication、quality、MLOps、lineage | `src` 75 个文件，40 个测试文件 |
| `source/services/knowledge-next/` | 知识版本、检索、Evidence read、receipt、导入与生命周期 | `src` 38 个文件，28 个测试文件 |
| `source/services/executor/` | Job、节点调度、worker lease、重试、恢复、结果发布 | `src` 39 个文件，36 个测试文件 |
| `source/packages/operator-pack/` | Native/QGIS/remote_http 算子及 smoke/runtime | `src` 64 个文件，46 个测试文件；另有独立 Native 门禁 |
| `source/packages/workflow-compiler/` | Workflow v6 纯编译与图校验 | 保持 cardinality=`one`、精确端口、artifact nodeOutput、DAG、参数和输出校验 |
| `source/domains/groundwater/6.0.0/` | 地下水领域模型、工作流、知识和固定执行案例 | 10 models / 10 workflows |

当前 10 个领域模型和同名工作流为：

1. `groundwater.flow-field`
2. `groundwater.monitoring-coverage`
3. `groundwater.monitoring-trend`
4. `groundwater.render-map`
5. `groundwater.source-sustainability`
6. `groundwater.summary`
7. `groundwater.transfer-learning-analysis`
8. `groundwater.kriging`
9. `groundwater.rule-classification`
10. `groundwater.drastic`

当前代码已经具备且后续修改必须保留的关键保护：

- Preparation 会重新读取精确 Data version、核对 version identity，并执行 `PINNED` / `CURRENT_REQUIRED` 策略。
- Confirmation 保存走不可变修订，Web 不直接创建 Job。
- Session stream 同时使用 SSE event id 与服务端 snapshot reconciliation，并有会话 generation/AbortController 防止旧会话响应回写。
- Session results 后端查询只投影每个 action 的最高 WorkflowRun attempt。
- Workflow Compiler 已拒绝未知算子、错误端口、cycle、不可达节点、非法参数和不支持的 many cardinality。
- Workflow Draft 固定 `execution_allowed=false`，不能旁路 Confirmation/Executor。
- Data、Knowledge 和 Executor 已存在完整服务边界，不需要创建平行存储或执行系统。

## 3. P0 缺陷逐项复核

### CHAT-01 / UI-01 Confirmation 数据资源加载

状态：**RESOLVED（2026-11-10）**。以下条目保留首次复核时的缺陷证据；实现与验证结果见 `90_PROGRESS.md` 的 CHAT-01 日志。

- `source/web/src/ConfirmationEditor.tsx` 的资源 effect 直接执行 `data_list_datasets -> Promise.all(data_get_version) -> setResources`。
- 没有 loading、error、retry 或部分失败状态，也没有 `.catch()`。
- 没有 AbortController；项目切换或卸载不能取消旧请求。
- 任一版本详情失败会使整个 Promise 链 rejection，用户界面仍表现为普通空列表。
- `source/web/src/ConfirmationEditor.test.tsx` 仅覆盖参数编辑和 focus/suggested value 两个成功场景。

涉及源码：

- `source/web/src/ConfirmationEditor.tsx`
- `source/web/src/api.ts`
- `source/web/src/api-contract.ts`
- `source/web/src/types.ts`

目标测试：

- 列表加载中、成功、空列表、列表失败、单个版本失败、retry、项目切换/卸载取消。
- 冻结 action arguments 与 input references 不因资源刷新而改变。

验收命令：

```bash
cd source
npm test --workspace web -- src/ConfirmationEditor.test.tsx
npm run typecheck --workspace web
../bin/dev check --scope web
```

### CHAT-02 / UI-02 Confirmation JSON 参数编辑

状态：**RESOLVED（2026-11-10）**。以下条目保留首次复核时的缺陷证据；实现与验证结果见 `90_PROGRESS.md` 的 CHAT-02 日志。

- `SchemaField` 的 object/array 分支直接以 `JSON.stringify(value)` 控制 textarea。
- `JSON.parse` 失败时 catch 为空，只保留最后合法值；用户输入会立即回退且看不到错误。
- 保存按钮只检查 `changes.length`，没有 JSON draft error gate。
- 当前测试没有 object/array、非法 JSON、恢复合法 JSON或保存值类型断言。

涉及源码和验收命令同 CHAT-01；先新增失败测试，再实现 raw draft + inline error + save gate。

### PROG-01 / UI-03 Session result authoritative replacement

状态：**RESOLVED（2026-11-10）**。以下条目保留首次复核时的缺陷证据；实现与验证结果见 `90_PROGRESS.md` 的 PROG-01 / UI-03 日志。

- `source/web/src/use-session-stream.ts` 以 `workflowRunId` 建 Map，只有旧项不存在或 `remoteOutputs` 为空时才写入服务端新结果。
- retry 会产生新的 WorkflowRun id；后端 `list_session_results()` 只返回每个 action 的最高 attempt，但前端会保留已不在权威响应中的旧 WorkflowRun。
- 即使 workflowRunId 相同，只要旧 outputs 非空，更新后的服务端 output set 也不会替换。
- `Result` 已含可选 `attempt`，但当前 merge 未使用；没有 hook/merge 单测。

涉及源码：

- `source/web/src/use-session-stream.ts`
- `source/web/src/types.ts`
- `source/web/src/api-contract.ts`
- `source/kernel/src/platform_kernel/agent_runtime/persistence/context.py`
- `source/kernel/tests/test_session_results.py`

目标测试：旧 attempt 有 outputs 后被新 attempt 原子替换；旧 WorkflowRun 被权威快照移除；迟到旧请求不能覆盖新会话/新 generation。

验收命令：

```bash
cd source
npm test --workspace web -- src/session-stream-state.test.ts
npm run typecheck --workspace web
.venv/bin/python -m pytest -q kernel/tests/test_session_results.py
../bin/dev check --scope web
../bin/dev check --scope kernel
```

### PROG-02 / UI-04 Workflow Draft cache invalidation

状态：**RESOLVED（2026-11-10）**。以下条目保留首次复核时的缺陷证据；实现与验证结果见 `90_PROGRESS.md` 的 PROG-02 / UI-04 日志。

- `draftIds` 仅包含未缓存或缓存状态为 `GENERATING` 的 id。
- draft 一旦缓存为 `COMPLETE` / `FAILED`，后续 `workflow_draft.*` dirty refresh 和 reconnect 都不会重新读取该 id。
- merge 也拒绝用新 terminal projection 替换已缓存 terminal projection。
- `session-stream-state.test.ts` 只断言事件会令 drafts dirty，没有断言 dirty 后实际重取与替换。

涉及源码：`use-session-stream.ts`、`session-stream-state.ts` 及其测试；应抽取可测试的权威 merge/cache 策略。

### VIEW-01 / UI-05 GeoViewer lifecycle

状态：**RESOLVED（2026-11-10）**。以下条目保留首次复核时的缺陷证据；实现与验证结果见 `90_PROGRESS.md` 的 VIEW-01 / UI-05 日志。

- `source/web/src/viewers/SpatialViewers.tsx` 的主 effect 依赖 `[url, colorField]`。
- 每次着色字段变化都会重建 `VectorSource`、`VectorLayer` 和 `Map`，并重新请求 URL。
- `source.once('change')` 使用创建 effect 时的 `colorField` 闭包；首次自动选择字段后会触发第二次完整建图。
- cleanup 只有 `map.setTarget(undefined)`，没有显式清理 source/listeners。
- 现有 Playwright 只断言控件可见，没有实例数、请求数、样式/图例一致性测试。

涉及源码：

- `source/web/src/viewers/SpatialViewers.tsx`
- `source/web/src/ArtifactViewer.tsx`
- `source/web/src/ArtifactViewer.test.tsx`
- `source/web/e2e/v12-database-formats.spec.ts`

验收命令：

```bash
cd source
npm test --workspace web -- src/ArtifactViewer.test.tsx
npm run typecheck --workspace web
../bin/dev check --scope web
```

### VIEW-03 / UI-06 Raster physical-value probe

状态：**RESOLVED（2026-11-10）**。显示路径继续使用 OpenLayers normalized tiles；探针改用独立 GeoTIFF.js 原始 raster window，按 band 处理 NoData、scale/offset 和单位，并显示 map/pixel coordinate。色带与透明度只更新 Layer，不重建 source/map；生成式 GeoTIFF fixture、UI 边界测试与完整 Web 结果见 `90_PROGRESS.md`。

原审计确认 GeoTIFF source 使用 `normalize: true`，旧 pointer probe 强制把 `layer.getData()` 转为 `Uint8Array`，palette 会重建 source/map，opacity 只改 canvas DOM style，且没有物理值 fixture；这些问题现均已由独立原值读取路径与回归覆盖。

此卡涉及科学数值语义。实施前必须先确认 OpenLayers 10.11 的本地类型/源码语义，并建立独立原始 raster window 读取路径；不得把 canvas RGBA 改名为物理值。

### AG-01 field existence/type preflight

状态：**RESOLVED（2026-11-10）**。Executable-model v5 现已把直接字段参数映射到精确 Data input、合同标识与允许类型族；Preparation 会在 Confirmation 前产生字段存在性/类型 checks 和稳定 blocking issues。跨版本、ID/name、缺 descriptor、类型错误及七模型零误拦结果见 `90_PROGRESS.md` 的 AG-01 日志。

- `preparation_tool._inspect_data_inputs()` 只对 `descriptor.fields` 和 `semantics.fields` 是否非空给出 PASS/UNKNOWN。
- `ExecutableModelDefinition` 只有 `data_input_parameters`，没有 field-parameter 到 data input、允许类型/语义的声明。
- 七个模型已有 `groupField`、`timeField`、`valueField`、`xField`、`yField`、`referenceElevationField`、`depthToWaterField` 等字段参数，但 Preparation 不逐项验证。
- 当前相关测试只覆盖 schema path、extra argument、精确 data version 和 selection policy；没有 `FIELD_NOT_FOUND` / `FIELD_TYPE_MISMATCH`。

涉及源码：

- `source/kernel/src/platform_kernel/agent_runtime/preparation_tool.py`
- `source/kernel/src/platform_kernel/settings_types.py`
- `source/kernel/src/platform_kernel/settings_manifest.py`
- `source/platform/assembler/assembler.py`
- `source/platform/assembler/assembler_domain.py`
- `source/domains/groundwater/6.0.0/models/*.model.json`
- `source/kernel/tests/test_internal_modules.py`
- `source/tests/contracts/test_domain_package_simulation.py`
- `source/tests/assembler/test_domain_packages.py`

此卡会改变装配到 Kernel 的内部模型合同，必须同时验证 assembler/contracts/kernel，不能只在 prompt 或 UI 加规则。

### AG-02 unit/CRS/time semantic preflight

状态：**RESOLVED（2026-11-10）**。Executable-model v6 现已声明字段单位、输入 CRS、CRS 参数、时间字段/范围与 flow-field 垂向 basis 要求；Assembler、Kernel 与 Executor 在各自边界严格验证同一合同。Preparation 使用精确 Data semantics/descriptor 产生可审计的 PASS/UNKNOWN/FAIL：缺失单位、CRS、时区、范围或规范化垂向含义均保持 UNKNOWN，不猜测；已知单位未转换、量纲冲突、CRS 无效/不匹配、非米制投影目标、垂向角色反置、坏时区和范围不覆盖会在 Confirmation 前阻塞。Workspace 会显示依据并把 UNKNOWN 链接到精确 Data version。实现、红灯测试与完整边界结果见 `90_PROGRESS.md` 的 AG-02 日志。

源合同仍允许 `FieldMeaning.meaning` 为自由文本，因此垂向 basis 只有在值精确命中模型声明的规范含义时才能 PASS；其他文本必须 UNKNOWN。这是有意的“不推测”边界，不应通过关键词启发式放宽。

### AG-03 MiniMax normal execution finalization

状态：**RESOLVED（2026-11-10）**。MiniMax 普通 terminal run 现走通用 persisted-execution finalizer，不再携带 synthetic case 声明；case state 路径在成功和失败时均保留声明。输入身份去重、逐 action plan revision、失败优先和 presentation-only 行为均有回归覆盖；实现与验证范围见 `90_PROGRESS.md` 的 AG-03 日志。

- `run_resume.resume_run()` 的 MiniMax 普通 execution terminal 分支调用 `finalize_kernel_case()`。
- `finalize_kernel_case()` 无条件加入“这是开发调试案例”声明，因此普通 run 会收到 synthetic-case 文案。
- finalizer 用第一个 action 的 plan revision 和 input reference 数量代表整个 batch，多 action 统计不准确。
- 它已用 `all(action.status == COMPLETE)` 判定成功，FAILED 不会被 COMPLETE 覆盖；这一点应保留。
- `test_minimax_terminalizes_completed_execution_without_provider_acknowledgement` 只断言包含“已完成”，没有断言普通 run 不含案例免责声明。

涉及源码：

- `source/kernel/src/platform_kernel/agent_runtime/run_resume.py`
- `source/kernel/src/platform_kernel/agent_runtime/run_finalization.py`
- `source/kernel/src/platform_kernel/agent_runtime/persistence/run_recovery.py`
- `source/kernel/tests/test_kernel_lifecycle.py`
- synthetic case 相邻测试

验收命令：

```bash
cd source
.venv/bin/python -m pytest -q kernel/tests/test_kernel_lifecycle.py
../bin/dev check --scope kernel
```

### AG-04 Intent Router Chinese gold

状态：**RESOLVED（2026-11-10）**。当前已有严格冻结的 24 条中文 model-router 金标与可执行 evaluator：普通咨询、比较、执行、执行否定、状态查询、workflow draft、附件读取和缺字段边界各 3 条。报告分别给出 exact pair、`responseKind` 与 `semanticIntent` 的总体、expected-class confusion 和 category 分层；不包含消息正文，并明确排除 Kernel 拥有的 control/精确短状态规则。缺字段只有在请求种类本身不明确时才期望 `NEEDS_CLARIFICATION`；明确执行和明确 workflow 请求仍保持各自模式。

当前 shell 没有任何必需的 `PLATFORM_INTENT_ROUTER_*` 配置，因此真实 Provider 运行按合同为 `BLOCKED / MISSING_ROUTER_CONFIGURATION`，没有准确率证据，不能用 ScriptedModel 或 fixture 自洽冒充真实模型表现。实现、红灯测试、fixture checksum 与 owner-slice 结果见 `90_PROGRESS.md` 的 AG-04 日志。

### WF-01 Workflow Draft sequence/fan-out

状态：**RESOLVED（2026-11-10）**。顺序意图现只由明确的前后关系触发，并按 nodeOutput DAG 的依赖路径校验；合法 fan-out、fan-in、多 sink 和 multi-output 不再误拒，普通 `after 2020` 不触发。断链、反向依赖和中间输出仍被拒绝，compiler 的 cycle/unreachable/port checks 保持不变；实现与验证范围见 `90_PROGRESS.md` 的 WF-01 日志。

### WF-02 Workflow Draft DAG visualization

状态：**RESOLVED（2026-11-10）**。草稿卡现有只读 SVG DAG：独立的 workflow input 节点、按 nodeOutput port 绘制的 edge、图边缘 missing badge、按层分列的 fan-out/multi-sink 布局，以及可用键盘展开的端口/参数来源/limitations 详情。未引入大型图编辑依赖，也未添加执行或绕过确认的动作；红绿测试和门禁范围见 `90_PROGRESS.md` 的 WF-02 日志。

### DATA-01 Data field profile

状态：**RESOLVED（2026-11-10）**。Data descriptor 现持久化在既有 ingestion 全表扫描中生成的 `field-profile.v1`：字段 id/name/type、全量空值与非空数、范围、最多 5 个有界样例、时间解析/物理时区和保守的数字字符串转换证据。目录 UI 合并 descriptor 与已声明单位/业务时区，并明确“全量统计”与“不代表分布的有界样例”；GeoPackage preview 显示 geometry type、解析 CRS 和 WGS84 bbox，截断时明确空间范围/类型仅来自预览样本。精确 version inspection 原样返回同一 descriptor，Preparation 无重复画像实现；UI trust boundary 拒绝不完整或计数矛盾的新画像。红绿测试、Data 声明切片与镜像检查的环境性 NOT_RUN 见 `90_PROGRESS.md` 的 DATA-01 日志。

- `ReplySubmission._sequential_draft_issue()` 仍用 `先...再/然后` 或英文 `first|then|after` 正则。
- 普通时间短语 `after 2020` 会触发顺序规则。
- 规则要求 `edge_count >= node_count - 1`、恰好一个 sink，且所有 outputs 来自该 sink；合法 fan-out/multi-output 会被拒绝。
- 当前只有“明确线性请求但 downstream 仍绑定 workflow input”的修复测试；没有 fan-out、fan-in 或时间短语负例。

涉及源码：

- `source/kernel/src/platform_kernel/agent_runtime/tooling/reply_submission.py`
- `source/kernel/tests/test_workflow_drafts.py`
- `source/packages/workflow-compiler/src/platform_workflow_compiler/graph.py`
- `source/packages/workflow-compiler/tests/test_compiler.py`

验收命令：

```bash
cd source
.venv/bin/python -m pytest -q kernel/tests/test_workflow_drafts.py
.venv/bin/python -m pytest -q packages/workflow-compiler/tests/test_compiler.py
../bin/dev check --scope kernel
```

### KNOW-01 Knowledge evidence source view

状态：**RESOLVED（2026-11-10）**。主 Web 现以被点击的精确 `versionId/unitId/checksum` 读取并识别主证据，按“引用片段原文 / 必需上下文”展示文档标题、原文、locator、来源版本与时间及精确 Data binding；裸 unit/checksum 默认收进折叠的技术身份。只有 `OK` 且内容完整的证据会显示原文和来源链接，所有非 OK 状态与空内容均明确提示不可作为当前依据。红绿测试和 Web 门禁范围见 `90_PROGRESS.md` 的 KNOW-01 日志。

### GW-01 monitoring-trend expansion

状态：**RESOLVED（2026-11-10）**。现有 6 节点、12 个 output names 和旧调用参数保持有效；工作流新增 Pettitt 变化点、seasonal Mann–Kendall、连续缺测段和显式阈值事件，并发布 9 个具名结果。季节趋势、阈值事件默认关闭，启用后必须分别给出季节周期，或用户阈值与比较方向；平台不推断科学前置条件。Gap-run 与总体完整率分开解释，四个节点各有独立 synthetic case，摘要包含 interpretation/limitations 和可评价状态。Operator lock、产品 UI、domain/product checksum、装配冻结字节与 15,512-token 工具预算均已同步；精确红绿测试、owner scopes 和 Native `NOT_RUN` 原因见 `90_PROGRESS.md` 的 GW-01 日志。

### GW-02 flow-field interpretation

状态：**RESOLVED（2026-11-10）**。八个既有 output names 与 workflow `1.1.0` 保持不变，model 升级为 `3.1.0`。水头现在明确为同一参考面、同一垂向基准下的参考高程减埋深并统一换算为米；梯度明确为 targetCrs 米制投影网格上由 IDW 水头面推导的无量纲二维水平量，方向采用从 grid north 顺时针指向水头降低方向。Darcy specific discharge 与按有效孔隙度换算的 seepage velocity 均单独给出公式、单位、参数和 limitations；均质/各向同性标量 K、均匀孔隙度、二维稳态等假设显式公开。publication、operator summary schema 与产品 UI 均明确地图是模型推导结果而非实测地下水流速。三个 synthetic cases、模型/UI 合同、Operator catalog、Assembler/Contracts owners、锁与 checksum 结果及 Native `NOT_RUN` 原因见 `90_PROGRESS.md` 的 GW-02 日志。

### GW-03 monitoring-coverage interpretation

状态：**RESOLVED（2026-11-10）**。既有 `coverage` GeoJSON 输出和受信任的 dissolve → convex-hull 算法链保持不变，新增 `coverage-summary`；model/workflow 升级为 `3.1.0` / `1.2.0`。点覆盖模式在运行原算法前要求至少三个唯一且不共线的点，拒绝非点几何，重复坐标在摘要中单列。摘要公开输入与唯一/重复点数、源 CRS、地理 CRS 椭球测地或投影 CRS 平面米制测量方法、hull 面积和周长。模型、publication、Agent 指引、model card 与产品 UI 均明确结果仅是最外侧监测点的二维几何包络，不是含水层边界、地下水影响范围、捕获区或水力连通性证明。投影/地理 synthetic cases、稳定失败、wrapper 算法链、模型/UI 合同、Operator/Assembler/Contracts owners、锁与 checksum 结果及 Native `NOT_RUN` 原因见 `90_PROGRESS.md` 的 GW-03 日志。

### GW-04 render-map scientific expression

状态：**RESOLVED（2026-11-10）**。既有单文件静态 `map-image` PNG 保持不变，model/workflow 升级为 `3.1.0` / `1.1.0` 并新增 `map-summary`。标题、数值字段、受控 palette、像素尺寸和显式单位现在精确绑定；矢量与首个 raster band 的摘要公开源格式、CRS、数据范围、有效/NoData 计数、值域、色标、解释与限制。混合缺失值有固定 NoData 表达，声明格式与文件签名错配、非法参数及无数值字段均有稳定错误。模型、publication、Agent 指引、model card 和产品 UI 明确保留源 CRS，不重投影，也不宣称支持多图层、在线底图、交互地图或 PDF。红绿 synthetic cases、产品/Agent 合同、工具预算、Operator/Assembler/Contracts owners、锁与 checksum 结果及 Native `NOT_RUN` 原因见 `90_PROGRESS.md` 的 GW-04 日志。

### GW-05 summary semantics

状态：**RESOLVED（2026-11-10）**。既有 `summary` output name 保持不变，model/workflow 升级为 `3.1.0` / `1.1.0` 并新增 `summary-details`。表格按显式数值字段对完整 staged artifact 统计，可选已核实字段分组且最多 100 组；NPY 支持按列或展平。有效值、声明空值和 NaN/Inf 分开计数，空列不会污染其他列，整份输入无有限值则稳定失败；population standard deviation 与 linear-interpolation quartiles 均显式声明。范围记录为 `FULL_STAGED_INPUT`，同时明确它不是 preview sample，且 staged artifact 自身仍可能是更广总体的样本或子集。单位只作标签，不转换、不推断。模型、publication、Agent 指引、model card 与产品 UI 均同步这些数值、分组、字段、范围和单位边界。红绿 synthetic cases、产品/派生域合同、工具预算、Operator/Assembler/Contracts owners、锁与 checksum 结果及 Native `NOT_RUN` 原因见 `90_PROGRESS.md` 的 GW-05 日志。

### GW-06 transfer-learning analysis audit semantics

状态：**RESOLVED（2026-11-10）**。workflow `1.0.0` 的 58 节点、25 个既有 output names 与执行链保持不变，model 升级为 `3.1.0`。chronological target split 现在公开训练/留出边界、training-only scaler fit scope 和 observed-history single-step evaluation mode；明确留出期可使用更早的真实观测，不是递归未来预测、leave-well-out 或外部独立验证。总体和逐井 metrics 发布同一行上的 persistence baseline、MAE/RMSE/R²、R²/null 原因、改善率/null 原因及单位。影像覆盖诊断公开首个选定波段 mask、阈值与通过状态；月份诊断公开请求/实际时间、选择规则、年龄和 NO_MATCH/MAX_AGE_EXCEEDED 状态。25 项 publication、Agent 指引、model card 与中文 UI 同步这些审计边界；红绿 synthetic cases、case model ref、产品装配身份、Operator/Assembler/Contracts owners、锁与 checksum 结果及 Native `NOT_RUN` 原因见 `90_PROGRESS.md` 的 GW-06 日志。

### GW-07 source-sustainability external provenance

状态：**RESOLVED（2026-11-10）**。model 升级为 `1.1.0`，workflow `1.0.0` 的单个 `remote_http` 节点和三个既有 output names 保持不变。模型、publication、Agent 指引、model card 与中文 UI 现在统一说明：平台只把精确 JSON 委托给部署绑定的外部服务，不拥有或复现其评分算法；安装状态、确认卡或 allowlist 均不能证明服务当前可用或方法已被平台独立验证。Executor 要求结果版本精确匹配 `/version` 握手，并在下载任何结果文件前验证同一 remote task 的成功 receipt；平台 receipt 绑定 operation、输入、structured values、artifact 与远端 receipt 的稳定身份，不记录 secret 或本机路径。错误面包含绑定、版本、认证、协议、完整性、失败、超时和不确定提交/终止状态；结果明确不能替代科学验证、专业复核或行政审批。红绿 provenance tests、产品与锁身份、owner scopes、Executor 旧 fixture drift 和 Native `NOT_RUN` 原因见 `90_PROGRESS.md` 的 GW-07 日志。

### NEW-01 Ordinary / Universal Kriging

状态：**RESOLVED（2026-11-10）**。首次复核确认源码、lock 与环境均没有 GSTools/PyKrige，也没有可调用的 Kriging operator/model/workflow；本卡没有把计划名称当作能力或静默增加依赖。现在新增受限且可审计的 NumPy covariance solver：支持 Ordinary 与 linear-drift Universal Kriging、三种 variogram、完整人工参数或确定性拟合、局部邻域、预测/方差 GeoTIFF、经验变异函数、LOO 诊断和同网格 IDW surface comparison。CRS 必须显式且目标为米制投影；重复/不足点、漂移秩不足、坏参数、邻域不足和数值病态均稳定失败。模型、工作流、产品 UI、Agent 指引和 model card 统一声明不自动证明平稳性/各向同性、variogram 合理性、预测无偏或优于 IDW；IDW 差异无真值时不是误差。红绿测试、依赖决策、锁/装配身份、预算、owner scopes 与 Native `NOT_RUN` 见 `90_PROGRESS.md` 的 NEW-01 日志。

### NEW-02 Rule-based table classification

状态：**RESOLVED（2026-11-10）**。首次复核确认不存在可调用的 `table.classify` operator/model/workflow；现新增严格分离的 data、rules 与 bindings artifacts，规则不会藏在自由参数中。规则 schema 明确指标、单位、口径、类别严重度、开闭/单边/多区间、来源版本/定位与核验状态；绑定 schema 把每个输入字段精确映射到指标、单位和口径。实现对边界使用 Decimal 精确比较，覆盖 `2^53 + 1` 等不能由 binary float 无损表示的整数；缺测、超范围、未覆盖和未核验均显式保留，不被伪装成最低风险。输出包含逐指标命中、保守最差综合分类、规则摘要和来源身份；pH 等双侧指标可由同一规则集合表达。模型、工作流、产品 UI、Agent 指引与 model card 统一声明结果仅是指定规则版本的机械分类，不是合规裁定、风险概率、趋势、因果结论或专业复核替代。红绿测试、精度回归、锁/装配身份、预算、owner scopes 与 Native `NOT_RUN` 见 `90_PROGRESS.md` 的 NEW-02 日志。

### NEW-03 DRASTIC relative vulnerability

状态：**RESOLVED（2026-11-10）**。首次复核确认不存在可调用的 DRASTIC operator/model/workflow。现新增固定 D/R/A/S/T/I/C 七因子和 config artifact 的领域算子；权重、每个因子的 rating rules 与 index classes 独立配置并绑定已核验来源。七层 CRS、宽高与 transform 必须精确一致，任何重投影或重采样都必须由用户在上游显式选择；算子本身严格拒绝错网格。任一输入 NoData/非有限值、未覆盖 rating 与未覆盖分类均按明确阶段传播或计数，不会被静默填补。输出 index/classification GeoTIFF 和 summary，公开权重、规则版本/来源、网格、逐因子与总体未覆盖、NoData、类别计数和指数统计。模型、工作流、产品 UI、Agent 指引和 model card 统一声明 DRASTIC 是给定规则/权重下的相对内在地下水脆弱性指数，不是污染概率，也不模拟污染源、浓度、迁移时间、暴露或后果。手算 86、独立 NumPy oracle、NoData/rule gap、错网格和来源/config 负例，锁/装配身份、预算、owner scopes 与 Native `NOT_RUN` 见 `90_PROGRESS.md` 的 NEW-03 日志。

### ADV-01 Pastas dependency and scientific boundary

状态：**RESOLVED（SPIKE ONLY；2026-11-10）**。用户授权 registry 访问后，现已在 `products/groundwater/spikes/pastas/` 建立独立 `pastas==2.0.1` CPython 3.12 lock、hash-pinned export、dependency/license/API/ABI 审查和确定性 synthetic benchmark；它没有进入 root/native/qgis locks，也没有发布 Operator、model、workflow 或 UI 能力。运行边界强制单井日尺度 hydraulic head 与同单位降水/蒸发 stress，水位缺测丢弃，stress 缺测/缺日/负值拒绝；算法固定为 Linear recharge、Exponential response、0 天纯延迟、365 天 warmup、显式校准与 chronological holdout。结果保存 series、参数、分期 metrics、solver/Jacobian/covariance 诊断、完整 checksums、manifest 与 V12 assessment；本地收敛和满秩 Jacobian 不被表述为参数可辨识性。固定 synthetic case 的校准/验证 RMSE 分别为 `0.0061225288/0.0059586834 m`、R² 为 `0.9817664012/0.9878263758`，但现场验证、domain review 与同切分 GRU 比较均仍为 `NOT_ASSESSED`。依赖证据、红绿测试、版本/hash、完整数值和非发布边界见 `90_PROGRESS.md` 的 ADV-01 完成日志。

### ADV-02 MODFLOW 6 + FloPy dependency boundary

状态：**RESOLVED（SPIKE ONLY；2026-11-10）**。此前本地无 `mf6`/FloPy/`modflowapi` 且授权不足的 OPEN 结论仍是当时的有效证据；用户随后明确恢复目标，授权 PyPI FloPy 3.12.0 依赖审查，并只授权从 USGS 官方 `code.usgs.gov` MODFLOW 6 release 取得 Linux x86_64 MF6 6.8.1。现已在 `products/groundwater/spikes/modflow6/` 建立独立 CPython 3.12 lock、hash-pinned runtime export、完整 dependency/license/API/ABI audit、MF6 archive/binary/library/metadata/license/disclaimer/ELF lock 和离线 Dockerfile；没有任何 GitHub 源码或非官方 binary 下载，也没有修改 root/native/qgis locks。独立 runner 在每次执行前复核镜像 architecture、labels 与 `mf6 -v`，随后以 no-network、read-only root、drop-capabilities、1 CPU/512 MiB 限制运行固定 homogeneous confined steady DIS benchmark。解析水头最大误差为 0 m，Darcy 总流量理论 30 与模拟入/出流 `29.999999999999893 m³/day` 一致，预算差 0%，normal termination；head GeoTIFF 又由现有 `raster.contour@1.0.0` 的底层 GDAL 路径实际生成 10 条等值线。该结果仅为 `UNCALIBRATED_SYNTHETIC` scientific PASS；工程归档、Agent 体验、真实场地、transient/WEL/RCH/OBS、PRT、PEST++/IES 和默认产品集成仍未评估。完整供应链 identity、依赖版本、镜像/环境大小、红绿测试、数值、限制与 NOT_RUN 见 `90_PROGRESS.md` 的 ADV-02 完成日志。

### M0-01 development command truth

状态：**RESOLVED（2026-11-10）**。首次实测发现稳定 `bin/bootstrap` 转发到不存在的 `gw bootstrap`，执行手册声明的 `gw deps verify/materialize` 同样没有 CLI 路由；更关键的是 `gw dev` 只设置了锁定环境变量，却继续用宿主 Python 运行。现已复用既有 toolchain/dependency 实现恢复统一入口，新增无副作用的 `bootstrap --status`，并令所有 `dev` 命令先验证锁摘要、Python binary checksum、marker 和重定位状态后再以锁定解释器重执行，缺失时 fail closed。当前工具链和三个依赖 scope 均未物化；按用户边界没有执行任何默认 runtime 安装。实际命令、前置条件、XDG 输出目录、诊断结果和完整 `NOT_RUN` 清单见 `source/docs/development/vibecoding-quick-reference.md`，红绿回归与验证范围见 `90_PROGRESS.md` 的 M0-01 日志。

### M0-02 single-operator quick run

状态：**RESOLVED（2026-11-10）**。现有 `moperators.smoke` 已包含 catalog fixture staging，生产执行边界是 `moperators.runtime.task`；本卡在两者上增加单算子包装，没有复制 runtime。CLI 支持精确 id/version、fixture 与 JSON 参数覆盖、XDG 内输出目录，并只在输入 checksum、参数 schema、子进程结果和输出合同全部通过后原子发布 replay、summary 与逐项 SHA-256。非法参数、未知 fixture、越界输出和运行前置条件均有稳定拒绝码。

为遵守“不修改默认 Native/QGIS/root runtime”，真实 Native smoke 不复用调用入口解释器：命令验证锁定 uv 与 CPython binary，并只读逐文件复核与当前 Python locks 兼容的既有 wheelhouse，在 XDG build 创建离线、hash-pinned、一次性 Native 环境，退出即删除。`timeseries.gap_runs@1.0.0` 的锁定 CPython 3.12.8 实跑 PASS，非法 `frequency="week"` 返回 `OPERATOR_PARAMETERS_INVALID` 且没有半成品目录。公共 `../bin/dev` 在当前缺失 root 工具链时仍按 M0-01 fail closed；内部验收没有被描述为公共入口或完整 Native gate 通过。命令、产物身份、测试和 `NOT_RUN` 边界见 `source/docs/development/vibecoding-quick-reference.md` 与 `90_PROGRESS.md` 的 M0-02 日志。

### M0-03 workflow golden

状态：**RESOLVED（2026-11-10）**。源码复核确认不存在可直接复用的 workflow golden runner，但已有 Workflow Compiler、Operator Catalog 和生产 `moperators.runtime.task`。现新增的薄 runner 只负责稳定拓扑编排、workflow binding、证据 snapshot 与 JSON Pointer 级 diff；所有节点仍通过既有生产 task 执行，没有复制 compiler、executor 或算子实现。

签入的 `monitoring-trend`、`render-map`、`flow-field` 三个 case 固定 fixture、全量参数、workflow checksum、catalog digest、每个 output identity 与选定关键值，并各自维护独立 baseline。三者已在锁定 CPython 3.12.8 Native 环境与同一个只读本地系统工具 image 中重复 PASS，差异均为空；flow-field 的 GeoTIFF/Parquet identity 在第二次成功执行中保持不变，monitoring/render 的宿主与容器 snapshots 逐字节一致。无 GDAL CLI 的宿主 flow 明确拒绝，不被计作通过。

可选系统 image 只允许来自项目专属 rootless daemon 的现有 Linux/amd64 image，并复核精确 `gdal-bin`/GDAL 版本和 image ID；运行时 no-pull、no-network、read-only、drop capabilities、不挂 Docker socket，也不使用 image 内 Python/源码。默认 root/Native/QGIS runtime 与 locks 均未修改。公共 `../bin/dev` 因 root 工具链尚未物化仍按 M0-01 fail closed；内部真实运行、summary SHA-256、测试、命令和完整 `NOT_RUN` 边界见 `source/docs/development/vibecoding-quick-reference.md` 与 `90_PROGRESS.md` 的 M0-03 日志。

### M0-04 Agent offline replay

状态：**RESOLVED（2026-11-10）**。当前没有可直接调用的 Agent replay 命令，但 Kernel 测试已经提供 `ScriptedModel`/`ModelStep`，生产 `PlatformApplication` 也拥有 Confirmation、Preparation、Workflow Draft、WorkflowRun 与执行终结状态机。现新增的开发入口只负责冻结 case、驱动真实状态机、归一化 snapshot、计数和一致性断言，没有复制 Agent 或执行系统。

六个签入 case 分别验证普通 DIALOGUE 不执行、明确执行提案停在 Confirmation、缺字段要求用户修正、显式 fan-out 草稿、MiniMax 非案例执行终态，以及引用只能来自本轮成功读取的 Knowledge unit。所有服务观察与 Executor 回执都由进程内 `httpx.MockTransport` 提供，Provider 使用测试设施 `ScriptedModel`；因此运行完全离线且确定性，但不被表述为真实 DeepSeek/MiniMax 准确率或网络协议验证。

实测六个 case 各重复三轮，逐项不变量均为 `PASS`，每个 case 的三份 snapshot SHA-256 完全一致；执行提案的 Confirmation/WorkflowRun 为 1/0，MiniMax 完整执行为 1/1，其余均为 0/0。证据只原子发布到 XDG cache，默认 runtime 未修改。公共 `../bin/dev` 因 root 工具链未物化仍按 M0-01 fail closed；内部 summary identity、focused tests、命令与 `NOT_RUN` 边界见 `source/docs/development/vibecoding-quick-reference.md` 与 `90_PROGRESS.md` 的 M0-04 日志。

### M0-05 current capability inventory

状态：**RESOLVED（2026-11-10）**。此前模型、workflow、算子和 UI 能力数量散落在 manifest、测试与旧文档中，且部分文档仍保留“六模型”文字，不能作为当前事实来源。现新增的开发入口从产品实际选择的领域包、`domain-manifest.json`、全部 model/workflow、Operator Catalog index 和 Product UI 声明重建确定性参考表；复用 Assembler 验证路径，并另以 `OperatorCatalog.load` 校验完整 index、runtime、manifest/smoke identity，没有创建平行 catalog。

签入的 `source/docs/development/current-capability-inventory.md` 当前动态列出 10 models、10 workflows、109 catalog operators、43 个被领域 workflow 使用的唯一算子、runtime 分布、11 个 UI examples、viewer 分布，以及每个模型的说明/适用范围、每个 workflow 的节点和精确 output/viewer、完整算子使用关系。计数和行都从已验证源声明派生，不硬编码“六类模型”或固定算子数量；Pastas/MF6 独立 spike 与 `remote_http` 可用性边界明确保留。

`capability-inventory --verify` 会只读重算并要求签入参考表逐字节一致，漂移以稳定错误拒绝。内部命令实测 PASS，focused tests 为 5 passed，相邻快速反馈 CLI 回归为 18 passed，Ruff、文档检查与 `git diff --check` 均 PASS；公共 `../bin/dev` 仍因锁定 root 工具链未物化而 fail closed。来源摘要、参考表 identity、命令、测试和完整 `NOT_RUN` 边界见 `90_PROGRESS.md` 的 M0-05 日志。

### DOC-01 current-product documentation consistency

状态：**RESOLVED（2026-11-10）**。`architecture-structure-and-flow.md`、`domain-packages.md` 和 `webui.md` 中旧 Product Manifest/Executable Model schema、`groundwater@5.0.0`、v11、旧 Gateway 端口和手写“六模型”等描述已按当前源码修正。精确能力集合统一链接 M0-05 自动功能清单，不再在说明页维护平行数量；WebUI 场景说明补入 Product UI 当前已有的 Kriging、规则分类和外部水源地综合评价条件。

文档检查器现直接读取当前 product/domain manifest、全部 model/workflow schema 和 bootstrap layout，要求三份当前产品说明引用正确 API/domain/schema/端口与功能清单，并拒绝手写模型数、v11 和非当前 loopback Gateway 端口。测试先以缺失检查 API 红灯，实现后对旧文档产生 17 个真实错误，修正后 15 个 checker tests、文档全检、Ruff 与 `git diff --check` 全部通过。该结论只证明文档身份一致，不替代 Compose、Playwright 或发布门禁；完整证据见 `90_PROGRESS.md` 的 DOC-01 日志。

### GATE-01 host/static and platform-product owner gates

状态：**RESOLVED（2026-11-10）**。AG-04 后隔离复现的四组历史失败现已独立收口：import-boundary 只以顶层 `SCHEMA` 合同分类无独立 schema package 的服务持久表，不再把 Executor migration backup table 当作当前产品表；Usage Capture 的自然语言不再命中 raw-SQL 正则。Usage Capture 的模型请求起始职责与 Data/Knowledge E2E 的 pytest/UI 编排已拆分到内部模块，公共 `_RunUsageCapture` identity 和 CLI facade 保持不变，两个行数门禁均恢复。

Data slice 测试现在复用 `pytest_command(test_slice, report_skips=True)`，Executor persistence 测试复用实现导出的 `SCHEMA_VERSION`，不再复制易漂移参数。完整 slice 还发现并修正 reply-quality 的当前领域 checksum 与模型集合：release identity 现在覆盖产品实际 10 模型，而 8 类/24-run 回复质量场景本身保持冻结。

同一四文件 focused suite 从 4 failed / 45 passed 变为 50 passed，相关 Usage/E2E 回归 148 passed，reply-quality 16 passed，完整 `platform_product` slice 为 522 passed / 17 个显式环境 skip；全局 Ruff、import boundary、internal layout、文档和 diff 检查均通过。13 个 skip 仍要求 build wheelhouse，另外 4 个属于独立 Pastas/MF6 环境条件；公共 `dev compile` 继续以 `PROJECT_TOOLCHAIN_NOT_READY` fail closed，不能把宿主结果解释为锁定环境或发布门禁通过。完整命令与边界见 `90_PROGRESS.md` 的 GATE-01 日志。

### GATE-02 remaining host service/protocol owner gates

状态：**RESOLVED（2026-11-10）**。其余宿主 owner 集合的历史失败全部集中在 Executor 测试夹具与当前 compiled summary 合同漂移：成功请求遗漏当前必需的 `unit` 默认参数，成功完成只提供旧 `summary` 单值端口，并仍按单一发布结果计数。测试构造器现从模型 `defaultParameters` 合并规范参数，双输出夹具满足当前 `statistics.describe` 的 `summary`/`details` schema，并验证 `summary`/`summary-details` 两个命名发布；故意遗漏默认参数或值端口的负向合同仍保持拒绝。

生产 Executor 的 canonical、schema、值端口和发布校验均未放宽。完整 Executor owner 从 68 failed / 86 passed 收口为 154 passed；Embedding Client、Protocol Contracts、Workflow Compiler、Data、Executor 与 contracts 的宿主集合为 559 passed / 2 个显式 `service_image` deselected。默认工具链/wheelhouse、Native/QGIS、Compose/Playwright 与真实外部服务门禁仍是 NOT_RUN，详见 `90_PROGRESS.md` 的 GATE-02 日志。

### GATE-03 Kernel/Web, product-neutral boundary and production-build browser gates

状态：**RESOLVED（2026-11-10）**。历史三个 Kernel failure 已关闭：DeepSeek 连续空 final 的协议修复按两次真实请求累计 30 tokens；compaction planner 现在把 `MemorySummary` assistant message envelope 纳入预算，并由完整序列化后的检查继续处理转义与 tokenizer 边界。完整 Kernel 最终为 849 passed，精确 Kernel mypy 为 9 个 source files 无问题。

产品中立边界首次全检发现 Kernel/shared development tooling 直接硬编码 Groundwater 身份。通用技术标识规则、显式产品 manifest、产品选择和 catalog implementation-source override 现取代硬编码分支；workflow golden 声明/快照与远程算子生成器已回到 `products/groundwater/` 所有权目录。完整 boundary 最终零 findings，operator lock/index 连续生成字节不变，能力清单只读重算 PASS。

生产构建 Playwright 首次因 Air Quality 夹具只声明旧 `summary` 输出而在装配阶段失败；夹具现从 current model `finalOutputs` 派生 `summary` 与 `summary-details`，并提供产品专属文案。复跑 9 passed。主 Web 仍为 28 files / 200 tests，typecheck 和 production build PASS；Ruff、core imports、documentation、worktree hygiene、internal layout、`uv lock --check` 与 `git diff --check` 均通过。最终只读前置审计确认锁定 toolchain 为 `MISSING`、python/npm/system dependency scopes 均 `MISSING`，公共全检 fail closed；Knowledge 数据库、真实 Agent/Intent Router/Embedding Provider 与 sustainability endpoint 配置也全部缺失。因此 Compose/live-browser、Native/QGIS、真实 Provider/远程服务和发布验收没有被冒充为已通过；精确文件、命令和散列见 `90_PROGRESS.md` 的 GATE-03 日志。

## 4. 已有基线测试结果

首次复核时的窄基线保留如下：

```text
npm test --workspace web -- src/ConfirmationEditor.test.tsx src/session-stream-state.test.ts
PASS: 2 files, 6 tests

.venv/bin/python -m pytest -q \
  kernel/tests/test_workflow_drafts.py::test_deepseek_draft_repairs_disconnected_sequence_and_intermediate_output \
  kernel/tests/test_kernel_lifecycle.py::test_minimax_terminalizes_completed_execution_without_provider_acknowledgement \
  kernel/tests/test_internal_modules.py::test_preparation_tool_returns_path_diagnostics_for_extra_model_argument
PASS: 3 tests
```

当前收口复核的权威结果为：

```text
.venv/bin/python -m pytest -q -rs --tb=short kernel/tests
PASS: 849 tests

npm --prefix web test
PASS: 28 files / 200 tests
npm --prefix web run typecheck
PASS
npm --prefix web run build
PASS
npm --prefix web run test:e2e:build
PASS: 9 Playwright tests

bash scripts/checks/check-boundaries.sh
PASS: naming, retired-version scan, zero findings
```

Kernel pytest 在清理历史 `/tmp/pytest-of-mtd/garbage-*` 时仍报告目录非空/权限 warning；成功命令退出码为 0。该 warning 保留为环境诊断，不改变断言结果，也不被解释为完整发布健康证明。

未运行（NOT_RUN）：

- 锁定工具链下的 `../bin/dev check --scope all` 与 13 个 wheelhouse checks
- 完整 Native/QGIS operator tests
- 隔离 Compose 及其中的 live-browser；生产构建 Playwright 已单独通过
- 真实 DeepSeek/MiniMax、Embedding Provider 与 remote_http 服务
- 完整发布验收

## 5. 计划与源码不一致

1. `90_PROGRESS.md` 将 “sequential draft/fan-out” 标为 `AG-04`，但 `07_AGENT_PREFLIGHT.md` 的 `AG-04` 是 Intent Router 中文金标；顺序/fan-out 在 `08_WORKFLOW_DRAFTS.md` 是 `WF-01`。
2. `90_PROGRESS.md` 将 `WF-01` 写成 DAG visualization，而 `08_WORKFLOW_DRAFTS.md` 中 DAG visualization 是 `WF-02`。
3. `03_GAP_MATRIX.md` 用 `UI-03/UI-04` 表示 result/draft cache，专项文档用 `PROG-01/PROG-02`。本复核保留两组别名，实施日志优先使用专项文档 ID。
4. `domain-manifest.json` 与当前功能清单现均反映实际 models/workflows；三份当前产品说明中的旧“六个/六模型”、领域 pin/schema、v11 与旧端口已由 DOC-01 独立修正，并加入基于当前 manifest 的自动检查。
5. 当前公共合同是 API v12；`docs/capabilities/webui.md` 的演示与开发段落现已全部切换到 v12 与当前 Gateway 端口，该差异已解决。
6. 当前源码为 109 个 operator、10 models、10 workflows；Workflow Compiler cardinality=`one` 的限制仍存在。

## 6. 新发现但不扩张首卡的问题

- 复核记录曾误写 `use-session-stream.ts` 对每个 SSE event 连续调用两次相同 `log(...)`；重新对照 baseline 与当前 diff 后确认实际只有一次调用，因此无需产品代码改动。
- 源码树已有忽略的 `.generated`、`build` 和测试缓存目录。本轮不清理、不覆盖；后续测试继续使用项目 XDG/临时目录约定。
- AG-04 后记录的四组 platform/product owner failures 已由 GATE-01 独立修复并以完整 slice 复跑；历史 476 passed 仍只代表当时范围，当前权威结果为 522 passed / 17 个显式环境 skip。
- 其余宿主服务/协议 owner failures 已由 GATE-02 收口；Executor 当前为 154 passed，跨 Embedding/Protocols/Workflow Compiler/Data/Executor/Contracts 的权威宿主结果为 559 passed / 2 个显式 `service_image` deselected。
- 三个历史 Kernel failures、产品中立边界硬编码与 Air Quality 生产构建夹具漂移已由 GATE-03 收口；完整 Kernel 849 passed，生产构建 Playwright 9 passed。

## 7. 推荐执行顺序

下列编号保留首次复核时的实施顺序；这些实施卡当前均已完成。后续只剩三组条件性收口：先在用户允许物化默认环境后验证锁定工具链与 wheelhouse，再运行完整 Native/QGIS，最后运行隔离 Compose/live-browser、真实 Provider/远程服务及发布验收。缺少该授权或外部配置时保持精确 `NOT_RUN`，不以宿主或离线结果替代。

首批实施任务：

1. **CHAT-01**：资源加载 loading/error/retry/abort 与失败场景测试。
2. **CHAT-02**：JSON raw draft、解析错误、保存 gate 与 schema 值类型测试。
3. **PROG-01**：以服务端完整结果集为权威，按 action/attempt 原子替换。
4. **PROG-02**：基于 dirty event/reconnect 的 draft cache 失效与迟到响应保护。

随后按风险推进：

5. VIEW-01 GeoViewer 生命周期。
6. AG-01 字段存在性/类型合同与 Preparation。
7. WF-01 显式依赖关系与 fan-out。
8. AG-03 通用执行终结与 synthetic case 分离。
9. VIEW-03 原始栅格物理值探针（已完成）。
10. AG-02 单位、CRS 与时间语义 preflight；缺失语义保持 UNKNOWN，不推测（已完成）。
11. AG-04 Intent Router 中文金标与分层 evaluator（已完成；真实 Provider 准确率待配置环境运行）。
12. WF-02 Workflow Draft 只读 SVG DAG、外部输入、missing badge 与 multi-sink 呈现（已完成）。
13. DATA-01 字段画像：复用 descriptor 事实，区分全量/抽样统计，完善时间、空间、单位和数字字符串提示（已完成）。
14. KNOW-01 evidence source view：展示来源、版本/时间、定位、摘要和可打开原文片段，并保留 citation 证据链（已完成）。
15. GW-01 monitoring-trend expansion：既有输出合同与科学前置条件已冻结，突变、季节趋势、连续缺测与显式阈值事件能力已完成。
16. GW-02 flow-field interpretation：已明确水位/埋深、垂向基准、K/CRS 单位、方向和 Darcy 限制，推导速度不再表述为观测（已完成）。
17. GW-03 monitoring-coverage interpretation：处理少于三个不共线点与重复点，公开 CRS/投影、hull 面积/周长，并明确凸包不是含水层边界或影响范围（已完成）。
18. GW-04 render-map improvement：补齐标题、值字段、palette、尺寸、CRS/范围、色标/单位、nodata 与输入错误表达，保持单文件静态 PNG 的能力边界（已完成）。
19. GW-05 summary semantics：已核对真实 summary operator chain 的数值摘要、空值、分组、字段语义、统计范围与单位；完整 staged artifact 不再被表述为更广总体（已完成）。
20. GW-06 transfer-learning-analysis：已核对单步留出回测、时间切分、仅训练集拟合 scaler、R²/null 原因及 metrics、target split、imagery coverage、map-month diagnostics 等具名结果（已完成）。
21. GW-07 source-sustainability：明确外部服务依赖、结果 provenance、错误与限制展示，不把远程黑盒描述成平台内置已复现算法（已完成）。

每张卡只在其新增失败测试先失败、实现后相关检查通过、`90_PROGRESS.md` 记录真实命令后标记 DONE。

## 8. 2026-11-11 最终源码复核与全项目验收

此前第 4 节和 GATE-03 中的默认工具链、Native/QGIS、Compose/live-browser `NOT_RUN` 记录是当时边界下的历史事实；用户随后明确允许默认环境物化。当前权威状态已由 Run ID `60a68e4a5295` 的全量报告取代：14 个声明阶段全部 PASS，2508 passed / 4 个独立 spike 条件 skip，失败、阻断与未运行阶段均为 0。

交互式增量开发能力位于 `source/scripts/development/interactive/`，与生产运行时代码无 import 依赖；源码只读、可写状态仅在 XDG。实测覆盖 Uvicorn 热重载、Vite HMR、增量影响规划、批次合并与替代、定向服务重启、dashboard/SSE/metrics/proxy、日志脱敏、停止控制面后保留 Compose，以及无活动控制面时的稳定状态读取。最终审计为 `phase=STOPPED`、`controlPlane=development-only`、`runtimeImports=none`、`writableState=xdg-only`。

全量报告：

- `/home/mtd/.local/state/groundwater-v6/validation/interactive-goal/full-project-report.json`
- `/home/mtd/.local/state/groundwater-v6/validation/interactive-goal/full-project-report.md`

最终隔离 Compose 使用随机项目、动态端口、私网与新卷，报告记录 `existingDeploymentMutation=false`；16 个实时浏览器场景全部通过，覆盖结构化证据精确读取、Data/Knowledge 抽屉、上传与精确版本附加、双成果下载/恢复、拒绝不产生 WorkflowRun、意图路由与问题矩阵。默认 `source-groundwater` 栈在验收后保持健康，隔离 E2E 资源已回收。

结论边界保持严格：4 个 skip 是未激活的独立 MF6 release input、FloPy 或 Pastas spike 环境；工程状态为 PASS，但真实场地科学有效性、真实 Provider 表现和第三方服务可用性仍为 `NOT_ASSESSED`。这些边界不构成当前实现目标的剩余缺陷，也不得被工程 PASS 替代。
