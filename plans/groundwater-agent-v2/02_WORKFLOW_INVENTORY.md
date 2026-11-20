# Groundwater Agent V2 阶段 2 Workflow 清单

核查日期为 2026-11-20（Asia/Shanghai），以本地 `main@bd5d58ab63e16b2f5a59ceabcc7faf1c4c71f411` 及已有的阶段 1 工作区改动为准。活动产品取 `product-manifest.json` 完整 pin 指定的领域包及 Product UI。旧包仍按自己的 ID、版本和摘要存放，可用于历史身份追溯。已持久化历史运行的读取行为需要 Kernel/Executor 的运行记录验证。 [产品清单](../../source/products/groundwater/product-manifest.json) · [活动领域](../../source/domains/groundwater/6.2.0/domain-manifest.json) · [历史领域](../../source/domains/groundwater/6.1.0/domain-manifest.json) · [阶段 1 治理](01_OPERATOR_GOVERNANCE.md)

## 定义、执行与展示边界

- Workflow 源文件声明输入角色、参数名、节点依赖、有版本的 Operator 和输出端口引用。可执行 Model 源文件通过 `workflowRef` 精确指向其 ID、版本和 definition checksum，并声明 Agent 工具、输入绑定、参数合同、执行策略、最终输出与发布信息。Assembler 校验 Model 字段及摘要后，在生成物中内联由 Workflow 编译得到的 `workflow`，源 Model 的 Schema 没有 DAG 字段。本清单的两个摘要都是现有编译与装配口径的规范 JSON 摘要。 [Assembler](../../source/platform/assembler/assembler_domain.py) · [Model 字段合同](../../source/platform/assembler/assembler_common.py) · [Workflow Compiler](../../source/packages/workflow-compiler/src/platform_workflow_compiler/graph.py)
- 全部 Model 通过公开 `GET /api/v12/capabilities` 进入能力目录的「工作流」标签。具有研究工作区使用权限时，可点「在会话中使用」按 Model 和 Workflow 精确身份选择预设。模型表中的 Agent 工具是会话调用入口。Product UI 的推荐问题用于填入自然语言，案例档位用于展示领域 Execution Case。 [公开能力路由](../../source/kernel/src/platform_kernel/api/routes/system.py) · [能力抽屉](../../source/web/src/ProductDrawers.tsx) · [选中身份](../../source/kernel/src/platform_kernel/workflow_selection.py)
- 预设走静态 Model/Workflow 合同。组合列仅表示精确 Operator 版本在产品的动态组合名单内。动态草稿还需满足当前轮证据、明确角色与参数、工件语义及资源限制。当前产品限制动态组合为最多 12 节点、并行度 1。18 个预设图均含白名单以外的节点，整体不能以当前产品的动态组合合同确认执行。Phase 1 的技术或科学证据是独立治理记录。 [装配名单和预算](../../source/platform/assembler/assembler.py) · [草稿准入](../../source/kernel/src/platform_kernel/agent_runtime/workflow_draft.py) · [阶段 1 治理](01_OPERATOR_GOVERNANCE.md)
- 参数来源按 Model 合同展开。预设调用先合并 `defaultParameters` 与显式提交的参数，再沿 `parameterBindings` 进入 Workflow，最后按节点绑定送 Worker。节点列的单个名字代表 Workflow 与节点参数同名。Manifest Schema 的 `default` 还是一项算子声明，Executor 静态提交流程不会统一注入未绑定参数。已查到 `gradient.band` 和 `dissolve.separateDisjoint` 的未绑定项由各自实现分别回退到 `1` 和 `false`。配置工件作为输入时，以精确 Data 版本绑定。 [Kernel 参数合并](../../source/kernel/src/platform_kernel/agent_runtime/model_tool.py) · [Executor 入参](../../source/services/executor/src/platform_executor_service/control_components/admission.py) · [流场梯度](../../source/packages/operator-pack/src/moperators/operators/hydraulic_gradient.py) · [QGIS 融合](../../source/packages/operator-pack/src/moperators/operators/qgis.py)
- 按案例解析时，`yaozhanbao-transfer-case` 先加载 Model 默认值，再绑定同批数据角色并叠加案例档位。默认 `standard` 无覆盖，`development_fast` 仅把 `pretrain.epochs`、`finetune.epochs` 都设为 `5`，把 `pretrain.hiddenSize` 设为 `32`。这个 Execution Case 的档位与阶段 2 版本化 Workflow Profile 是独立合同。 [领域案例](../../source/domains/groundwater/6.2.0/cases/yaozhanbao-transfer-case.json) · [案例解析](../../source/kernel/src/platform_kernel/agent_runtime/case_resolver.py)
- 输出表的底层合同来自编译后的节点端口。多组媒体类型和格式表示定义所容纳的组合，实际输出由精确输入、工件规则与参数决定。`value` 表示节点值类型，Model 可以声明随数据文件发布的 JSON 投影。发布声明、UI 标签和查看器分别来自 Model 与本产品选中的 UI 配置。清单提供静态身份和发布合同，运行时凭据以实际发布后的精确 Data Reference 为准。 [Workflow Compiler 格式约束](../../source/packages/workflow-compiler/src/platform_workflow_compiler/formats.py) · [产品 UI](../../source/products/groundwater/ui/product-ui-porous.v1.json)

## 用例与验证范围

生成表关联 Manifest 登记的 Execution Case、产品开发 Golden baseline 和 real-data case 声明。三条 Pack Golden 的现有快照与活动 Workflow 的 ID、版本和摘要吻合。本清单读取静态声明与基线快照，Golden 的工件执行结果另需运行时记录。real-data 项仅核对本地声明，数据准备及现场科学适用性未在清单核查中验证。 [Golden runner](../../source/scripts/development/workflow_golden.py) · [real-data 案例](../../source/products/groundwater/development/real-data-cases.v2.json)

下列是工作区内其余可定位的场景入口及覆盖对象。它们是代码或夹具定义中的测试关系。服务型、浏览器型与外部数据型用例未在本清单验证中执行。

| 本地场景入口 | Workflow 绑定或夹具 |
| --- | --- |
| [六 Model Gateway 场景](../../source/tests/e2e/groundwater/model_lifecycle.py) | `summary`、`monitoring-coverage`、`monitoring-trend`、`flow-field`、`transfer-learning-analysis`、`render-map`，发布测试表、点图层和合成迁移夹具。监测覆盖另有从发布的范围派生制图的场景。 |
| [Native real-data Gateway 场景](../../source/tests/e2e/groundwater/native_real.py) | 上述四条 real-data 声明绑定到 `monitoring-trend`、`flow-field`、`kriging`、`drastic`。同一套件的 `classification-control` 使用来自 Operator Pack 的规则和表夹具执行 `rule-classification`。 |
| [腰站堡路由和浏览器场景](../../source/web/e2e/v12-yaozhanbao-workflow.spec.ts) | `transfer-learning-analysis` 的 `yaozhanbao-transfer-case`。固定 Provider Wire 测试也具有该案例的观察数据与路由输入。 [Provider Wire](../../source/tests/contracts/test_fixed_provider_wire.py) |
| [Porous 服务部署场景](../../source/tests/e2e/groundwater/porous_live.py) | `porous-simulation` 使用合成项目包。附加场景覆盖 `porous-calibration`（项目包和 `observations.json`）、`porous-scenario-comparison`（`scenario.zip`）、`porous-map`（`original.tiff`）、`porous-inversion-training`（`training.zip`）、`porous-inversion-sampling`（上一步发布的权重与 `sampling.json`）和 `porous-agricultural-inversion`（`agricultural.zip`）。 |
| [可持续性 Gateway 场景](../../source/tests/e2e/groundwater/sustainability_live.py) | `sustainability-input-preparation` 绑定 simulation、monitoring、management 证据，`source-sustainability` 绑定经准备输出的 assessment input。Porous 套件有相关输入链。服务端数值计算与适用性在本轮清单验证中为未知。 |

清单用现有 `capability_inventory.py` 的装配入口读取 Product、Domain、Workflow、Executable Model 和 UI，复用 Workflow Compiler、Catalog implementation lock、模型与输入绑定校验。下面的章节由 [清单渲染器](workflow_inventory.py) 生成。从仓库根目录使用已安装的项目 Python 3.12 检查生成块。

```bash
unshare -Urn -- python -B plans/groundwater-agent-v2/workflow_inventory.py --check
```

本次用项目锁定的 Python 3.12.8 在禁网命名空间运行了 28 项检查，全部通过。范围含现有清单生成与 Golden 比较助手、Kriging、DRASTIC、规则分类、可持续性合同、产品装配快照及两项 Groundwater 装配入口用例。

## 活动 Workflow 明细

<!-- BEGIN GENERATED WORKFLOWS -->
产品选择 `groundwater@6.2.0`，包摘要 `sha256:9862f88dd7cce5bcdcf58dd008edb6465bcfde031d97548785a66eb3e14c5fc3`。共 18 个 Workflow、18 个 Executable Model、95 个节点及 117 个命名输出。

保留的历史领域包 `groundwater@6.1.0` 使用独立包摘要 `sha256:8662efa7382bbe4f9fe7c35fd873bf7109a4d75ee3213fd4bb0fdc92b4c4b10d`。两个包 JSON 定义内容相同的同名文件数为 `workflowDefinitions` 18/18、`executableModels` 18/18、`executionCases` 1/1。

动态组合白名单有 62 个精确算子版本。按 Workflow ID 排序，节点与输出保留来源顺序。

### groundwater.drastic@1.0.0

- Workflow 定义摘要 `sha256:be5c6ddceb58abbb0b58cd669e7172ba31f267c5c44db3ad7413bc1b01e92328`。 [Workflow JSON](../../source/domains/groundwater/6.2.0/workflows/drastic.workflow.json)
- 组合算子成员 —。本图包含未准入的精确版本，动态组合草稿的准入检查返回 `COMPOSITION_OPERATOR_UNSUPPORTED`。
- Model `groundwater.drastic@1.0.0`，摘要 `sha256:dc5576b8e1efc187cbc8d88e7a32bc63e62868d8471142409667cc22a0fc9b1a`。产品入口为预设「DRASTIC 相对脆弱性评价」，Agent 工具为 `run_drastic`。 [Model JSON](../../source/domains/groundwater/6.2.0/models/drastic.model.json)
- 声明绑定的验证用例 [外部来源样本声明](../../source/products/groundwater/development/real-data-cases.v2.json) `odnr-franklin-drastic`，来源组 `odnr-franklin-drastic`，输入角色 `A, C, D, I, R, S, T, config`

| 输入角色 | 基数 | 编译后的格式 / 媒体类型 | Model 调用参数 |
| --- | --- | --- | --- |
| `D` | `one` | `cog (image/tiff)`<br>`geotiff (image/tiff)` | `depthVersionId` |
| `R` | `one` | `cog (image/tiff)`<br>`geotiff (image/tiff)` | `rechargeVersionId` |
| `A` | `one` | `cog (image/tiff)`<br>`geotiff (image/tiff)` | `aquiferMediaVersionId` |
| `S` | `one` | `cog (image/tiff)`<br>`geotiff (image/tiff)` | `soilMediaVersionId` |
| `T` | `one` | `cog (image/tiff)`<br>`geotiff (image/tiff)` | `topographyVersionId` |
| `I` | `one` | `cog (image/tiff)`<br>`geotiff (image/tiff)` | `vadoseImpactVersionId` |
| `C` | `one` | `cog (image/tiff)`<br>`geotiff (image/tiff)` | `hydraulicConductivityVersionId` |
| `config` | `one` | `json (application/json)` | `configVersionId` |

| 节点 | 精确 Operator · Runtime | 组合成员 | 端口输入绑定 | 节点参数 ← Workflow 参数 | Manifest Schema 默认声明 |
| --- | --- | --- | --- | --- | --- |
| `evaluate-drastic` | `groundwater.drastic.evaluate@1.0.0` · `native` | 否 | `D ← input:D`<br>`R ← input:R`<br>`A ← input:A`<br>`S ← input:S`<br>`T ← input:T`<br>`I ← input:I`<br>`C ← input:C`<br>`config ← input:config` | — | — |

Workflow 参数集合为空。输入文件仍可承载规则、服务或算法配置。

| 命名输出 | 节点端口 | 编译后的底层合同 | Model 发布声明 | UI 标签 · 查看器 |
| --- | --- | --- | --- | --- |
| `drastic-index` | `evaluate-drastic.index` | `artifact`<br>`geotiff (image/tiff)` | `geotiff (image/tiff)` · `FILE_ONLY` | DRASTIC 相对脆弱性指数 · `map` |
| `drastic-classification` | `evaluate-drastic.classification` | `artifact`<br>`geotiff (image/tiff)` | `geotiff (image/tiff)` · `FILE_ONLY` | DRASTIC 相对脆弱性等级 · `map` |
| `drastic-summary` | `evaluate-drastic.summary` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | DRASTIC 运行审计摘要 · `json` |

### groundwater.flow-field@1.1.0

- Workflow 定义摘要 `sha256:bda102fc7492dccae5e2fe60a9e56cc1d5a1028ed382fdf28ede10a673e8ff6b`。 [Workflow JSON](../../source/domains/groundwater/6.2.0/workflows/flow-field.workflow.json)
- 组合算子成员 —。本图包含未准入的精确版本，动态组合草稿的准入检查返回 `COMPOSITION_OPERATOR_UNSUPPORTED`。
- Model `groundwater.flow-field@3.1.0`，摘要 `sha256:54dba957a1445a2af1e563db7ee861cfdf241a71e7b3fc2996dfc5966833d5df`。产品入口为预设「地下水流场」，Agent 工具为 `run_flow_field`。 [Model JSON](../../source/domains/groundwater/6.2.0/models/flow-field.model.json)
- 声明绑定的验证用例 [Pack Golden](../../source/products/groundwater/development/workflow-golden-cases.v2.json) `flow-field`，夹具 `observations=groundwater.flow-field`，重点输出 `summary`，[同身份基线](../../source/products/groundwater/development/workflow-goldens/flow-field.json)，[外部来源样本声明](../../source/products/groundwater/development/real-data-cases.v2.json) `usgs-mrva-flow-field`，来源组 `usgs-mrva-2024`，输入角色 `observations`

| 输入角色 | 基数 | 编译后的格式 / 媒体类型 | Model 调用参数 |
| --- | --- | --- | --- |
| `observations` | `one` | `arrow-ipc-file (application/vnd.apache.arrow.file)`<br>`parquet (application/vnd.apache.parquet)`<br>`csv (text/csv)` | `versionId` |

| 节点 | 精确 Operator · Runtime | 组合成员 | 端口输入绑定 | 节点参数 ← Workflow 参数 | Manifest Schema 默认声明 |
| --- | --- | --- | --- | --- | --- |
| `head` | `groundwater.head.calculate@1.0.0` · `native` | 否 | `observations ← input:observations` | `referenceElevationField`<br>`depthToWaterField`<br>`referenceElevationUnit`<br>`depthToWaterUnit`<br>`outputField ← valueField` | `outputField` = `"hydraulic_head_m"`（已显式绑定） |
| `snapshot` | `timeseries.snapshot@1.0.0` · `native` | 否 | `observations ← head.heads` | `groupField ← wellField`<br>`timeField`<br>`valueField`<br>`targetTime`<br>`selection`<br>`maxAgeDays` | — |
| `idw` | `points.interpolate.idw@1.0.0` · `native` | 否 | `points ← snapshot.snapshot` | `valueField`<br>`xField`<br>`yField`<br>`sourceCrs`<br>`targetCrs`<br>`pixelSize`<br>`power`<br>`neighborCount` | `neighborCount` = `12`（已显式绑定）<br>`power` = `2`（已显式绑定） |
| `gradient` | `groundwater.hydraulic_gradient@1.0.0` · `native` | 否 | `head ← idw.raster` | — | `band` = `1`（未绑定） |
| `velocity` | `groundwater.darcy_velocity@1.0.0` · `native` | 否 | `gradientMagnitude ← gradient.magnitude`<br>`flowDirection ← gradient.direction` | `hydraulicConductivityMetersPerDay`<br>`effectivePorosity` | — |

<details><summary>Workflow 参数来源</summary>

| Workflow 参数 | Model 调用参数 | 来源 |
| --- | --- | --- |
| `wellField` | `wellField` | 调用方必填 |
| `timeField` | `timeField` | 调用方必填 |
| `valueField` | `valueField` | Model default `"hydraulic_head_m"`，可显式覆盖 |
| `xField` | `xField` | 调用方必填 |
| `yField` | `yField` | 调用方必填 |
| `referenceElevationField` | `referenceElevationField` | 调用方必填 |
| `depthToWaterField` | `depthToWaterField` | 调用方必填 |
| `referenceElevationUnit` | `referenceElevationUnit` | Model default `"m"`，可显式覆盖 |
| `depthToWaterUnit` | `depthToWaterUnit` | Model default `"m"`，可显式覆盖 |
| `targetTime` | `targetTime` | 调用方必填 |
| `selection` | `selection` | Model default `"latestAtOrBefore"`，可显式覆盖 |
| `maxAgeDays` | `maxAgeDays` | Model default `30`，可显式覆盖 |
| `sourceCrs` | `sourceCrs` | 调用方必填 |
| `targetCrs` | `targetCrs` | 调用方必填 |
| `pixelSize` | `pixelSize` | 调用方必填 |
| `power` | `power` | Model default `2`，可显式覆盖 |
| `neighborCount` | `neighborCount` | Model default `12`，可显式覆盖 |
| `hydraulicConductivityMetersPerDay` | `hydraulicConductivityMetersPerDay` | 调用方必填 |
| `effectivePorosity` | `effectivePorosity` | 调用方必填 |

</details>

| 命名输出 | 节点端口 | 编译后的底层合同 | Model 发布声明 | UI 标签 · 查看器 |
| --- | --- | --- | --- | --- |
| `snapshot` | `snapshot.snapshot` | `artifact`<br>`parquet (application/vnd.apache.parquet)` | `parquet (application/vnd.apache.parquet)` · `FILE_QUERYABLE` | 目标时点观测 · `table` |
| `snapshot-diagnostics` | `snapshot.diagnostics` | `artifact`<br>`parquet (application/vnd.apache.parquet)` | `parquet (application/vnd.apache.parquet)` · `FILE_QUERYABLE` | 时点选择诊断 · `table` |
| `head-raster` | `idw.raster` | `artifact`<br>`cog (image/tiff)`<br>`geotiff (image/tiff)` | `geotiff (image/tiff)` · `FILE_ONLY` | 插值水头栅格 · `map` |
| `gradient` | `gradient.magnitude` | `artifact`<br>`cog (image/tiff)`<br>`geotiff (image/tiff)` | `geotiff (image/tiff)` · `FILE_ONLY` | 推导的水力梯度 · `map` |
| `flow-direction` | `gradient.direction` | `artifact`<br>`cog (image/tiff)`<br>`geotiff (image/tiff)` | `geotiff (image/tiff)` · `FILE_ONLY` | 推导的流向 · `map` |
| `darcy-flux` | `velocity.darcyFlux` | `artifact`<br>`cog (image/tiff)`<br>`geotiff (image/tiff)` | `geotiff (image/tiff)` · `FILE_ONLY` | 推导的达西通量 · `map` |
| `seepage-velocity` | `velocity.seepageVelocity` | `artifact`<br>`cog (image/tiff)`<br>`geotiff (image/tiff)` | `geotiff (image/tiff)` · `FILE_ONLY` | 推导的孔隙流速 · `map` |
| `summary` | `velocity.summary` | `value`<br>`{"properties":{"darcyFluxFormula":{"type":"string"},"darcyFluxUnit":{"const":"m/day","type":"string"},"directionConvention":{"type":"string"},"directionUnit":{"const":"degree","type":"string"},"effectivePorosity":{"exclusiveMinimum":0,"maximum":1,"type":"number"},"horizontalCrs":{"type":"string"},"hydraulicConductivityMetersPerDay":{"minimum":0,"type":"number"},"interpretation":{"type":"string"},"limitations":{"items":{"type":"string"},"minItems":1,"type":"array"},"resultKind":{"const":"MODEL_DERIVED_NOT_OBSERVED","type":"string"},"seepageVelocityFormula":{"type":"string"},"seepageVelocityUnit":{"const":"m/day","type":"string"},"validCellCount":{"minimum":0,"type":"integer"},"velocityUnit":{"const":"m/day","type":"string"}},"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 汇总结果 · `json` |

### groundwater.kriging@1.0.0

- Workflow 定义摘要 `sha256:b6671797ceb615690a8e08611d2489938d75076e58e92cde1978149db9bf78bd`。 [Workflow JSON](../../source/domains/groundwater/6.2.0/workflows/kriging.workflow.json)
- 组合算子成员 —。本图包含未准入的精确版本，动态组合草稿的准入检查返回 `COMPOSITION_OPERATOR_UNSUPPORTED`。
- Model `groundwater.kriging@1.0.0`，摘要 `sha256:6c6bd25880ca11f7f413c1cba968402fde5d07710b4069d477fb026562ff6fa8`。产品入口为预设「克里金插值」，Agent 工具为 `run_kriging`。 [Model JSON](../../source/domains/groundwater/6.2.0/models/kriging.model.json)
- 声明绑定的验证用例 [外部来源样本声明](../../source/products/groundwater/development/real-data-cases.v2.json) `usgs-mrva-kriging`，来源组 `usgs-mrva-2024`，输入角色 `points`

| 输入角色 | 基数 | 编译后的格式 / 媒体类型 | Model 调用参数 |
| --- | --- | --- | --- |
| `points` | `one` | `json (application/json)`<br>`arrow-ipc-file (application/vnd.apache.arrow.file)`<br>`parquet (application/vnd.apache.parquet)`<br>`csv (text/csv)` | `versionId` |

| 节点 | 精确 Operator · Runtime | 组合成员 | 端口输入绑定 | 节点参数 ← Workflow 参数 | Manifest Schema 默认声明 |
| --- | --- | --- | --- | --- | --- |
| `kriging` | `points.interpolate.kriging@1.0.0` · `native` | 否 | `points ← input:points` | `valueField`<br>`xField`<br>`yField`<br>`sourceCrs`<br>`targetCrs`<br>`pixelSize`<br>`extentMinX`<br>`extentMinY`<br>`extentMaxX`<br>`extentMaxY`<br>`krigingType`<br>`variogramModel`<br>`variogramRange`<br>`variogramSill`<br>`variogramNugget`<br>`variogramBinCount`<br>`neighborCount`<br>`minimumNeighbors`<br>`searchRadius`<br>`exact`<br>`idwPower`<br>`valueUnit` | `krigingType` = `"ordinary"`（已显式绑定）<br>`variogramModel` = `"spherical"`（已显式绑定）<br>`variogramBinCount` = `10`（已显式绑定）<br>`neighborCount` = `12`（已显式绑定）<br>`minimumNeighbors` = `4`（已显式绑定）<br>`searchRadius` = `null`（已显式绑定）<br>`exact` = `true`（已显式绑定）<br>`idwPower` = `2.0`（已显式绑定）<br>`valueUnit` = `""`（已显式绑定） |

<details><summary>Workflow 参数来源</summary>

| Workflow 参数 | Model 调用参数 | 来源 |
| --- | --- | --- |
| `valueField` | `valueField` | 调用方必填 |
| `xField` | `xField` | 调用方必填 |
| `yField` | `yField` | 调用方必填 |
| `sourceCrs` | `sourceCrs` | 调用方必填 |
| `targetCrs` | `targetCrs` | 调用方必填 |
| `pixelSize` | `pixelSize` | 调用方必填 |
| `extentMinX` | `extentMinX` | 调用方选填或条件必填，见 Model Schema |
| `extentMinY` | `extentMinY` | 调用方选填或条件必填，见 Model Schema |
| `extentMaxX` | `extentMaxX` | 调用方选填或条件必填，见 Model Schema |
| `extentMaxY` | `extentMaxY` | 调用方选填或条件必填，见 Model Schema |
| `krigingType` | `krigingType` | Model default `"ordinary"`，可显式覆盖 |
| `variogramModel` | `variogramModel` | Model default `"spherical"`，可显式覆盖 |
| `variogramRange` | `variogramRange` | 调用方选填或条件必填，见 Model Schema |
| `variogramSill` | `variogramSill` | 调用方选填或条件必填，见 Model Schema |
| `variogramNugget` | `variogramNugget` | 调用方选填或条件必填，见 Model Schema |
| `variogramBinCount` | `variogramBinCount` | Model default `10`，可显式覆盖 |
| `neighborCount` | `neighborCount` | Model default `12`，可显式覆盖 |
| `minimumNeighbors` | `minimumNeighbors` | Model default `4`，可显式覆盖 |
| `searchRadius` | `searchRadius` | Model default `null`，可显式覆盖 |
| `exact` | `exact` | Model default `true`，可显式覆盖 |
| `idwPower` | `idwPower` | Model default `2.0`，可显式覆盖 |
| `valueUnit` | `valueUnit` | Model default `""`，可显式覆盖 |

</details>

| 命名输出 | 节点端口 | 编译后的底层合同 | Model 发布声明 | UI 标签 · 查看器 |
| --- | --- | --- | --- | --- |
| `kriging-prediction` | `kriging.prediction` | `artifact`<br>`geotiff (image/tiff)` | `geotiff (image/tiff)` · `FILE_ONLY` | 克里金预测栅格 · `map` |
| `kriging-variance` | `kriging.variance` | `artifact`<br>`geotiff (image/tiff)` | `geotiff (image/tiff)` · `FILE_ONLY` | 克里金方差栅格 · `map` |
| `variogram-diagnostics` | `kriging.variogramDiagnostics` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 变异函数诊断 · `json` |
| `kriging-loo` | `kriging.loo` | `artifact`<br>`csv (text/csv)` | `csv (text/csv)` · `FILE_QUERYABLE` | 留一法交叉验证 · `table` |
| `kriging-summary` | `kriging.summary` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 克里金运行摘要 · `json` |

### groundwater.monitoring-coverage@1.2.0

- Workflow 定义摘要 `sha256:453cf5c204b8868b17aa929660b8e2f4a940f1a10ae9542e23badb19ce9b6d4b`。 [Workflow JSON](../../source/domains/groundwater/6.2.0/workflows/monitoring-coverage.workflow.json)
- 组合算子成员 `data.convert@1.0.0`。本图包含未准入的精确版本，动态组合草稿的准入检查返回 `COMPOSITION_OPERATOR_UNSUPPORTED`。
- Model `groundwater.monitoring-coverage@3.1.0`，摘要 `sha256:6e96cbafb0b85ca55f763037900ae1fe3425970eaf724dd71f42fa215678fe09`。产品入口为预设「监测井覆盖」，Agent 工具为 `run_monitoring_coverage`。 [Model JSON](../../source/domains/groundwater/6.2.0/models/monitoring-coverage.model.json)
- 声明绑定的验证用例 未找到结构化用例声明

| 输入角色 | 基数 | 编译后的格式 / 媒体类型 | Model 调用参数 |
| --- | --- | --- | --- |
| `wells` | `one` | `geojson (application/geo+json)` | `versionId` |

| 节点 | 精确 Operator · Runtime | 组合成员 | 端口输入绑定 | 节点参数 ← Workflow 参数 | Manifest Schema 默认声明 |
| --- | --- | --- | --- | --- | --- |
| `dissolve` | `vector.dissolve@1.0.0` · `qgis` | 否 | `vector ← input:wells` | `pointCoverageMode` | `pointCoverageMode` = `false`（已显式绑定）<br>`separateDisjoint` = `false`（未绑定） |
| `hull` | `vector.convex_hull@1.0.0` · `qgis` | 否 | `vector ← dissolve.vector` | — | — |
| `geojson` | `data.convert@1.0.0` · `native` | 是 | `data ← hull.vector` | `outputFormat` | — |

<details><summary>Workflow 参数来源</summary>

| Workflow 参数 | Model 调用参数 | 来源 |
| --- | --- | --- |
| `outputFormat` | `outputFormat` | Model default `"geojson"`，可显式覆盖 |
| `pointCoverageMode` | `pointCoverageMode` | Model default `true`，可显式覆盖 |

</details>

| 命名输出 | 节点端口 | 编译后的底层合同 | Model 发布声明 | UI 标签 · 查看器 |
| --- | --- | --- | --- | --- |
| `coverage` | `geojson.data` | `artifact`<br>`flatgeobuf (application/flatgeobuf)`<br>`geojson (application/geo+json)`<br>`geopackage (application/geopackage+sqlite3)`<br>`json (application/json)`<br>`arrow-ipc-file (application/vnd.apache.arrow.file)`<br>`geoparquet (application/vnd.apache.parquet)`<br>`parquet (application/vnd.apache.parquet)`<br>`las (application/vnd.las)`<br>`copc (application/vnd.laz)`<br>`laz (application/vnd.laz)`<br>`cog (image/tiff)`<br>`geotiff (image/tiff)`<br>`csv (text/csv)` | `geojson (application/geo+json)` · `FILE_ONLY` | 井点几何凸包 · `map` |
| `coverage-summary` | `dissolve.summary` | `value`<br>`{"properties":{"areaSquareMeters":{"exclusiveMinimum":0,"type":"number"},"areaUnit":{"const":"m2","type":"string"},"crs":{"minLength":1,"type":"string"},"crsKind":{"enum":["GEOGRAPHIC","PROJECTED"],"type":"string"},"duplicatePointCount":{"minimum":0,"type":"integer"},"hullGeometryType":{"const":"Polygon","type":"string"},"inputFeatureCount":{"minimum":0,"type":"integer"},"inputPointCount":{"minimum":0,"type":"integer"},"interpretation":{"minLength":1,"type":"string"},"limitations":{"items":{"minLength":1,"type":"string"},"minItems":1,"type":"array"},"measurementMethod":{"enum":["GEODESIC_ELLIPSOID","PLANAR_PROJECTED_TO_METRES"],"type":"string"},"perimeterMeters":{"exclusiveMinimum":0,"type":"number"},"perimeterUnit":{"const":"m","type":"string"},"resultKind":{"enum":["DISSOLVED_VECTOR","GEOMETRIC_CONVEX_HULL"],"type":"string"},"uniquePointCount":{"minimum":0,"type":"integer"}},"required":["resultKind","interpretation","limitations"],"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 井点几何覆盖摘要 · `json` |

### groundwater.monitoring-trend@1.1.0

- Workflow 定义摘要 `sha256:156c558d24d5495124cb1735d9b1c2ba0baffafa42a2dc296425891150a5b07e`。 [Workflow JSON](../../source/domains/groundwater/6.2.0/workflows/monitoring-trend.workflow.json)
- 组合算子成员 —。本图包含未准入的精确版本，动态组合草稿的准入检查返回 `COMPOSITION_OPERATOR_UNSUPPORTED`。
- Model `groundwater.monitoring-trend@3.1.0`，摘要 `sha256:43566799121f36c726bc0746308095866a79510e5c057a96b8b543f6dd19e259`。产品入口为预设「监测趋势」，Agent 工具为 `run_monitoring_trend`。 [Model JSON](../../source/domains/groundwater/6.2.0/models/monitoring-trend.model.json)
- 声明绑定的验证用例 [Pack Golden](../../source/products/groundwater/development/workflow-golden-cases.v2.json) `monitoring-trend`，夹具 `observations=monitoring.timeseries`，重点输出 `aggregate-summary, change-point-summary, completeness-summary, gap-run-summary, linear-summary, mann-kendall-summary, outlier-summary, period-change-summary, seasonal-trend-summary, threshold-event-summary`，[同身份基线](../../source/products/groundwater/development/workflow-goldens/monitoring-trend.json)，[外部来源样本声明](../../source/products/groundwater/development/real-data-cases.v2.json) `local-monitoring-trend`，来源组 `local-groundwater-v2`，输入角色 `observations`

| 输入角色 | 基数 | 编译后的格式 / 媒体类型 | Model 调用参数 |
| --- | --- | --- | --- |
| `observations` | `one` | `arrow-ipc-file (application/vnd.apache.arrow.file)`<br>`parquet (application/vnd.apache.parquet)`<br>`csv (text/csv)` | `versionId` |

| 节点 | 精确 Operator · Runtime | 组合成员 | 端口输入绑定 | 节点参数 ← Workflow 参数 | Manifest Schema 默认声明 |
| --- | --- | --- | --- | --- | --- |
| `aggregate` | `timeseries.aggregate@1.0.0` · `native` | 否 | `observations ← input:observations` | `groupField`<br>`timeField`<br>`valueField`<br>`frequency` | — |
| `completeness` | `timeseries.completeness@1.0.0` · `native` | 否 | `observations ← input:observations` | `groupField`<br>`timeField`<br>`valueField`<br>`frequency`<br>`expectedStart`<br>`expectedEnd` | — |
| `linear-trend` | `statistics.linear_trend@1.0.0` · `native` | 否 | `observations ← input:observations` | `groupField`<br>`timeField`<br>`valueField` | — |
| `mann-kendall` | `statistics.mann_kendall@1.0.0` · `native` | 否 | `observations ← input:observations` | `groupField`<br>`timeField`<br>`valueField`<br>`alpha` | `alpha` = `0.05`（已显式绑定） |
| `mad-outliers` | `statistics.mad_outliers@1.0.0` · `native` | 否 | `observations ← input:observations` | `groupField`<br>`valueField`<br>`threshold` | `threshold` = `3.5`（已显式绑定） |
| `period-change` | `timeseries.period_change@1.0.0` · `native` | 否 | `observations ← input:observations` | `groupField`<br>`timeField`<br>`valueField`<br>`baselineStart`<br>`baselineEnd`<br>`comparisonStart`<br>`comparisonEnd`<br>`aggregation` | — |
| `change-point` | `statistics.pettitt_change_point@1.0.0` · `native` | 否 | `observations ← input:observations` | `groupField`<br>`timeField`<br>`valueField`<br>`frequency`<br>`alpha` | `alpha` = `0.05`（已显式绑定） |
| `seasonal-trend` | `statistics.seasonal_mann_kendall@1.0.0` · `native` | 否 | `observations ← input:observations` | `groupField`<br>`timeField`<br>`valueField`<br>`enabled ← seasonalTrendEnabled`<br>`seasonFrequency`<br>`alpha` | `alpha` = `0.05`（已显式绑定）<br>`enabled` = `true`（已显式绑定） |
| `gap-runs` | `timeseries.gap_runs@1.0.0` · `native` | 否 | `observations ← input:observations` | `groupField`<br>`timeField`<br>`valueField`<br>`frequency`<br>`expectedStart`<br>`expectedEnd` | — |
| `threshold-events` | `timeseries.threshold_events@1.0.0` · `native` | 否 | `observations ← input:observations` | `groupField`<br>`timeField`<br>`valueField`<br>`frequency`<br>`enabled ← thresholdEventsEnabled`<br>`comparison ← eventComparison`<br>`thresholdValue ← eventThreshold` | `enabled` = `true`（已显式绑定） |

<details><summary>Workflow 参数来源</summary>

| Workflow 参数 | Model 调用参数 | 来源 |
| --- | --- | --- |
| `groupField` | `groupField` | 调用方必填 |
| `timeField` | `timeField` | 调用方必填 |
| `valueField` | `valueField` | 调用方必填 |
| `frequency` | `frequency` | 调用方必填 |
| `expectedStart` | `expectedStart` | 调用方必填 |
| `expectedEnd` | `expectedEnd` | 调用方必填 |
| `baselineStart` | `baselineStart` | 调用方必填 |
| `baselineEnd` | `baselineEnd` | 调用方必填 |
| `comparisonStart` | `comparisonStart` | 调用方必填 |
| `comparisonEnd` | `comparisonEnd` | 调用方必填 |
| `aggregation` | `aggregation` | 调用方必填 |
| `alpha` | `alpha` | Model default `0.05`，可显式覆盖 |
| `threshold` | `threshold` | Model default `3.5`，可显式覆盖 |
| `seasonalTrendEnabled` | `seasonalTrendEnabled` | Model default `false`，可显式覆盖 |
| `seasonFrequency` | `seasonFrequency` | 调用方选填或条件必填，见 Model Schema |
| `thresholdEventsEnabled` | `thresholdEventsEnabled` | Model default `false`，可显式覆盖 |
| `eventComparison` | `eventComparison` | 调用方选填或条件必填，见 Model Schema |
| `eventThreshold` | `eventThreshold` | 调用方选填或条件必填，见 Model Schema |

</details>

| 命名输出 | 节点端口 | 编译后的底层合同 | Model 发布声明 | UI 标签 · 查看器 |
| --- | --- | --- | --- | --- |
| `aggregated-series` | `aggregate.series` | `artifact`<br>`parquet (application/vnd.apache.parquet)` | `parquet (application/vnd.apache.parquet)` · `FILE_QUERYABLE` | 聚合后的监测序列 · `chart` |
| `aggregate-summary` | `aggregate.summary` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 聚合统计摘要 · `table` |
| `completeness-profiles` | `completeness.profiles` | `artifact`<br>`parquet (application/vnd.apache.parquet)` | `parquet (application/vnd.apache.parquet)` · `FILE_QUERYABLE` | 数据完整性明细 · `table` |
| `completeness-summary` | `completeness.summary` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 数据完整性摘要 · `table` |
| `linear-trends` | `linear-trend.trends` | `artifact`<br>`parquet (application/vnd.apache.parquet)` | `parquet (application/vnd.apache.parquet)` · `FILE_QUERYABLE` | 线性趋势结果 · `chart` |
| `linear-summary` | `linear-trend.summary` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 线性趋势摘要 · `table` |
| `mann-kendall-trends` | `mann-kendall.trends` | `artifact`<br>`parquet (application/vnd.apache.parquet)` | `parquet (application/vnd.apache.parquet)` · `FILE_QUERYABLE` | Mann–Kendall 趋势检验 · `chart` |
| `mann-kendall-summary` | `mann-kendall.summary` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 趋势检验摘要 · `table` |
| `outlier-observations` | `mad-outliers.observations` | `artifact`<br>`parquet (application/vnd.apache.parquet)` | `parquet (application/vnd.apache.parquet)` · `FILE_QUERYABLE` | 异常观测明细 · `table` |
| `outlier-summary` | `mad-outliers.summary` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 异常检测摘要 · `table` |
| `period-changes` | `period-change.changes` | `artifact`<br>`parquet (application/vnd.apache.parquet)` | `parquet (application/vnd.apache.parquet)` · `FILE_QUERYABLE` | 期间变化明细 · `chart` |
| `period-change-summary` | `period-change.summary` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 期间变化摘要 · `table` |
| `change-points` | `change-point.changePoints` | `artifact`<br>`parquet (application/vnd.apache.parquet)` | `parquet (application/vnd.apache.parquet)` · `FILE_QUERYABLE` | 变化点检验结果 · `table` |
| `change-point-summary` | `change-point.summary` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 变化点检验摘要 · `table` |
| `seasonal-trends` | `seasonal-trend.trends` | `artifact`<br>`parquet (application/vnd.apache.parquet)` | `parquet (application/vnd.apache.parquet)` · `FILE_QUERYABLE` | 季节 Mann–Kendall 趋势检验 · `chart` |
| `seasonal-trend-summary` | `seasonal-trend.summary` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 季节趋势检验摘要 · `table` |
| `gap-runs` | `gap-runs.gaps` | `artifact`<br>`parquet (application/vnd.apache.parquet)` | `parquet (application/vnd.apache.parquet)` · `FILE_QUERYABLE` | 连续缺测段 · `table` |
| `gap-run-summary` | `gap-runs.summary` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 连续缺测段摘要 · `table` |
| `threshold-classification` | `threshold-events.classified` | `artifact`<br>`parquet (application/vnd.apache.parquet)` | `parquet (application/vnd.apache.parquet)` · `FILE_QUERYABLE` | 阈值分类序列 · `chart` |
| `threshold-events` | `threshold-events.events` | `artifact`<br>`parquet (application/vnd.apache.parquet)` | `parquet (application/vnd.apache.parquet)` · `FILE_QUERYABLE` | 阈值事件明细 · `table` |
| `threshold-event-summary` | `threshold-events.summary` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 阈值事件摘要 · `table` |

### groundwater.porous-agricultural-inversion@1.0.0

- Workflow 定义摘要 `sha256:cae8e3363a0e2ca1a48b41d504726ec3db72cc0280e2a7c8b8b106357c2f2b79`。 [Workflow JSON](../../source/domains/groundwater/6.2.0/workflows/porous-agricultural-inversion.workflow.json)
- 组合算子成员 —。本图包含未准入的精确版本，动态组合草稿的准入检查返回 `COMPOSITION_OPERATOR_UNSUPPORTED`。
- Model `groundwater.porous-agricultural-inversion@1.0.0`，摘要 `sha256:527d5bd118801f76675a05019c053d5d1180f05304e360209e29181e6efb9f72`。产品入口为预设「孔隙型农业抽水反演」，Agent 工具为 `run_porous_agri_inversion`。 [Model JSON](../../source/domains/groundwater/6.2.0/models/porous-agricultural-inversion.model.json)
- 声明绑定的验证用例 未找到结构化用例声明

| 输入角色 | 基数 | 编译后的格式 / 媒体类型 | Model 调用参数 |
| --- | --- | --- | --- |
| `agricultural_data` | `one` | `zip (application/zip)` | `agriculturalDataVersionId` |

| 节点 | 精确 Operator · Runtime | 组合成员 | 端口输入绑定 | 节点参数 ← Workflow 参数 | Manifest Schema 默认声明 |
| --- | --- | --- | --- | --- | --- |
| `execute` | `groundwater.porous.agri_invert@1.0.0` · `remote_http`<br>`porous-groundwater/agri.invert` | 否 | `agricultural_data ← input:agricultural_data` | — | — |

Workflow 参数集合为空。输入文件仍可承载规则、服务或算法配置。

| 命名输出 | 节点端口 | 编译后的底层合同 | Model 发布声明 | UI 标签 · 查看器 |
| --- | --- | --- | --- | --- |
| `porous-agricultural-arrays` | `execute.posterior_arrays` | `artifact`<br>`npy (application/x-npy)` | `npy (application/x-npy)` · `FILE_ONLY` | 基准、线性估计和重算水头 · `metadata` |
| `porous-agricultural-metrics` | `execute.metrics` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 农业抽水反演指标 · `json` |
| `porous-agricultural-report` | `execute.report_package` | `artifact`<br>`zip (application/zip)` | `zip (application/zip)` · `FILE_ONLY` | 农业反演分组及报告 · `metadata` |
| `porous-agricultural-inversion-receipt` | `execute.run_receipt` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 运行凭据 · `json` |

### groundwater.porous-calibration@1.0.0

- Workflow 定义摘要 `sha256:41c2dccfb30b0dffc5ebaa7463b8c89bd29d807efd6ae011f8b033097c91cd00`。 [Workflow JSON](../../source/domains/groundwater/6.2.0/workflows/porous-calibration.workflow.json)
- 组合算子成员 —。本图包含未准入的精确版本，动态组合草稿的准入检查返回 `COMPOSITION_OPERATOR_UNSUPPORTED`。
- Model `groundwater.porous-calibration@1.0.0`，摘要 `sha256:7abcb95817db7db8e9c20396e0f55782006b24e7ffaf012110346440372898b4`。产品入口为预设「孔隙型 PEST 水头率定」，Agent 工具为 `run_porous_pest_calibration`。 [Model JSON](../../source/domains/groundwater/6.2.0/models/porous-calibration.model.json)
- 声明绑定的验证用例 未找到结构化用例声明

| 输入角色 | 基数 | 编译后的格式 / 媒体类型 | Model 调用参数 |
| --- | --- | --- | --- |
| `project_package` | `one` | `zip (application/zip)` | `projectPackageVersionId` |
| `observations` | `one` | `json (application/json)` | `calibrationObservationsVersionId` |

| 节点 | 精确 Operator · Runtime | 组合成员 | 端口输入绑定 | 节点参数 ← Workflow 参数 | Manifest Schema 默认声明 |
| --- | --- | --- | --- | --- | --- |
| `compile` | `groundwater.porous.model_compile@1.0.0` · `remote_http`<br>`porous-groundwater/model.compile` | 否 | `project_package ← input:project_package` | — | — |
| `calibrate` | `groundwater.porous.pest_calibrate@1.1.0` · `remote_http`<br>`porous-groundwater/pest.calibrate` | 否 | `model_package ← compile.model_package`<br>`observations ← input:observations` | `includeCalibratedModel` | — |
| `simulate` | `groundwater.porous.flow_transport_simulate@1.0.0` · `remote_http`<br>`porous-groundwater/flow_transport.simulate` | 否 | `model_package ← calibrate.calibrated_model_package` | — | — |

<details><summary>Workflow 参数来源</summary>

| Workflow 参数 | Model 调用参数 | 来源 |
| --- | --- | --- |
| `includeCalibratedModel` | `includeCalibratedModel` | Model default `true`，可显式覆盖 |

</details>

| 命名输出 | 节点端口 | 编译后的底层合同 | Model 发布声明 | UI 标签 · 查看器 |
| --- | --- | --- | --- | --- |
| `porous-calibrated-parameters` | `calibrate.calibrated_parameters` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 率定参数倍率 · `json` |
| `porous-calibration-residuals` | `calibrate.residual_diagnostics` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 率定残差 · `json` |
| `porous-calibration-package` | `calibrate.calibration_package` | `artifact`<br>`zip (application/zip)` | `zip (application/zip)` · `FILE_ONLY` | PEST 率定报告 · `metadata` |
| `porous-calibrated-model` | `calibrate.calibrated_model_package` | `artifact`<br>`zip (application/zip)` | `zip (application/zip)` · `FILE_ONLY` | 可重算的已率定模型包 · `metadata` |
| `porous-calibrated-summary` | `simulate.calculation_summary` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 率定后仿真摘要 · `json` |
| `porous-calibrated-result` | `simulate.result_package` | `artifact`<br>`zip (application/zip)` | `zip (application/zip)` · `FILE_ONLY` | 率定后仿真结果包 · `metadata` |
| `porous-pest-compile-receipt` | `compile.run_receipt` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 率定前编译凭据 · `json` |
| `porous-pest-receipt` | `calibrate.run_receipt` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | PEST 率定凭据 · `json` |
| `porous-pest-simulation-receipt` | `simulate.run_receipt` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 率定后仿真凭据 · `json` |

### groundwater.porous-inversion-sampling@1.0.0

- Workflow 定义摘要 `sha256:beaf2d3606102533aea732991e0942f4c9a7dbcd5eb1a1149486c057eafdb6d2`。 [Workflow JSON](../../source/domains/groundwater/6.2.0/workflows/porous-inversion-sampling.workflow.json)
- 组合算子成员 —。本图包含未准入的精确版本，动态组合草稿的准入检查返回 `COMPOSITION_OPERATOR_UNSUPPORTED`。
- Model `groundwater.porous-inversion-sampling@1.0.0`，摘要 `sha256:04239793564e3b006c627b6e3839c0d260aa861b4235cfceee35151462d69b4b`。产品入口为预设「孔隙型反演采样」，Agent 工具为 `run_porous_inversion_sample`。 [Model JSON](../../source/domains/groundwater/6.2.0/models/porous-inversion-sampling.model.json)
- 声明绑定的验证用例 未找到结构化用例声明

| 输入角色 | 基数 | 编译后的格式 / 媒体类型 | Model 调用参数 |
| --- | --- | --- | --- |
| `weights` | `one` | `binary (application/octet-stream)` | `inversionWeightsVersionId` |
| `sample_config` | `one` | `json (application/json)` | `inversionSamplingVersionId` |

| 节点 | 精确 Operator · Runtime | 组合成员 | 端口输入绑定 | 节点参数 ← Workflow 参数 | Manifest Schema 默认声明 |
| --- | --- | --- | --- | --- | --- |
| `execute` | `groundwater.porous.inversion_sample@1.0.0` · `remote_http`<br>`porous-groundwater/inversion.sample` | 否 | `weights ← input:weights`<br>`sample_config ← input:sample_config` | — | — |

Workflow 参数集合为空。输入文件仍可承载规则、服务或算法配置。

| 命名输出 | 节点端口 | 编译后的底层合同 | Model 发布声明 | UI 标签 · 查看器 |
| --- | --- | --- | --- | --- |
| `porous-posterior-samples` | `execute.posterior_samples` | `artifact`<br>`npy (application/x-npy)` | `npy (application/x-npy)` · `FILE_ONLY` | 后验样本数组 · `metadata` |
| `porous-sampling-metrics` | `execute.metrics` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 采样指标 · `json` |
| `porous-sampling-report` | `execute.report_package` | `artifact`<br>`zip (application/zip)` | `zip (application/zip)` · `FILE_ONLY` | 后验均值与标准差 · `metadata` |
| `porous-inversion-sampling-receipt` | `execute.run_receipt` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 运行凭据 · `json` |

### groundwater.porous-inversion-training@1.0.0

- Workflow 定义摘要 `sha256:03f026af842aa36fe57f4f40c5f98adea81393e70878067cc6a4343073a847a6`。 [Workflow JSON](../../source/domains/groundwater/6.2.0/workflows/porous-inversion-training.workflow.json)
- 组合算子成员 —。本图包含未准入的精确版本，动态组合草稿的准入检查返回 `COMPOSITION_OPERATOR_UNSUPPORTED`。
- Model `groundwater.porous-inversion-training@1.0.0`，摘要 `sha256:59373130cb1d942661066f2e2a2f0c4fa5c18b13ba8a077c861aa277f45f06eb`。产品入口为预设「孔隙型反演训练」，Agent 工具为 `run_porous_inversion_train`。 [Model JSON](../../source/domains/groundwater/6.2.0/models/porous-inversion-training.model.json)
- 声明绑定的验证用例 未找到结构化用例声明

| 输入角色 | 基数 | 编译后的格式 / 媒体类型 | Model 调用参数 |
| --- | --- | --- | --- |
| `training_data` | `one` | `zip (application/zip)` | `inversionTrainingVersionId` |

| 节点 | 精确 Operator · Runtime | 组合成员 | 端口输入绑定 | 节点参数 ← Workflow 参数 | Manifest Schema 默认声明 |
| --- | --- | --- | --- | --- | --- |
| `execute` | `groundwater.porous.inversion_train@1.0.0` · `remote_http`<br>`porous-groundwater/inversion.train` | 否 | `training_data ← input:training_data` | — | — |

Workflow 参数集合为空。输入文件仍可承载规则、服务或算法配置。

| 命名输出 | 节点端口 | 编译后的底层合同 | Model 发布声明 | UI 标签 · 查看器 |
| --- | --- | --- | --- | --- |
| `porous-inversion-weights` | `execute.weights` | `artifact`<br>`binary (application/octet-stream)` | `binary (application/octet-stream)` · `FILE_ONLY` | 反演训练权重 · `metadata` |
| `porous-training-metrics` | `execute.metrics` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 训练与验证指标 · `json` |
| `porous-training-report` | `execute.report_package` | `artifact`<br>`zip (application/zip)` | `zip (application/zip)` · `FILE_ONLY` | 训练报告与缩放参数 · `metadata` |
| `porous-inversion-training-receipt` | `execute.run_receipt` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 运行凭据 · `json` |

### groundwater.porous-map@1.0.0

- Workflow 定义摘要 `sha256:a7da07e4f652b503a9f2caed833cfe8fb5cd8e3e7ae7a774202cfaf2c93f7d27`。 [Workflow JSON](../../source/domains/groundwater/6.2.0/workflows/porous-map.workflow.json)
- 组合算子成员 —。本图包含未准入的精确版本，动态组合草稿的准入检查返回 `COMPOSITION_OPERATOR_UNSUPPORTED`。
- Model `groundwater.porous-map@1.0.0`，摘要 `sha256:3d511a3eae8e79a39029064997b643d9d04ecf085a4725af86c67ccbdfddb06b`。产品入口为预设「孔隙型栅格地图准备」，Agent 工具为 `run_porous_map_prepare`。 [Model JSON](../../source/domains/groundwater/6.2.0/models/porous-map.model.json)
- 声明绑定的验证用例 未找到结构化用例声明

| 输入角色 | 基数 | 编译后的格式 / 媒体类型 | Model 调用参数 |
| --- | --- | --- | --- |
| `map_input` | `one` | `geotiff (image/tiff)` | `geoTiffVersionId` |

| 节点 | 精确 Operator · Runtime | 组合成员 | 端口输入绑定 | 节点参数 ← Workflow 参数 | Manifest Schema 默认声明 |
| --- | --- | --- | --- | --- | --- |
| `execute` | `groundwater.porous.map_prepare@1.0.0` · `remote_http`<br>`porous-groundwater/map.prepare` | 否 | `map_input ← input:map_input` | — | — |

Workflow 参数集合为空。输入文件仍可承载规则、服务或算法配置。

| 命名输出 | 节点端口 | 编译后的底层合同 | Model 发布声明 | UI 标签 · 查看器 |
| --- | --- | --- | --- | --- |
| `porous-map-raster` | `execute.geospatial_output` | `artifact`<br>`geotiff (image/tiff)` | `geotiff (image/tiff)` · `FILE_ONLY` | 可查看的 GeoTIFF · `map` |
| `porous-map-crs` | `execute.crs_attribution_metadata` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 坐标系、范围与波段信息 · `json` |
| `porous-map-receipt` | `execute.run_receipt` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 运行凭据 · `json` |

### groundwater.porous-scenario-comparison@1.0.0

- Workflow 定义摘要 `sha256:d34bcee2b7947fa73233b30c4e80533d125002ce9dc54be2fc95959db002fc9f`。 [Workflow JSON](../../source/domains/groundwater/6.2.0/workflows/porous-scenario-comparison.workflow.json)
- 组合算子成员 —。本图包含未准入的精确版本，动态组合草稿的准入检查返回 `COMPOSITION_OPERATOR_UNSUPPORTED`。
- Model `groundwater.porous-scenario-comparison@1.0.0`，摘要 `sha256:6274a3cde35704b8a88896e5051e0942e69900ec8a5820fbd13f8e3ef2feb8f7`。产品入口为预设「孔隙型情景对比」，Agent 工具为 `run_porous_scenario_compare`。 [Model JSON](../../source/domains/groundwater/6.2.0/models/porous-scenario-comparison.model.json)
- 声明绑定的验证用例 未找到结构化用例声明

| 输入角色 | 基数 | 编译后的格式 / 媒体类型 | Model 调用参数 |
| --- | --- | --- | --- |
| `scenario_package` | `one` | `zip (application/zip)` | `scenarioPackageVersionId` |

| 节点 | 精确 Operator · Runtime | 组合成员 | 端口输入绑定 | 节点参数 ← Workflow 参数 | Manifest Schema 默认声明 |
| --- | --- | --- | --- | --- | --- |
| `execute` | `groundwater.porous.scenario_compare@1.0.0` · `remote_http`<br>`porous-groundwater/scenario.compare` | 否 | `scenario_package ← input:scenario_package` | — | — |

Workflow 参数集合为空。输入文件仍可承载规则、服务或算法配置。

| 命名输出 | 节点端口 | 编译后的底层合同 | Model 发布声明 | UI 标签 · 查看器 |
| --- | --- | --- | --- | --- |
| `porous-scenario-table` | `execute.scenario_table` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 逐情景对比表 · `json` |
| `porous-scenario-summary` | `execute.comparison_summary` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 情景对比摘要 · `json` |
| `porous-scenario-results` | `execute.result_package` | `artifact`<br>`zip (application/zip)` | `zip (application/zip)` · `FILE_ONLY` | 逐情景仿真结果包 · `metadata` |
| `porous-scenario-comparison-receipt` | `execute.run_receipt` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 运行凭据 · `json` |

### groundwater.porous-simulation@1.0.0

- Workflow 定义摘要 `sha256:5805c3215561e9e095feea96c068598b673a6f8322f4ac2c2ac596ea71a9f895`。 [Workflow JSON](../../source/domains/groundwater/6.2.0/workflows/porous-simulation.workflow.json)
- 组合算子成员 —。本图包含未准入的精确版本，动态组合草稿的准入检查返回 `COMPOSITION_OPERATOR_UNSUPPORTED`。
- Model `groundwater.porous-simulation@1.0.0`，摘要 `sha256:7caf98b1ebf785d71f93eb1665b9c6e047a8707815f669c8885be9656d29f856`。产品入口为预设「孔隙型地下水流与溶质运移」，Agent 工具为 `run_porous_simulation`。 [Model JSON](../../source/domains/groundwater/6.2.0/models/porous-simulation.model.json)
- 声明绑定的验证用例 未找到结构化用例声明

| 输入角色 | 基数 | 编译后的格式 / 媒体类型 | Model 调用参数 |
| --- | --- | --- | --- |
| `project_package` | `one` | `zip (application/zip)` | `projectPackageVersionId` |

| 节点 | 精确 Operator · Runtime | 组合成员 | 端口输入绑定 | 节点参数 ← Workflow 参数 | Manifest Schema 默认声明 |
| --- | --- | --- | --- | --- | --- |
| `compile` | `groundwater.porous.model_compile@1.0.0` · `remote_http`<br>`porous-groundwater/model.compile` | 否 | `project_package ← input:project_package` | — | — |
| `simulate` | `groundwater.porous.flow_transport_simulate@1.0.0` · `remote_http`<br>`porous-groundwater/flow_transport.simulate` | 否 | `model_package ← compile.model_package` | — | — |
| `analyze` | `groundwater.porous.result_analyze@1.0.0` · `remote_http`<br>`porous-groundwater/result.analyze` | 否 | `result_package ← simulate.result_package` | — | — |
| `render` | `groundwater.porous.result_render@1.0.0` · `remote_http`<br>`porous-groundwater/result.render` | 否 | `result_package ← simulate.result_package` | — | — |

Workflow 参数集合为空。输入文件仍可承载规则、服务或算法配置。

| 命名输出 | 节点端口 | 编译后的底层合同 | Model 发布声明 | UI 标签 · 查看器 |
| --- | --- | --- | --- | --- |
| `porous-model-package` | `compile.model_package` | `artifact`<br>`zip (application/zip)` | `zip (application/zip)` · `FILE_ONLY` | 已编译的模型包 · `metadata` |
| `porous-model-validation` | `compile.validation_report` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 模型校验报告 · `json` |
| `porous-calculation-summary` | `simulate.calculation_summary` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 仿真摘要 · `json` |
| `porous-effective-parameters` | `simulate.effective_parameters` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 有效模型参数 · `json` |
| `porous-result-package` | `simulate.result_package` | `artifact`<br>`zip (application/zip)` | `zip (application/zip)` · `FILE_ONLY` | 水头与浓度结果包 · `metadata` |
| `porous-analysis` | `analyze.analysis_table` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 按时段和层统计的结果 · `json` |
| `porous-figure` | `render.rendered_figure` | `artifact`<br>`png (image/png)` | `png (image/png)` · `FILE_ONLY` | 最终时刻结果图 · `image` |
| `porous-vtk-package` | `render.vtu_pvd_package` | `artifact`<br>`zip (application/zip)` | `zip (application/zip)` · `FILE_ONLY` | VTU 和 PVD 文件包 · `metadata` |
| `porous-compile-receipt` | `compile.run_receipt` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 模型编译运行凭据 · `json` |
| `porous-simulation-receipt` | `simulate.run_receipt` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 仿真运行凭据 · `json` |
| `porous-analysis-receipt` | `analyze.run_receipt` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 分析运行凭据 · `json` |
| `porous-render-receipt` | `render.run_receipt` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 制图运行凭据 · `json` |

### groundwater.render-map@1.1.0

- Workflow 定义摘要 `sha256:5b463cdd0f6636f9b0aafad85df1809a1fdb9ebfea74ef96cb30fc4cfd33bd69`。 [Workflow JSON](../../source/domains/groundwater/6.2.0/workflows/render-map.workflow.json)
- 组合算子成员 —。本图包含未准入的精确版本，动态组合草稿的准入检查返回 `COMPOSITION_OPERATOR_UNSUPPORTED`。
- Model `groundwater.render-map@3.1.0`，摘要 `sha256:6c7ac50adf25407c4ff1f86b7a568d7ba1437b99c239a1e2745592b2dd52498c`。产品入口为预设「静态地图」，Agent 工具为 `render_map`。 [Model JSON](../../source/domains/groundwater/6.2.0/models/render-map.model.json)
- 声明绑定的验证用例 [Pack Golden](../../source/products/groundwater/development/workflow-golden-cases.v2.json) `render-map`，夹具 `data=cartography.points`，重点输出 `map-summary`，[同身份基线](../../source/products/groundwater/development/workflow-goldens/render-map.json)

| 输入角色 | 基数 | 编译后的格式 / 媒体类型 | Model 调用参数 |
| --- | --- | --- | --- |
| `data` | `one` | `geojson (application/geo+json)`<br>`cog (image/tiff)`<br>`geotiff (image/tiff)` | `sourceVersionId` |

| 节点 | 精确 Operator · Runtime | 组合成员 | 端口输入绑定 | 节点参数 ← Workflow 参数 | Manifest Schema 默认声明 |
| --- | --- | --- | --- | --- | --- |
| `render` | `cartography.render-png@1.0.0` · `native` | 否 | `data ← input:data` | `title`<br>`valueField`<br>`palette`<br>`width`<br>`height`<br>`unit` | `title` = `""`（已显式绑定）<br>`valueField` = `""`（已显式绑定）<br>`palette` = `"viridis"`（已显式绑定）<br>`width` = `1024`（已显式绑定）<br>`height` = `768`（已显式绑定）<br>`unit` = `""`（已显式绑定） |

<details><summary>Workflow 参数来源</summary>

| Workflow 参数 | Model 调用参数 | 来源 |
| --- | --- | --- |
| `title` | `title` | Model default `""`，可显式覆盖 |
| `valueField` | `valueField` | Model default `""`，可显式覆盖 |
| `palette` | `palette` | Model default `"viridis"`，可显式覆盖 |
| `width` | `width` | Model default `1024`，可显式覆盖 |
| `height` | `height` | Model default `768`，可显式覆盖 |
| `unit` | `unit` | Model default `""`，可显式覆盖 |

</details>

| 命名输出 | 节点端口 | 编译后的底层合同 | Model 发布声明 | UI 标签 · 查看器 |
| --- | --- | --- | --- | --- |
| `map-image` | `render.image` | `artifact`<br>`png (image/png)` | `png (image/png)` · `FILE_ONLY` | 静态地图 · `image` |
| `map-summary` | `render.summary` | `value`<br>`{"additionalProperties":false,"properties":{"crs":{"minLength":1,"type":"string"},"data":{"type":"object"},"extent":{"additionalProperties":false,"properties":{"maxX":{"type":"number"},"maxY":{"type":"number"},"minX":{"type":"number"},"minY":{"type":"number"}},"required":["minX","minY","maxX","maxY"],"type":"object"},"image":{"additionalProperties":false,"properties":{"height":{"maximum":4096,"minimum":480,"type":"integer"},"width":{"maximum":4096,"minimum":640,"type":"integer"}},"required":["width","height"],"type":"object"},"interpretation":{"minLength":1,"type":"string"},"limitations":{"items":{"minLength":1,"type":"string"},"minItems":1,"type":"array"},"sourceFormat":{"enum":["geojson","geotiff","cog"],"type":"string"},"sourceKind":{"enum":["vector","raster"],"type":"string"},"style":{"type":"object"}},"required":["sourceKind","sourceFormat","crs","extent","image","style","data","interpretation","limitations"],"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 静态地图表达摘要 · `json` |

### groundwater.rule-classification@1.0.0

- Workflow 定义摘要 `sha256:0c60d99586f81af8af942917c67029ddab42bca1e5ca431ac20104745120b551`。 [Workflow JSON](../../source/domains/groundwater/6.2.0/workflows/rule-classification.workflow.json)
- 组合算子成员 —。本图包含未准入的精确版本，动态组合草稿的准入检查返回 `COMPOSITION_OPERATOR_UNSUPPORTED`。
- Model `groundwater.rule-classification@1.0.0`，摘要 `sha256:92e61dc3fe2d540df83d20fa77233d1fda74749f25c8917469e59d19a6659818`。产品入口为预设「规则分类」，Agent 工具为 `run_rule_classification`。 [Model JSON](../../source/domains/groundwater/6.2.0/models/rule-classification.model.json)
- 声明绑定的验证用例 未找到结构化用例声明

| 输入角色 | 基数 | 编译后的格式 / 媒体类型 | Model 调用参数 |
| --- | --- | --- | --- |
| `data` | `one` | `arrow-ipc-file (application/vnd.apache.arrow.file)`<br>`parquet (application/vnd.apache.parquet)`<br>`csv (text/csv)` | `dataVersionId` |
| `rules` | `one` | `json (application/json)` | `rulesVersionId` |
| `bindings` | `one` | `json (application/json)` | `bindingsVersionId` |

| 节点 | 精确 Operator · Runtime | 组合成员 | 端口输入绑定 | 节点参数 ← Workflow 参数 | Manifest Schema 默认声明 |
| --- | --- | --- | --- | --- | --- |
| `classify` | `table.classify@1.0.0` · `native` | 否 | `data ← input:data`<br>`rules ← input:rules`<br>`bindings ← input:bindings` | — | — |

Workflow 参数集合为空。输入文件仍可承载规则、服务或算法配置。

| 命名输出 | 节点端口 | 编译后的底层合同 | Model 发布声明 | UI 标签 · 查看器 |
| --- | --- | --- | --- | --- |
| `classified-table` | `classify.classified` | `artifact`<br>`parquet (application/vnd.apache.parquet)` | `parquet (application/vnd.apache.parquet)` · `FILE_QUERYABLE` | 规则分类结果表 · `table` |
| `indicator-classifications` | `classify.indicatorClassifications` | `artifact`<br>`parquet (application/vnd.apache.parquet)` | `parquet (application/vnd.apache.parquet)` · `FILE_QUERYABLE` | 逐指标分类明细 · `table` |
| `classification-rule-summary` | `classify.ruleSummary` | `artifact`<br>`parquet (application/vnd.apache.parquet)` | `parquet (application/vnd.apache.parquet)` · `FILE_QUERYABLE` | 分类规则审计表 · `table` |
| `classification-summary` | `classify.summary` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 规则分类运行摘要 · `json` |

### groundwater.source-sustainability@1.0.0

- Workflow 定义摘要 `sha256:e750627daac6deb708df78c04e8561567110aed3ce7e03c8225cea40f28c9b98`。 [Workflow JSON](../../source/domains/groundwater/6.2.0/workflows/source-sustainability.workflow.json)
- 组合算子成员 —。本图包含未准入的精确版本，动态组合草稿的准入检查返回 `COMPOSITION_OPERATOR_UNSUPPORTED`。
- Model `groundwater.source-sustainability@1.1.0`，摘要 `sha256:7bcee0cec861ca8c419ab0dd46a044e9e40594acce071e447414369cf206c110`。产品入口为预设「水源地综合评价」，Agent 工具为 `run_source_sustainability`。 [Model JSON](../../source/domains/groundwater/6.2.0/models/source-sustainability.model.json)
- 声明绑定的验证用例 未找到结构化用例声明

| 输入角色 | 基数 | 编译后的格式 / 媒体类型 | Model 调用参数 |
| --- | --- | --- | --- |
| `assessment_input` | `one` | `json (application/json)` | `assessmentVersionId` |

| 节点 | 精确 Operator · Runtime | 组合成员 | 端口输入绑定 | 节点参数 ← Workflow 参数 | Manifest Schema 默认声明 |
| --- | --- | --- | --- | --- | --- |
| `evaluate` | `groundwater.source_sustainability.evaluate@1.0.0` · `remote_http`<br>`groundwater-sustainability/sustainability.evaluate` | 否 | `assessment_input ← input:assessment_input` | — | — |

Workflow 参数集合为空。输入文件仍可承载规则、服务或算法配置。

| 命名输出 | 节点端口 | 编译后的底层合同 | Model 发布声明 | UI 标签 · 查看器 |
| --- | --- | --- | --- | --- |
| `sustainability-result` | `evaluate.sustainability_result` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 外部服务返回的结构化评价 · `json` |
| `sustainability-result-file` | `evaluate.result_file` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 完整性校验后的外部结果文件 · `json` |
| `sustainability-run-receipt` | `evaluate.run_receipt` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 外部执行与完整性凭据 · `metadata` |

### groundwater.summary@1.1.0

- Workflow 定义摘要 `sha256:29e39ac874fd98a6a209552207f086352f15da98aeaa9e80ce3189fea094885a`。 [Workflow JSON](../../source/domains/groundwater/6.2.0/workflows/summary.workflow.json)
- 组合算子成员 —。本图包含未准入的精确版本，动态组合草稿的准入检查返回 `COMPOSITION_OPERATOR_UNSUPPORTED`。
- Model `groundwater.summary@3.1.0`，摘要 `sha256:9b8cab4683deaf5aa0e80b8f5da06a738209daa89ae593ec189fffd3135069b0`。产品入口为预设「数据概览」，Agent 工具为 `run_summary`。 [Model JSON](../../source/domains/groundwater/6.2.0/models/summary.model.json)
- 声明绑定的验证用例 未找到结构化用例声明

| 输入角色 | 基数 | 编译后的格式 / 媒体类型 | Model 调用参数 |
| --- | --- | --- | --- |
| `data` | `one` | `json (application/json)`<br>`arrow-ipc-file (application/vnd.apache.arrow.file)`<br>`parquet (application/vnd.apache.parquet)`<br>`npy (application/x-npy)`<br>`csv (text/csv)` | `versionId` |

| 节点 | 精确 Operator · Runtime | 组合成员 | 端口输入绑定 | 节点参数 ← Workflow 参数 | Manifest Schema 默认声明 |
| --- | --- | --- | --- | --- | --- |
| `describe` | `statistics.describe@1.0.0` · `native` | 否 | `data ← input:data` | `axis`<br>`column`<br>`groupField`<br>`unit` | `axis` = `"columns"`（已显式绑定）<br>`unit` = `""`（已显式绑定） |

<details><summary>Workflow 参数来源</summary>

| Workflow 参数 | Model 调用参数 | 来源 |
| --- | --- | --- |
| `axis` | `axis` | Model default `"columns"`，可显式覆盖 |
| `column` | `column` | 调用方选填或条件必填，见 Model Schema |
| `groupField` | `groupField` | 调用方选填或条件必填，见 Model Schema |
| `unit` | `unit` | Model default `""`，可显式覆盖 |

</details>

| 命名输出 | 节点端口 | 编译后的底层合同 | Model 发布声明 | UI 标签 · 查看器 |
| --- | --- | --- | --- | --- |
| `summary` | `describe.summary` | `value`<br>`{"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 汇总结果 · `json` |
| `summary-details` | `describe.details` | `value`<br>`{"additionalProperties":false,"properties":{"conventions":{"additionalProperties":false,"properties":{"declaredNulls":{"const":"excluded-and-counted"},"nonFiniteValues":{"const":"excluded-and-counted"},"quantiles":{"const":"linear-interpolation"},"stddev":{"const":"population-ddof-0"}},"required":["declaredNulls","nonFiniteValues","stddev","quantiles"],"type":"object"},"interpretation":{"minLength":1,"type":"string"},"limitations":{"items":{"minLength":1,"type":"string"},"minItems":1,"type":"array"},"schemaVersion":{"const":"statistics-describe-details.v1"},"scope":{"additionalProperties":false,"properties":{"basis":{"const":"FULL_STAGED_INPUT"},"inputFormat":{"minLength":1,"type":"string"},"sourceCount":{"minimum":0,"type":"integer"},"sourceCountKind":{"enum":["elements","rows"],"type":"string"}},"required":["basis","inputFormat","sourceCount","sourceCountKind"],"type":"object"},"selection":{"additionalProperties":false,"properties":{"axis":{"enum":["columns","flatten"],"type":"string"},"column":{"type":["string","null"]},"groupField":{"type":["string","null"]},"unit":{"type":["string","null"]}},"required":["axis","column","groupField","unit"],"type":"object"},"series":{"items":{"additionalProperties":false,"properties":{"field":{"additionalProperties":false,"properties":{"kind":{"enum":["array-column","array-values","table-column"],"type":"string"},"name":{"minLength":1,"type":"string"}},"required":["kind","name"],"type":"object"},"groups":{"items":{"additionalProperties":false,"properties":{"key":{"type":"object"},"statistics":{"type":"object"}},"required":["key","statistics"],"type":"object"},"maxItems":100,"type":"array"},"statistics":{"type":"object"}},"required":["field","statistics","groups"],"type":"object"},"minItems":1,"type":"array"}},"required":["schemaVersion","scope","selection","series","conventions","interpretation","limitations"],"type":"object"}` | `json (application/json)` · `FILE_ONLY` | 描述统计范围与语义 · `json` |

### groundwater.sustainability-input-preparation@1.0.0

- Workflow 定义摘要 `sha256:8348828744c309628f406c15f6d522c2e523960cc8786c86ccd7f4ee6eb246b3`。 [Workflow JSON](../../source/domains/groundwater/6.2.0/workflows/sustainability-input-preparation.workflow.json)
- 组合算子成员 —。本图包含未准入的精确版本，动态组合草稿的准入检查返回 `COMPOSITION_OPERATOR_UNSUPPORTED`。
- Model `groundwater.sustainability-input-preparation@1.0.0`，摘要 `sha256:baade9048f5a9e791ea8ec39b58b24b764e4905cfac6ab708982310e9291998a`。产品入口为预设「水源地评价输入准备」，Agent 工具为 `prepare_source_sustainability`。 [Model JSON](../../source/domains/groundwater/6.2.0/models/sustainability-input-preparation.model.json)
- 声明绑定的验证用例 未找到结构化用例声明

| 输入角色 | 基数 | 编译后的格式 / 媒体类型 | Model 调用参数 |
| --- | --- | --- | --- |
| `simulation` | `one` | `json (application/json)` | `simulationEvidenceVersionId` |
| `monitoring` | `one` | `json (application/json)` | `monitoringEvidenceVersionId` |
| `management` | `one` | `json (application/json)` | `managementEvidenceVersionId` |

| 节点 | 精确 Operator · Runtime | 组合成员 | 端口输入绑定 | 节点参数 ← Workflow 参数 | Manifest Schema 默认声明 |
| --- | --- | --- | --- | --- | --- |
| `execute` | `groundwater.source_sustainability.prepare@1.0.0` · `native` | 否 | `simulation ← input:simulation`<br>`monitoring ← input:monitoring`<br>`management ← input:management` | — | — |

Workflow 参数集合为空。输入文件仍可承载规则、服务或算法配置。

| 命名输出 | 节点端口 | 编译后的底层合同 | Model 发布声明 | UI 标签 · 查看器 |
| --- | --- | --- | --- | --- |
| `assessment-input-candidate` | `execute.assessment_input` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 候选评价 JSON 或阻止标记 · `json` |
| `assessment-input-sources` | `execute.field_sources` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 指标来源与缺项诊断 · `json` |

### groundwater.transfer-learning-analysis@1.0.0

- Workflow 定义摘要 `sha256:9660909aae42460bcfe51daf50c881a5ed26a63f04eea8083d96ceaf93bb649c`。 [Workflow JSON](../../source/domains/groundwater/6.2.0/workflows/transfer-learning-analysis.workflow.json)
- 组合算子成员 `crs.reproject@1.0.0`<br>`data.convert@1.0.0`<br>`table.filter@1.0.0`。本图包含未准入的精确版本，动态组合草稿的准入检查返回 `COMPOSITION_OPERATOR_UNSUPPORTED`。
- Model `groundwater.transfer-learning-analysis@3.1.0`，摘要 `sha256:5cea4d238c61564b8c91f227f47db54477b60ab61474ae4d05ff69289b338c3a`。产品入口为预设「迁移学习回测」，Agent 工具为 `run_transfer_learning_analysis`。 [Model JSON](../../source/domains/groundwater/6.2.0/models/transfer-learning-analysis.model.json)
- 声明绑定的验证用例 [Domain Execution Case](../../source/domains/groundwater/6.2.0/cases/yaozhanbao-transfer-case.json) `yaozhanbao-transfer-case`，数据标签 `case:yaozhanbao-transfer-case`，默认 `standard`，选项 `standard, development_fast`

| 输入角色 | 基数 | 编译后的格式 / 媒体类型 | Model 调用参数 |
| --- | --- | --- | --- |
| `workbook` | `one` | `xlsx (application/vnd.openxmlformats-officedocument.spreadsheetml.sheet)` | `workbookVersionId` |
| `monthMapping` | `one` | `json (application/json)` | `monthMappingVersionId` |
| `sourceFilter` | `one` | `json (application/json)` | `sourceFilterVersionId` |
| `targetFilter` | `one` | `json (application/json)` | `targetFilterVersionId` |
| `boundary` | `one` | `geopackage (application/geopackage+sqlite3)` | `boundaryVersionId` |
| `imagery` | `one` | `geotiff (image/tiff)` | `imageryVersionId` |
| `counties` | `one` | `geopackage (application/geopackage+sqlite3)` | `countiesVersionId` |
| `townships` | `one` | `geopackage (application/geopackage+sqlite3)` | `townshipsVersionId` |
| `rivers` | `one` | `geopackage (application/geopackage+sqlite3)` | `riversVersionId` |
| `groundwater_systems` | `one` | `geopackage (application/geopackage+sqlite3)` | `groundwater_systemsVersionId` |
| `railways` | `one` | `geopackage (application/geopackage+sqlite3)` | `railwaysVersionId` |
| `basin_boundary` | `one` | `geopackage (application/geopackage+sqlite3)` | `basin_boundaryVersionId` |
| `source-scaler-rules` | `one` | `json (application/json)` | `source-scaler-rulesVersionId` |
| `target-scaler-rules` | `one` | `json (application/json)` | `target-scaler-rulesVersionId` |
| `source-windows-rules` | `one` | `json (application/json)` | `source-windows-rulesVersionId` |
| `target-windows-rules` | `one` | `json (application/json)` | `target-windows-rulesVersionId` |
| `holdout-windows-rules` | `one` | `json (application/json)` | `holdout-windows-rulesVersionId` |
| `inverse-rules` | `one` | `json (application/json)` | `inverse-rulesVersionId` |
| `imagery-rules` | `one` | `json (application/json)` | `imagery-rulesVersionId` |

| 节点 | 精确 Operator · Runtime | 组合成员 | 端口输入绑定 | 节点参数 ← Workflow 参数 | Manifest Schema 默认声明 |
| --- | --- | --- | --- | --- | --- |
| `extract` | `spreadsheet.extract_table@1.0.0` · `native` | 否 | `workbook ← input:workbook` | `sheetName ← extract.sheetName`<br>`headerRow ← extract.headerRow`<br>`formulaMode ← extract.formulaMode`<br>`maxOutputRows ← extract.maxOutputRows` | — |
| `unpivot` | `table.timeseries_unpivot@1.0.0` · `native` | 否 | `data ← extract.data`<br>`mapping ← input:monthMapping` | `timeField ← unpivot.timeField` | — |
| `source-rows` | `table.filter@1.0.0` · `native` | 是 | `data ← unpivot.data`<br>`rules ← input:sourceFilter` | — | — |
| `source-split` | `timeseries.chronological_split@1.0.0` · `native` | 否 | `data ← source-rows.matched` | `groupField ← source-split.groupField`<br>`timeField ← source-split.timeField`<br>`trainRatio ← source-split.trainRatio`<br>`inputWindow ← source-split.inputWindow` | — |
| `source-scaler` | `preprocessing.standard_scaler_fit@1.0.0` · `native` | 否 | `data ← source-split.fitRows`<br>`rules ← input:source-scaler-rules` | — | — |
| `source-scaled` | `preprocessing.standard_scaler_transform@1.0.0` · `native` | 否 | `data ← source-rows.matched`<br>`scaler ← source-scaler.scaler` | — | — |
| `source-windows` | `timeseries.supervised_windows@1.0.0` · `native` | 否 | `data ← source-scaled.data`<br>`targets ← source-split.trainTargets`<br>`rules ← input:source-windows-rules` | `groupField ← source-windows.groupField`<br>`timeField ← source-windows.timeField`<br>`targetField ← source-windows.targetField`<br>`inputWindow ← source-windows.inputWindow` | — |
| `target-rows` | `table.filter@1.0.0` · `native` | 是 | `data ← unpivot.data`<br>`rules ← input:targetFilter` | — | — |
| `target-split` | `timeseries.chronological_split@1.0.0` · `native` | 否 | `data ← target-rows.matched` | `groupField ← target-split.groupField`<br>`timeField ← target-split.timeField`<br>`trainRatio ← target-split.trainRatio`<br>`inputWindow ← target-split.inputWindow` | — |
| `target-scaler` | `preprocessing.standard_scaler_fit@1.0.0` · `native` | 否 | `data ← target-split.fitRows`<br>`rules ← input:target-scaler-rules` | — | — |
| `target-scaled` | `preprocessing.standard_scaler_transform@1.0.0` · `native` | 否 | `data ← target-rows.matched`<br>`scaler ← target-scaler.scaler` | — | — |
| `target-windows` | `timeseries.supervised_windows@1.0.0` · `native` | 否 | `data ← target-scaled.data`<br>`targets ← target-split.trainTargets`<br>`rules ← input:target-windows-rules` | `groupField ← target-windows.groupField`<br>`timeField ← target-windows.timeField`<br>`targetField ← target-windows.targetField`<br>`inputWindow ← target-windows.inputWindow` | — |
| `holdout-windows` | `timeseries.supervised_windows@1.0.0` · `native` | 否 | `data ← target-scaled.data`<br>`targets ← target-split.holdoutTargets`<br>`rules ← input:holdout-windows-rules` | `groupField ← holdout-windows.groupField`<br>`timeField ← holdout-windows.timeField`<br>`targetField ← holdout-windows.targetField`<br>`inputWindow ← holdout-windows.inputWindow` | — |
| `pretrain` | `ml.gru_attention_pretrain@1.0.0` · `native` | 否 | `features ← source-windows.features`<br>`targets ← source-windows.targets` | `hiddenSize ← pretrain.hiddenSize`<br>`numLayers ← pretrain.numLayers`<br>`numHeads ← pretrain.numHeads`<br>`dropout ← pretrain.dropout`<br>`epochs ← pretrain.epochs`<br>`batchSize ← pretrain.batchSize`<br>`learningRate ← pretrain.learningRate`<br>`seed ← pretrain.seed`<br>`device ← pretrain.device` | — |
| `finetune` | `ml.gru_attention_finetune@1.0.0` · `native` | 否 | `features ← target-windows.features`<br>`targets ← target-windows.targets`<br>`weights ← pretrain.weights`<br>`modelSpec ← pretrain.modelSpec` | `epochs ← finetune.epochs`<br>`batchSize ← finetune.batchSize`<br>`learningRate ← finetune.learningRate`<br>`seed ← finetune.seed`<br>`device ← finetune.device` | — |
| `predict` | `ml.gru_attention_predict@1.0.0` · `native` | 否 | `features ← holdout-windows.features`<br>`sampleIndex ← holdout-windows.sampleIndex`<br>`weights ← finetune.weights`<br>`modelSpec ← finetune.modelSpec` | `device ← predict.device` | — |
| `inverse` | `preprocessing.standard_scaler_inverse@1.0.0` · `native` | 否 | `data ← predict.predictions`<br>`scaler ← target-scaler.scaler`<br>`rules ← input:inverse-rules` | `scalerField ← inverse.scalerField` | — |
| `evaluate` | `statistics.regression_metrics@1.0.0` · `native` | 否 | `predictions ← inverse.data` | `groupField ← evaluate.groupField`<br>`actualField ← evaluate.actualField`<br>`predictedField ← evaluate.predictedField`<br>`baselineField ← evaluate.baselineField`<br>`unit ← evaluate.unit` | — |
| `boundary-projected` | `crs.reproject@1.0.0` · `native` | 是 | `data ← input:boundary` | `layer ← boundary-projected.layer`<br>`targetCrs ← boundary-projected.targetCrs` | — |
| `extent` | `vector.envelope@1.0.0` · `native` | 否 | `vector ← boundary-projected.data` | `layer ← extent.layer`<br>`paddingMeters ← extent.paddingMeters`<br>`aspectRatio ← extent.aspectRatio` | — |
| `imagery` | `raster.window@1.0.0` · `native` | 否 | `raster ← input:imagery`<br>`extent ← extent.extent`<br>`rules ← input:imagery-rules` | `extentLayer ← imagery.extentLayer`<br>`minimumCoverage ← imagery.minimumCoverage` | — |
| `counties-projected` | `crs.reproject@1.0.0` · `native` | 是 | `data ← input:counties` | `layer ← counties-projected.layer`<br>`targetCrs ← counties-projected.targetCrs` | — |
| `counties-clipped` | `vector.clip@1.0.0` · `qgis` | 否 | `vector ← counties-projected.data`<br>`mask ← extent.extent` | `vectorLayer ← counties-clipped.vectorLayer`<br>`maskLayer ← counties-clipped.maskLayer` | — |
| `townships-projected` | `crs.reproject@1.0.0` · `native` | 是 | `data ← input:townships` | `layer ← townships-projected.layer`<br>`targetCrs ← townships-projected.targetCrs` | — |
| `townships-clipped` | `vector.clip@1.0.0` · `qgis` | 否 | `vector ← townships-projected.data`<br>`mask ← extent.extent` | `vectorLayer ← townships-clipped.vectorLayer`<br>`maskLayer ← townships-clipped.maskLayer` | — |
| `rivers-projected` | `crs.reproject@1.0.0` · `native` | 是 | `data ← input:rivers` | `layer ← rivers-projected.layer`<br>`targetCrs ← rivers-projected.targetCrs` | — |
| `rivers-clipped` | `vector.clip@1.0.0` · `qgis` | 否 | `vector ← rivers-projected.data`<br>`mask ← extent.extent` | `vectorLayer ← rivers-clipped.vectorLayer`<br>`maskLayer ← rivers-clipped.maskLayer` | — |
| `groundwater_systems-projected` | `crs.reproject@1.0.0` · `native` | 是 | `data ← input:groundwater_systems` | `layer ← groundwater_systems-projected.layer`<br>`targetCrs ← groundwater_systems-projected.targetCrs` | — |
| `groundwater_systems-clipped` | `vector.clip@1.0.0` · `qgis` | 否 | `vector ← groundwater_systems-projected.data`<br>`mask ← extent.extent` | `vectorLayer ← groundwater_systems-clipped.vectorLayer`<br>`maskLayer ← groundwater_systems-clipped.maskLayer` | — |
| `railways-projected` | `crs.reproject@1.0.0` · `native` | 是 | `data ← input:railways` | `layer ← railways-projected.layer`<br>`targetCrs ← railways-projected.targetCrs` | — |
| `railways-clipped` | `vector.clip@1.0.0` · `qgis` | 否 | `vector ← railways-projected.data`<br>`mask ← extent.extent` | `vectorLayer ← railways-clipped.vectorLayer`<br>`maskLayer ← railways-clipped.maskLayer` | — |
| `basin_boundary-projected` | `crs.reproject@1.0.0` · `native` | 是 | `data ← input:basin_boundary` | `layer ← basin_boundary-projected.layer`<br>`targetCrs ← basin_boundary-projected.targetCrs` | — |
| `basin_boundary-clipped` | `vector.clip@1.0.0` · `qgis` | 否 | `vector ← basin_boundary-projected.data`<br>`mask ← extent.extent` | `vectorLayer ← basin_boundary-clipped.vectorLayer`<br>`maskLayer ← basin_boundary-clipped.maskLayer` | — |
| `prediction-points` | `vector.from_xy@1.0.0` · `native` | 否 | `data ← evaluate.evaluated` | `xField ← prediction-points.xField`<br>`yField ← prediction-points.yField`<br>`sourceCrs ← prediction-points.sourceCrs` | — |
| `prediction-membership` | `vector.tag_membership@1.0.0` · `native` | 否 | `points ← prediction-points.points`<br>`polygons ← input:boundary` | `polygonLayer ← prediction-membership.polygonLayer`<br>`outputField ← prediction-membership.outputField` | — |
| `map-month` | `timeseries.snapshot@1.0.0` · `native` | 否 | `observations ← evaluate.evaluated` | `groupField ← map-month.groupField`<br>`timeField ← map-month.timeField`<br>`valueField ← map-month.valueField`<br>`targetTime ← map-month.targetTime`<br>`selection ← map-month.selection`<br>`maxAgeDays ← map-month.maxAgeDays` | — |
| `map-points` | `vector.from_xy@1.0.0` · `native` | 否 | `data ← map-month.snapshot` | `xField ← map-points.xField`<br>`yField ← map-points.yField`<br>`sourceCrs ← map-points.sourceCrs` | — |
| `map-membership` | `vector.tag_membership@1.0.0` · `native` | 否 | `points ← map-points.points`<br>`polygons ← input:boundary` | `polygonLayer ← map-membership.polygonLayer`<br>`outputField ← map-membership.outputField` | — |
| `prediction-points-wgs84` | `crs.reproject@1.0.0` · `native` | 是 | `data ← prediction-membership.points` | `layer ← prediction-points-wgs84.layer`<br>`targetCrs ← prediction-points-wgs84.targetCrs` | — |
| `prediction-points-geojson` | `data.convert@1.0.0` · `native` | 是 | `data ← prediction-points-wgs84.data` | `layer ← prediction-points-geojson.layer`<br>`outputFormat ← prediction-points-geojson.outputFormat` | — |
| `map-points-wgs84` | `crs.reproject@1.0.0` · `native` | 是 | `data ← map-membership.points` | `layer ← map-points-wgs84.layer`<br>`targetCrs ← map-points-wgs84.targetCrs` | — |
| `map-points-geojson` | `data.convert@1.0.0` · `native` | 是 | `data ← map-points-wgs84.data` | `layer ← map-points-geojson.layer`<br>`outputFormat ← map-points-geojson.outputFormat` | — |
| `boundary-wgs84` | `crs.reproject@1.0.0` · `native` | 是 | `data ← boundary-projected.data` | `layer ← boundary-wgs84.layer`<br>`targetCrs ← boundary-wgs84.targetCrs` | — |
| `boundary-geojson` | `data.convert@1.0.0` · `native` | 是 | `data ← boundary-wgs84.data` | `layer ← boundary-geojson.layer`<br>`outputFormat ← boundary-geojson.outputFormat` | — |
| `counties-wgs84` | `crs.reproject@1.0.0` · `native` | 是 | `data ← counties-clipped.vector` | `layer ← counties-wgs84.layer`<br>`targetCrs ← counties-wgs84.targetCrs` | — |
| `counties-geojson` | `data.convert@1.0.0` · `native` | 是 | `data ← counties-wgs84.data` | `layer ← counties-geojson.layer`<br>`outputFormat ← counties-geojson.outputFormat` | — |
| `townships-wgs84` | `crs.reproject@1.0.0` · `native` | 是 | `data ← townships-clipped.vector` | `layer ← townships-wgs84.layer`<br>`targetCrs ← townships-wgs84.targetCrs` | — |
| `townships-geojson` | `data.convert@1.0.0` · `native` | 是 | `data ← townships-wgs84.data` | `layer ← townships-geojson.layer`<br>`outputFormat ← townships-geojson.outputFormat` | — |
| `rivers-wgs84` | `crs.reproject@1.0.0` · `native` | 是 | `data ← rivers-clipped.vector` | `layer ← rivers-wgs84.layer`<br>`targetCrs ← rivers-wgs84.targetCrs` | — |
| `rivers-geojson` | `data.convert@1.0.0` · `native` | 是 | `data ← rivers-wgs84.data` | `layer ← rivers-geojson.layer`<br>`outputFormat ← rivers-geojson.outputFormat` | — |
| `groundwater_systems-wgs84` | `crs.reproject@1.0.0` · `native` | 是 | `data ← groundwater_systems-clipped.vector` | `layer ← groundwater_systems-wgs84.layer`<br>`targetCrs ← groundwater_systems-wgs84.targetCrs` | — |
| `groundwater_systems-geojson` | `data.convert@1.0.0` · `native` | 是 | `data ← groundwater_systems-wgs84.data` | `layer ← groundwater_systems-geojson.layer`<br>`outputFormat ← groundwater_systems-geojson.outputFormat` | — |
| `railways-wgs84` | `crs.reproject@1.0.0` · `native` | 是 | `data ← railways-clipped.vector` | `layer ← railways-wgs84.layer`<br>`targetCrs ← railways-wgs84.targetCrs` | — |
| `railways-geojson` | `data.convert@1.0.0` · `native` | 是 | `data ← railways-wgs84.data` | `layer ← railways-geojson.layer`<br>`outputFormat ← railways-geojson.outputFormat` | — |
| `basin_boundary-wgs84` | `crs.reproject@1.0.0` · `native` | 是 | `data ← basin_boundary-clipped.vector` | `layer ← basin_boundary-wgs84.layer`<br>`targetCrs ← basin_boundary-wgs84.targetCrs` | — |
| `basin_boundary-geojson` | `data.convert@1.0.0` · `native` | 是 | `data ← basin_boundary-wgs84.data` | `layer ← basin_boundary-geojson.layer`<br>`outputFormat ← basin_boundary-geojson.outputFormat` | — |
| `imagery-wgs84` | `raster.reproject@1.0.0` · `native` | 否 | `raster ← imagery.raster` | `targetCrs ← imagery-wgs84.targetCrs`<br>`resampling ← imagery-wgs84.resampling` | `resampling` = `"nearest"`（已显式绑定） |
| `imagery-cog` | `data.convert@1.0.0` · `native` | 是 | `data ← imagery-wgs84.raster` | `outputFormat ← imagery-cog.outputFormat` | — |

<details><summary>Workflow 参数来源</summary>

| Workflow 参数 | Model 调用参数 | 来源 |
| --- | --- | --- |
| `unpivot.timeField` | `unpivot.timeField` | Model default `"month"`，可显式覆盖 |
| `source-split.groupField` | `source-split.groupField` | Model default `"Well_ID"`，可显式覆盖 |
| `source-split.timeField` | `source-split.timeField` | Model default `"month"`，可显式覆盖 |
| `source-split.trainRatio` | `source-split.trainRatio` | Model default `1.0`，可显式覆盖 |
| `source-split.inputWindow` | `source-split.inputWindow` | Model default `12`，可显式覆盖 |
| `source-windows.groupField` | `source-windows.groupField` | Model default `"Well_ID"`，可显式覆盖 |
| `source-windows.timeField` | `source-windows.timeField` | Model default `"month"`，可显式覆盖 |
| `source-windows.targetField` | `source-windows.targetField` | Model default `"water_depth"`，可显式覆盖 |
| `source-windows.inputWindow` | `source-windows.inputWindow` | Model default `12`，可显式覆盖 |
| `target-split.groupField` | `target-split.groupField` | Model default `"Well_ID"`，可显式覆盖 |
| `target-split.timeField` | `target-split.timeField` | Model default `"month"`，可显式覆盖 |
| `target-split.trainRatio` | `target-split.trainRatio` | Model default `0.8`，可显式覆盖 |
| `target-split.inputWindow` | `target-split.inputWindow` | Model default `12`，可显式覆盖 |
| `target-windows.groupField` | `target-windows.groupField` | Model default `"Well_ID"`，可显式覆盖 |
| `target-windows.timeField` | `target-windows.timeField` | Model default `"month"`，可显式覆盖 |
| `target-windows.targetField` | `target-windows.targetField` | Model default `"water_depth"`，可显式覆盖 |
| `target-windows.inputWindow` | `target-windows.inputWindow` | Model default `12`，可显式覆盖 |
| `holdout-windows.groupField` | `holdout-windows.groupField` | Model default `"Well_ID"`，可显式覆盖 |
| `holdout-windows.timeField` | `holdout-windows.timeField` | Model default `"month"`，可显式覆盖 |
| `holdout-windows.targetField` | `holdout-windows.targetField` | Model default `"water_depth"`，可显式覆盖 |
| `holdout-windows.inputWindow` | `holdout-windows.inputWindow` | Model default `12`，可显式覆盖 |
| `pretrain.hiddenSize` | `pretrain.hiddenSize` | Model default `64`，可显式覆盖 |
| `pretrain.numLayers` | `pretrain.numLayers` | Model default `2`，可显式覆盖 |
| `pretrain.numHeads` | `pretrain.numHeads` | Model default `4`，可显式覆盖 |
| `pretrain.dropout` | `pretrain.dropout` | Model default `0.2`，可显式覆盖 |
| `pretrain.epochs` | `pretrain.epochs` | Model default `80`，可显式覆盖 |
| `pretrain.batchSize` | `pretrain.batchSize` | Model default `16`，可显式覆盖 |
| `pretrain.learningRate` | `pretrain.learningRate` | Model default `0.001`，可显式覆盖 |
| `pretrain.seed` | `pretrain.seed` | Model default `42`，可显式覆盖 |
| `pretrain.device` | `pretrain.device` | Model default `"cpu"`，可显式覆盖 |
| `finetune.epochs` | `finetune.epochs` | Model default `100`，可显式覆盖 |
| `finetune.batchSize` | `finetune.batchSize` | Model default `16`，可显式覆盖 |
| `finetune.learningRate` | `finetune.learningRate` | Model default `0.0005`，可显式覆盖 |
| `finetune.seed` | `finetune.seed` | Model default `43`，可显式覆盖 |
| `finetune.device` | `finetune.device` | Model default `"cpu"`，可显式覆盖 |
| `predict.device` | `predict.device` | Model default `"cpu"`，可显式覆盖 |
| `inverse.scalerField` | `inverse.scalerField` | Model default `"water_depth"`，可显式覆盖 |
| `evaluate.groupField` | `evaluate.groupField` | Model default `"Well_ID"`，可显式覆盖 |
| `evaluate.actualField` | `evaluate.actualField` | Model default `"actual"`，可显式覆盖 |
| `evaluate.predictedField` | `evaluate.predictedField` | Model default `"predicted"`，可显式覆盖 |
| `evaluate.baselineField` | `evaluate.baselineField` | Model default `"persistence"`，可显式覆盖 |
| `evaluate.unit` | `evaluate.unit` | Model default `"m"`，可显式覆盖 |
| `boundary-projected.layer` | `boundary-projected.layer` | Model default `"water_source_boundaries"`，可显式覆盖 |
| `boundary-projected.targetCrs` | `boundary-projected.targetCrs` | Model default `"EPSG:32650"`，可显式覆盖 |
| `extent.layer` | `extent.layer` | Model default `"water_source_boundaries"`，可显式覆盖 |
| `extent.paddingMeters` | `extent.paddingMeters` | Model default `1000`，可显式覆盖 |
| `extent.aspectRatio` | `extent.aspectRatio` | Model default `1.6`，可显式覆盖 |
| `imagery.extentLayer` | `imagery.extentLayer` | Model default `"extent"`，可显式覆盖 |
| `imagery.minimumCoverage` | `imagery.minimumCoverage` | Model default `0.98`，可显式覆盖 |
| `counties-projected.layer` | `counties-projected.layer` | Model default `"GIS0065__县界"`，可显式覆盖 |
| `counties-projected.targetCrs` | `counties-projected.targetCrs` | Model default `"EPSG:32650"`，可显式覆盖 |
| `counties-clipped.vectorLayer` | `counties-clipped.vectorLayer` | Model default `"GIS0065__县界"`，可显式覆盖 |
| `counties-clipped.maskLayer` | `counties-clipped.maskLayer` | Model default `"extent"`，可显式覆盖 |
| `townships-projected.layer` | `townships-projected.layer` | Model default `"GIS0064__乡镇界"`，可显式覆盖 |
| `townships-projected.targetCrs` | `townships-projected.targetCrs` | Model default `"EPSG:32650"`，可显式覆盖 |
| `townships-clipped.vectorLayer` | `townships-clipped.vectorLayer` | Model default `"GIS0064__乡镇界"`，可显式覆盖 |
| `townships-clipped.maskLayer` | `townships-clipped.maskLayer` | Model default `"extent"`，可显式覆盖 |
| `rivers-projected.layer` | `rivers-projected.layer` | Model default `"GIS0066__盆地水系"`，可显式覆盖 |
| `rivers-projected.targetCrs` | `rivers-projected.targetCrs` | Model default `"EPSG:32650"`，可显式覆盖 |
| `rivers-clipped.vectorLayer` | `rivers-clipped.vectorLayer` | Model default `"GIS0066__盆地水系"`，可显式覆盖 |
| `rivers-clipped.maskLayer` | `rivers-clipped.maskLayer` | Model default `"extent"`，可显式覆盖 |
| `groundwater_systems-projected.layer` | `groundwater_systems-projected.layer` | Model default `"GIS0068__盆地地下水系统细分"`，可显式覆盖 |
| `groundwater_systems-projected.targetCrs` | `groundwater_systems-projected.targetCrs` | Model default `"EPSG:32650"`，可显式覆盖 |
| `groundwater_systems-clipped.vectorLayer` | `groundwater_systems-clipped.vectorLayer` | Model default `"GIS0068__盆地地下水系统细分"`，可显式覆盖 |
| `groundwater_systems-clipped.maskLayer` | `groundwater_systems-clipped.maskLayer` | Model default `"extent"`，可显式覆盖 |
| `railways-projected.layer` | `railways-projected.layer` | Model default `"GIS0074__铁路"`，可显式覆盖 |
| `railways-projected.targetCrs` | `railways-projected.targetCrs` | Model default `"EPSG:32650"`，可显式覆盖 |
| `railways-clipped.vectorLayer` | `railways-clipped.vectorLayer` | Model default `"GIS0074__铁路"`，可显式覆盖 |
| `railways-clipped.maskLayer` | `railways-clipped.maskLayer` | Model default `"extent"`，可显式覆盖 |
| `basin_boundary-projected.layer` | `basin_boundary-projected.layer` | Model default `"GIS0059__张家口盆地范围"`，可显式覆盖 |
| `basin_boundary-projected.targetCrs` | `basin_boundary-projected.targetCrs` | Model default `"EPSG:32650"`，可显式覆盖 |
| `basin_boundary-clipped.vectorLayer` | `basin_boundary-clipped.vectorLayer` | Model default `"GIS0059__张家口盆地范围"`，可显式覆盖 |
| `basin_boundary-clipped.maskLayer` | `basin_boundary-clipped.maskLayer` | Model default `"extent"`，可显式覆盖 |
| `prediction-points.xField` | `prediction-points.xField` | Model default `"Lon"`，可显式覆盖 |
| `prediction-points.yField` | `prediction-points.yField` | Model default `"Lat"`，可显式覆盖 |
| `prediction-points.sourceCrs` | `prediction-points.sourceCrs` | Model default `"EPSG:4490"`，可显式覆盖 |
| `prediction-membership.polygonLayer` | `prediction-membership.polygonLayer` | Model default `"water_source_boundaries"`，可显式覆盖 |
| `prediction-membership.outputField` | `prediction-membership.outputField` | Model default `"inside_protection"`，可显式覆盖 |
| `map-month.groupField` | `map-month.groupField` | Model default `"Well_ID"`，可显式覆盖 |
| `map-month.timeField` | `map-month.timeField` | Model default `"month"`，可显式覆盖 |
| `map-month.valueField` | `map-month.valueField` | Model default `"predicted"`，可显式覆盖 |
| `map-month.targetTime` | `map-month.targetTime` | Model default `"2024-12-01"`，可显式覆盖 |
| `map-month.selection` | `map-month.selection` | Model default `"exact"`，可显式覆盖 |
| `map-month.maxAgeDays` | `map-month.maxAgeDays` | Model default `0`，可显式覆盖 |
| `map-points.xField` | `map-points.xField` | Model default `"Lon"`，可显式覆盖 |
| `map-points.yField` | `map-points.yField` | Model default `"Lat"`，可显式覆盖 |
| `map-points.sourceCrs` | `map-points.sourceCrs` | Model default `"EPSG:4490"`，可显式覆盖 |
| `map-membership.polygonLayer` | `map-membership.polygonLayer` | Model default `"water_source_boundaries"`，可显式覆盖 |
| `map-membership.outputField` | `map-membership.outputField` | Model default `"inside_protection"`，可显式覆盖 |
| `extract.sheetName` | `extract.sheetName` | Model default `"Sheet1"`，可显式覆盖 |
| `extract.headerRow` | `extract.headerRow` | Model default `1`，可显式覆盖 |
| `extract.formulaMode` | `extract.formulaMode` | Model default `"cachedValue"`，可显式覆盖 |
| `extract.maxOutputRows` | `extract.maxOutputRows` | Model default `10000`，可显式覆盖 |
| `prediction-points-wgs84.layer` | `prediction-points-wgs84.layer` | Model default `"points"`，可显式覆盖 |
| `prediction-points-wgs84.targetCrs` | `prediction-points-wgs84.targetCrs` | Model default `"EPSG:4326"`，可显式覆盖 |
| `prediction-points-geojson.layer` | `prediction-points-geojson.layer` | Model default `"points"`，可显式覆盖 |
| `prediction-points-geojson.outputFormat` | `prediction-points-geojson.outputFormat` | Model default `"geojson"`，可显式覆盖 |
| `map-points-wgs84.layer` | `map-points-wgs84.layer` | Model default `"points"`，可显式覆盖 |
| `map-points-wgs84.targetCrs` | `map-points-wgs84.targetCrs` | Model default `"EPSG:4326"`，可显式覆盖 |
| `map-points-geojson.layer` | `map-points-geojson.layer` | Model default `"points"`，可显式覆盖 |
| `map-points-geojson.outputFormat` | `map-points-geojson.outputFormat` | Model default `"geojson"`，可显式覆盖 |
| `boundary-wgs84.layer` | `boundary-wgs84.layer` | Model default `"water_source_boundaries"`，可显式覆盖 |
| `boundary-wgs84.targetCrs` | `boundary-wgs84.targetCrs` | Model default `"EPSG:4326"`，可显式覆盖 |
| `boundary-geojson.layer` | `boundary-geojson.layer` | Model default `"water_source_boundaries"`，可显式覆盖 |
| `boundary-geojson.outputFormat` | `boundary-geojson.outputFormat` | Model default `"geojson"`，可显式覆盖 |
| `counties-wgs84.layer` | `counties-wgs84.layer` | Model default `"clipped"`，可显式覆盖 |
| `counties-wgs84.targetCrs` | `counties-wgs84.targetCrs` | Model default `"EPSG:4326"`，可显式覆盖 |
| `counties-geojson.layer` | `counties-geojson.layer` | Model default `"clipped"`，可显式覆盖 |
| `counties-geojson.outputFormat` | `counties-geojson.outputFormat` | Model default `"geojson"`，可显式覆盖 |
| `townships-wgs84.layer` | `townships-wgs84.layer` | Model default `"clipped"`，可显式覆盖 |
| `townships-wgs84.targetCrs` | `townships-wgs84.targetCrs` | Model default `"EPSG:4326"`，可显式覆盖 |
| `townships-geojson.layer` | `townships-geojson.layer` | Model default `"clipped"`，可显式覆盖 |
| `townships-geojson.outputFormat` | `townships-geojson.outputFormat` | Model default `"geojson"`，可显式覆盖 |
| `rivers-wgs84.layer` | `rivers-wgs84.layer` | Model default `"clipped"`，可显式覆盖 |
| `rivers-wgs84.targetCrs` | `rivers-wgs84.targetCrs` | Model default `"EPSG:4326"`，可显式覆盖 |
| `rivers-geojson.layer` | `rivers-geojson.layer` | Model default `"clipped"`，可显式覆盖 |
| `rivers-geojson.outputFormat` | `rivers-geojson.outputFormat` | Model default `"geojson"`，可显式覆盖 |
| `groundwater_systems-wgs84.layer` | `groundwater_systems-wgs84.layer` | Model default `"clipped"`，可显式覆盖 |
| `groundwater_systems-wgs84.targetCrs` | `groundwater_systems-wgs84.targetCrs` | Model default `"EPSG:4326"`，可显式覆盖 |
| `groundwater_systems-geojson.layer` | `groundwater_systems-geojson.layer` | Model default `"clipped"`，可显式覆盖 |
| `groundwater_systems-geojson.outputFormat` | `groundwater_systems-geojson.outputFormat` | Model default `"geojson"`，可显式覆盖 |
| `railways-wgs84.layer` | `railways-wgs84.layer` | Model default `"clipped"`，可显式覆盖 |
| `railways-wgs84.targetCrs` | `railways-wgs84.targetCrs` | Model default `"EPSG:4326"`，可显式覆盖 |
| `railways-geojson.layer` | `railways-geojson.layer` | Model default `"clipped"`，可显式覆盖 |
| `railways-geojson.outputFormat` | `railways-geojson.outputFormat` | Model default `"geojson"`，可显式覆盖 |
| `basin_boundary-wgs84.layer` | `basin_boundary-wgs84.layer` | Model default `"clipped"`，可显式覆盖 |
| `basin_boundary-wgs84.targetCrs` | `basin_boundary-wgs84.targetCrs` | Model default `"EPSG:4326"`，可显式覆盖 |
| `basin_boundary-geojson.layer` | `basin_boundary-geojson.layer` | Model default `"clipped"`，可显式覆盖 |
| `basin_boundary-geojson.outputFormat` | `basin_boundary-geojson.outputFormat` | Model default `"geojson"`，可显式覆盖 |
| `imagery-cog.outputFormat` | `imagery-cog.outputFormat` | Model default `"cog"`，可显式覆盖 |
| `imagery-wgs84.targetCrs` | `imagery-wgs84.targetCrs` | Model default `"EPSG:4326"`，可显式覆盖 |
| `imagery-wgs84.resampling` | `imagery-wgs84.resampling` | Model default `"bilinear"`，可显式覆盖 |

</details>

| 命名输出 | 节点端口 | 编译后的底层合同 | Model 发布声明 | UI 标签 · 查看器 |
| --- | --- | --- | --- | --- |
| `source-scaler` | `source-scaler.scaler` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 源域缩放器 · `json` |
| `target-scaler` | `target-scaler.scaler` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 目标域缩放器 · `json` |
| `target-split` | `target-split.split` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 目标域时间划分诊断 · `table` |
| `pretrained-weights` | `pretrain.weights` | `artifact`<br>`pytorch-state-dict (application/vnd.pytorch)` | `pytorch-state-dict (application/vnd.pytorch)` · `FILE_ONLY` | 预训练权重 · `metadata` |
| `pretrained-model-spec` | `pretrain.modelSpec` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 预训练模型规格 · `json` |
| `pretrain-loss` | `pretrain.loss` | `artifact`<br>`parquet (application/vnd.apache.parquet)` | `parquet (application/vnd.apache.parquet)` · `FILE_QUERYABLE` | 预训练损失 · `chart` |
| `finetuned-weights` | `finetune.weights` | `artifact`<br>`pytorch-state-dict (application/vnd.pytorch)` | `pytorch-state-dict (application/vnd.pytorch)` · `FILE_ONLY` | 微调权重 · `metadata` |
| `finetuned-model-spec` | `finetune.modelSpec` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 微调模型规格 · `json` |
| `finetune-loss` | `finetune.loss` | `artifact`<br>`parquet (application/vnd.apache.parquet)` | `parquet (application/vnd.apache.parquet)` · `FILE_QUERYABLE` | 微调损失 · `chart` |
| `predictions` | `evaluate.evaluated` | `artifact`<br>`parquet (application/vnd.apache.parquet)` | `parquet (application/vnd.apache.parquet)` · `FILE_QUERYABLE` | 单步留出回测结果 · `chart` |
| `metrics` | `evaluate.overall` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 留出回测总体指标 · `table` |
| `per-well-metrics` | `evaluate.perGroup` | `artifact`<br>`parquet (application/vnd.apache.parquet)` | `parquet (application/vnd.apache.parquet)` · `FILE_QUERYABLE` | 逐井留出回测指标 · `table` |
| `prediction-points` | `prediction-points-geojson.data` | `artifact`<br>`flatgeobuf (application/flatgeobuf)`<br>`geojson (application/geo+json)`<br>`geopackage (application/geopackage+sqlite3)`<br>`json (application/json)`<br>`arrow-ipc-file (application/vnd.apache.arrow.file)`<br>`geoparquet (application/vnd.apache.parquet)`<br>`parquet (application/vnd.apache.parquet)`<br>`las (application/vnd.las)`<br>`copc (application/vnd.laz)`<br>`laz (application/vnd.laz)`<br>`cog (image/tiff)`<br>`geotiff (image/tiff)`<br>`csv (text/csv)` | `geojson (application/geo+json)` · `FILE_ONLY` | 预测点资料 · `map` |
| `map-points` | `map-points-geojson.data` | `artifact`<br>`flatgeobuf (application/flatgeobuf)`<br>`geojson (application/geo+json)`<br>`geopackage (application/geopackage+sqlite3)`<br>`json (application/json)`<br>`arrow-ipc-file (application/vnd.apache.arrow.file)`<br>`geoparquet (application/vnd.apache.parquet)`<br>`parquet (application/vnd.apache.parquet)`<br>`las (application/vnd.las)`<br>`copc (application/vnd.laz)`<br>`laz (application/vnd.laz)`<br>`cog (image/tiff)`<br>`geotiff (image/tiff)`<br>`csv (text/csv)` | `geojson (application/geo+json)` · `FILE_ONLY` | 制图点资料 · `map` |
| `boundary` | `boundary-geojson.data` | `artifact`<br>`flatgeobuf (application/flatgeobuf)`<br>`geojson (application/geo+json)`<br>`geopackage (application/geopackage+sqlite3)`<br>`json (application/json)`<br>`arrow-ipc-file (application/vnd.apache.arrow.file)`<br>`geoparquet (application/vnd.apache.parquet)`<br>`parquet (application/vnd.apache.parquet)`<br>`las (application/vnd.las)`<br>`copc (application/vnd.laz)`<br>`laz (application/vnd.laz)`<br>`cog (image/tiff)`<br>`geotiff (image/tiff)`<br>`csv (text/csv)` | `geojson (application/geo+json)` · `FILE_ONLY` | 区域边界 · `map` |
| `extent` | `extent.extent` | `artifact`<br>`geopackage (application/geopackage+sqlite3)` | `geopackage (application/geopackage+sqlite3)` · `FILE_ONLY` | 分析范围 · `map` |
| `imagery` | `imagery-cog.data` | `artifact`<br>`flatgeobuf (application/flatgeobuf)`<br>`geojson (application/geo+json)`<br>`geopackage (application/geopackage+sqlite3)`<br>`json (application/json)`<br>`arrow-ipc-file (application/vnd.apache.arrow.file)`<br>`geoparquet (application/vnd.apache.parquet)`<br>`parquet (application/vnd.apache.parquet)`<br>`las (application/vnd.las)`<br>`copc (application/vnd.laz)`<br>`laz (application/vnd.laz)`<br>`cog (image/tiff)`<br>`geotiff (image/tiff)`<br>`csv (text/csv)` | `cog (image/tiff)` · `FILE_ONLY` | 影像 · `map` |
| `imagery-coverage` | `imagery.coverage` | `artifact`<br>`json (application/json)` | `json (application/json)` · `FILE_ONLY` | 影像窗口覆盖诊断 · `table` |
| `map-month-diagnostics` | `map-month.diagnostics` | `artifact`<br>`parquet (application/vnd.apache.parquet)` | `parquet (application/vnd.apache.parquet)` · `FILE_QUERYABLE` | 制图月份选择诊断 · `table` |
| `counties` | `counties-geojson.data` | `artifact`<br>`flatgeobuf (application/flatgeobuf)`<br>`geojson (application/geo+json)`<br>`geopackage (application/geopackage+sqlite3)`<br>`json (application/json)`<br>`arrow-ipc-file (application/vnd.apache.arrow.file)`<br>`geoparquet (application/vnd.apache.parquet)`<br>`parquet (application/vnd.apache.parquet)`<br>`las (application/vnd.las)`<br>`copc (application/vnd.laz)`<br>`laz (application/vnd.laz)`<br>`cog (image/tiff)`<br>`geotiff (image/tiff)`<br>`csv (text/csv)` | `geojson (application/geo+json)` · `FILE_ONLY` | 县级区划 · `map` |
| `townships` | `townships-geojson.data` | `artifact`<br>`flatgeobuf (application/flatgeobuf)`<br>`geojson (application/geo+json)`<br>`geopackage (application/geopackage+sqlite3)`<br>`json (application/json)`<br>`arrow-ipc-file (application/vnd.apache.arrow.file)`<br>`geoparquet (application/vnd.apache.parquet)`<br>`parquet (application/vnd.apache.parquet)`<br>`las (application/vnd.las)`<br>`copc (application/vnd.laz)`<br>`laz (application/vnd.laz)`<br>`cog (image/tiff)`<br>`geotiff (image/tiff)`<br>`csv (text/csv)` | `geojson (application/geo+json)` · `FILE_ONLY` | 乡镇区划 · `map` |
| `rivers` | `rivers-geojson.data` | `artifact`<br>`flatgeobuf (application/flatgeobuf)`<br>`geojson (application/geo+json)`<br>`geopackage (application/geopackage+sqlite3)`<br>`json (application/json)`<br>`arrow-ipc-file (application/vnd.apache.arrow.file)`<br>`geoparquet (application/vnd.apache.parquet)`<br>`parquet (application/vnd.apache.parquet)`<br>`las (application/vnd.las)`<br>`copc (application/vnd.laz)`<br>`laz (application/vnd.laz)`<br>`cog (image/tiff)`<br>`geotiff (image/tiff)`<br>`csv (text/csv)` | `geojson (application/geo+json)` · `FILE_ONLY` | 河流 · `map` |
| `groundwater_systems` | `groundwater_systems-geojson.data` | `artifact`<br>`flatgeobuf (application/flatgeobuf)`<br>`geojson (application/geo+json)`<br>`geopackage (application/geopackage+sqlite3)`<br>`json (application/json)`<br>`arrow-ipc-file (application/vnd.apache.arrow.file)`<br>`geoparquet (application/vnd.apache.parquet)`<br>`parquet (application/vnd.apache.parquet)`<br>`las (application/vnd.las)`<br>`copc (application/vnd.laz)`<br>`laz (application/vnd.laz)`<br>`cog (image/tiff)`<br>`geotiff (image/tiff)`<br>`csv (text/csv)` | `geojson (application/geo+json)` · `FILE_ONLY` | 地下水系统 · `map` |
| `railways` | `railways-geojson.data` | `artifact`<br>`flatgeobuf (application/flatgeobuf)`<br>`geojson (application/geo+json)`<br>`geopackage (application/geopackage+sqlite3)`<br>`json (application/json)`<br>`arrow-ipc-file (application/vnd.apache.arrow.file)`<br>`geoparquet (application/vnd.apache.parquet)`<br>`parquet (application/vnd.apache.parquet)`<br>`las (application/vnd.las)`<br>`copc (application/vnd.laz)`<br>`laz (application/vnd.laz)`<br>`cog (image/tiff)`<br>`geotiff (image/tiff)`<br>`csv (text/csv)` | `geojson (application/geo+json)` · `FILE_ONLY` | 铁路 · `map` |
| `basin_boundary` | `basin_boundary-geojson.data` | `artifact`<br>`flatgeobuf (application/flatgeobuf)`<br>`geojson (application/geo+json)`<br>`geopackage (application/geopackage+sqlite3)`<br>`json (application/json)`<br>`arrow-ipc-file (application/vnd.apache.arrow.file)`<br>`geoparquet (application/vnd.apache.parquet)`<br>`parquet (application/vnd.apache.parquet)`<br>`las (application/vnd.las)`<br>`copc (application/vnd.laz)`<br>`laz (application/vnd.laz)`<br>`cog (image/tiff)`<br>`geotiff (image/tiff)`<br>`csv (text/csv)` | `geojson (application/geo+json)` · `FILE_ONLY` | 流域边界 · `map` |
<!-- END GENERATED WORKFLOWS -->
