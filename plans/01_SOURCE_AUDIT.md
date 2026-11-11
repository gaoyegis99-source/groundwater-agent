# 01 源码审计：当前 main 的可核实事实

## 1. 审计边界

本审计基于 GitHub `main` 的递归 tree 和下列源文件读取，基线 commit：
`3354fc2d959219f399ed667b993dcbdf3d0db89e`。

已读取主要文件：
- `source/AGENTS.md`
- `source/README.md`
- `source/docs/architecture/architecture-overview.md`
- `source/docs/architecture/architecture-structure-and-flow.md`
- `source/docs/capabilities/webui.md`
- `source/docs/operations/execution-playbook.md`
- `source/domains/groundwater/6.0.0/domain-manifest.json`
- `source/domains/groundwater/6.0.0/instructions.md`
- `source/products/groundwater/product-manifest.json`
- `source/web/src/ConfirmationEditor.tsx`
- `source/web/src/use-session-stream.ts`
- `source/web/src/viewers/SpatialViewers.tsx`
- `source/web/src/viewers/ChartView.tsx`
- `source/web/src/WorkflowDraftCard.tsx`
- `source/web/src/TaskCard.tsx`
- `source/kernel/src/platform_kernel/agent_runtime/preparation_tool.py`
- `source/kernel/src/platform_kernel/agent_runtime/run_resume.py`
- `source/kernel/src/platform_kernel/agent_runtime/tooling/reply_submission.py`
- `source/packages/workflow-compiler/src/platform_workflow_compiler/graph.py`
- `source/packages/operator-pack/catalog/index.json`
- `source/packages/operator-pack/README.md`
- `source/domains/groundwater/6.0.0/models/monitoring-trend.model.json`
- `source/domains/groundwater/6.0.0/workflows/monitoring-trend.workflow.json`

本次是源码静态审查，没有在 GitHub 上执行测试，也没有运行完整 Compose。因此以下“已确认”表示源码逻辑可读确认，不表示运行环境已复现。

## 2. 仓库规模与目录

递归 tree 返回 `truncated=false`，共 1,746 个 blob 记录。统计：
- `source/web/src/`：229 个文件
- `source/kernel/src/platform_kernel/`：179 个文件
- `source/services/`：283 个文件
- Kernel tests 与 Web 测试样例清单：72 个文件记录
- Groundwater 6.0.0 workflows：7
- Groundwater 6.0.0 models：7

注意：这是 Git tree blob 数量，不是源代码行数，也不是所有测试的总数。

## 3. 当前版本与声明差异

`source/products/groundwater/product-manifest.json` 声明：
- `product-manifest.v8`
- Public API `v12`
- Domain package `groundwater@6.0.0`
- checksum `sha256:577ce573b7e67fad7367323bce87967c713c44c63d243467aaf47137fc7c53e6`
- projectId `groundwater-demo`

`source/domains/groundwater/6.0.0/domain-manifest.json` 实际声明 7 workflows、7 models、7 knowledge documents 和 1 execution case。

但 `source/domains/groundwater/6.0.0/instructions.md` 中仍有“六类已安装能力”的文字；`source/docs/architecture/architecture-structure-and-flow.md` 的代码位置表也写“六个模型”。这是文档与清单事实不一致，修复前必须检查是否还有其他引用与计数。

## 4. Operator Catalog

`source/packages/operator-pack/catalog/index.json` 的 `operator-catalog-index.v1` 当前包含 106 个 operator entries。运行时分布有 native、qgis、remote_http 三类。

已在目录中看到的关键能力：
- groundwater：`groundwater.head.calculate`、`groundwater.drawdown.calculate`、`groundwater.darcy_velocity`、`groundwater.hydraulic_gradient`
- interpolation：`points.interpolate.idw`
- statistics：`statistics.linear_trend`、`statistics.mann_kendall`、`statistics.pettitt_change_point`、`statistics.seasonal_mann_kendall`、`statistics.mad_outliers`
- timeseries：`timeseries.aggregate`、`completeness`、`gap_runs`、`period_change`、`threshold_events` 等
- raster：`algebra`、`cell_statistics`、`reclassify`、`contour`、`zonal_statistics`、`resample`、`reproject` 等
- vector：`buffer`、`convex_hull`、`dissolve`、`from_xy`、`spatial_join` 等
- table：`validate`、`profile`、`join`、`deduplicate`、`filter` 等

算子存在不代表领域模型已使用它，也不代表其输出和参数适合某个业务任务。必须核实 manifest、实现、smoke 和调用。

## 5. Workflow Compiler 的明确限制

`source/packages/workflow-compiler/src/platform_workflow_compiler/graph.py`：
- Workflow v6。
- workflow input cardinality 必须为 `one`。
- operator input cardinality 必须为 `one`。
- 节点 inputs 的 key 必须与 operator inputs 完全相同。
- 只能绑定 workflowInput 或 nodeOutput。
- 下游 nodeOutput 必须是 artifact。
- DAG 通过拓扑排序检测 cycle。
- 参数绑定必须是 workflowParameter，且满足 operator schema required。
因此不能假定 many 输入端口或常量绑定已经可用。

## 6. Groundwater 当前七个 workflow

实际文件：
1. `flow-field.workflow.json`
2. `monitoring-coverage.workflow.json`
3. `monitoring-trend.workflow.json`
4. `render-map.workflow.json`
5. `source-sustainability.workflow.json`
6. `summary.workflow.json`
7. `transfer-learning-analysis.workflow.json`

模型与其一一对应。每个模型必须从 `models/*.model.json` 读取真实 inputSchema、defaultParameters、inputBindings、parameterBindings、finalOutputs 和 outputPublications。

## 7. 当前明确的前端缺陷

### 7.1 ConfirmationEditor
`source/web/src/ConfirmationEditor.tsx`：
- 加载数据集后 Promise chain 没有 catch。
- 对 object/array 字段，textarea JSON.parse 失败时 catch 内注释为 retain last valid object；用户看不到错误状态。
- 资源选择器加载 `/data/datasets?limit=50` 后逐个读取 currentVersion，规模较大时会产生多请求；需要先测量再优化。
- 原值 diff 当前按 `confirmation.actions[].arguments` 对比，数值输入旁的“原值”显示当前 `value`，需要验证它是否应显示冻结值。不要误删已有 diff 区域。

### 7.2 use-session-stream
`source/web/src/use-session-stream.ts`：
- `draftCache` 的 draftIds 筛选条件是缓存不存在或缓存状态为 GENERATING；已缓存 COMPLETE/FAILED 草稿不再主动重新读取。
- 结果合并时，已有 run 若 `remoteOutputs.length > 0`，新的 `allResults()` 结果不会替换它。重试后旧输出可能保留。
- SSE 有 progress、details dirty、snapshot refresh 和重连恢复机制；修复时必须保留 event id 与 snapshot reconciliation 语义。

### 7.3 SpatialViewers
`source/web/src/viewers/SpatialViewers.tsx`：
- GeoViewer effect 依赖 `[url, colorField]`，effect 内创建 VectorSource、VectorLayer、Map；切换 colorField 会销毁并重建 map/source。
- `source.once('change')` 回调闭包读取 colorField；在初始化和切换时可能存在状态时序问题。
- RasterViewer 创建 `GeoTIFF({ sources: [{url}], normalize: true })`。
- RasterViewer 用 `layer.getData(event.pixel)`，再转 `Uint8Array` 显示，输出是渲染采样数据而非保证为物理栅格值。
- opacity effect 直接改 canvas.style.opacity，未确认是否覆盖所有 canvas/tile 渲染情况。

### 7.4 ChartView
`source/web/src/viewers/ChartView.tsx`：
- 数值字段用于 Y 轴候选。
- 查询分页调用 `runDataQuery(..., 0, 1000, ...)`。
- 最多展示 8 组，超出时提示截断。
- ECharts xAxis 固定为 category。
- x/y 轴没有把字段单位和数据语义呈现为完整标签。
- 组内 points 未显式排序；时间序列图可能依赖服务端查询顺序。
- 切换图表类型使用 line/bar/scatter。
因此图表优化应优先处理排序、单位、时间轴、截断与空值语义，而不是先增加更多图表类型。

## 8. 当前 Agent / Workflow 缺口

### 8.1 Preparation
`preparation_tool.py` 在每个 data input 上读取精确 version，核对 version identity，并按 `CURRENT_REQUIRED` 检查 currentVersion。
字段相关 checks 仅检查 `descriptor.fields` 和 `semantics.fields` 是否非空，状态为 PASS/UNKNOWN；没有看到针对模型 field parameter 的逐字段存在性/类型验证逻辑。

### 8.2 Sequential draft
`reply_submission.py` 的 `_sequential_draft_issue()` 使用简单文本正则 `先.+(?:再|然后)|\b(?:first|then|after)\b` 判断是否为顺序请求；随后计算 edge_count、sinks，并要求 edge_count 至少 node_count-1 且 sink 数为 1。
这对明确线性链有帮助，但会把自然语言中含“after”或合法 fan-out 的 DAG 误当作必须单 sink 的线性链。必须通过语义结构与显式顺序约束替代简单正则。

### 8.3 MiniMax terminal branch
`run_resume.py` 对非案例状态执行完成且 provider 为 minimax 的分支调用 `finalize_kernel_case(deps, run)`。该函数名称与案例终结逻辑耦合，内部生成案例化终态内容。需进一步查看函数完整实现与测试，修复时拆分通用完成投影与 synthetic case 专属免责声明，不得改变正常确认/恢复状态机。

### 8.4 WorkflowDraftCard
`WorkflowDraftCard.tsx` 当前展示：
- 草稿状态
- title/summary
- nodes 逐步列表
- missingInputs/unresolvedItems
- output list
- dataBindings
- parameterSources
- knowledgeRefs
- limitations
- COMPLETE 时 JSON export

它不是 DAG 编辑器，也没有直接的补齐表单或运行按钮。该边界与 WebUI 文档“草稿只导出、不安装、不执行”的原则一致。新增编辑体验必须保持 draft 不产生执行授权。

## 9. 当前 Data / Knowledge 能力不可忽略

Data 不只是上传目录。其源码包含 catalog discovery、version、semantics、preview、query、publication、quality、ML Ops、openlineage/outbox 等功能。
Knowledge Next 包含 retrieval、read evidence、lifecycle、import preflight、retrieval receipt 等功能。
前端也有独立 Data Web、Knowledge UI 和主会话中的 Knowledge drawer。

因此本计划不应重新开发 Data Lake 或 RAG 平台；应重点打通这些现有能力与主会话工作流。

## 10. 未完成的审计

本次没有运行：
- `../bin/dev check --scope ...`
- `npm test` / `npm run typecheck` / `npm run build`
- `python -m pytest`
- Native operator tests
- Playwright E2E
- `test_full_project`

所有测试状态在本次审计中均为 NOT_RUN。AI 助手执行任务时必须在实际本地 checkout 运行适用测试。
