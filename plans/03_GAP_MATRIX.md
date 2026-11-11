# 03 功能差距矩阵与优先级

状态：
- **Confirmed**：本次静态读取源码已确认实现形态或缺口。
- **Needs runtime repro**：源码有风险，但需本地测试确认用户可见影响。
- **Proposal**：目标功能尚未实现，需先确认接口与依赖。

| ID | 模块 | 当前实现/证据 | 缺口 | 优先级 |
|---|---|---|---|---|
| UI-01 | ConfirmationEditor | schema 驱动字段，Data resource dropdown | 资源加载无 catch；JSON 编辑错误静默 | P0 |
| UI-02 | ConfirmationEditor | 基于冻结 action arguments 生成 diff | 原值文案需确认，object/array 编辑反馈不足 | P1 |
| UI-03 | session stream | SSE + snapshot + dirty refresh | results 合并可能保留旧 remoteOutputs | P0 |
| UI-04 | session stream | draftCache 避免重复读取 | COMPLETE/FAILED 草稿不会再刷新 | P0 |
| UI-05 | GeoViewer | OpenLayers VectorSource + Map | colorField 变化重建 Map/Source | P0 |
| UI-06 | RasterViewer | GeoTIFF + WebGLTileLayer | normalize 与 Uint8Array 探针不能代表物理值 | P0 |
| UI-07 | ChartView | ECharts line/bar/scatter | 1000 行/8 组限制；x 轴固定 category；排序/单位不足 | P1 |
| AG-01 | Preparation | exact version、selection policy、字段元数据存在性 | 不逐个检查 field parameter 的存在与类型 | P0 |
| AG-02 | Reply resume | MiniMax completed run 有 presentation-only 分支 | 分支调用案例 finalizer，需拆通用终结路径 | P0 |
| AG-03 | Workflow draft | compiler preflight + retry | 顺序检测用正则，合法 fan-out 可能误拒 | P0 |
| WF-01 | WorkflowDraftCard | 节点列表、缺项、输出、证据、JSON export | 无可视化 DAG、缺项表单、局部修订体验 | P1 |
| WF-02 | Workflow execution | Executor node status 与 Kernel progress | 需要验证任务工作台是否完整展示节点/错误/恢复 | P1 |
| DATA-01 | Data | discovery、version、semantics、query、preview、quality、MLOps | 主会话参数选择与 data descriptor 的衔接 | P1 |
| DATA-02 | Data | descriptor / table inspection | field profile 的完整性、抽样口径和单位呈现 | P1 |
| KNOW-01 | Knowledge | retrieval/read evidence/lifecycle | 用户引用回原文、上下文和准确定位体验 | P1 |
| RES-01 | Results | output links、download、attach/continue/compare hooks | 各格式预览和比较体验需完整串联 | P1 |
| GW-01 | Trend | 已有 6 节点监测趋势 DAG | Pettitt / Seasonal MK 已有算子但未加入当前 trend workflow | P1 |
| GW-02 | Flow field | groundwater head/gradient/velocity operator 已有 | 业务输入语义、单位、适用假设和结果解释需完善 | P1 |
| GW-03 | Coverage | 已有 monitoring-coverage model | 凸包仅是几何覆盖，不是含水层边界 | P1 |
| GW-04 | Render map | 当前静态 PNG 单图输出 | 多图层/交互地图尚未形成领域能力 | P2 |
| OP-01 | Kriging | Catalog 有 IDW，无 Kriging entry | 新增 Kriging 实现和数值验收 | P2 |
| OP-02 | Classification | table operators 有 validate/profile/join 等 | 没有通用规则表分类算子 | P2 |
| OP-03 | DRASTIC | raster reclassify/algebra/cell_statistics 已有 | 7 输入端口领域算子与 nodata/grid alignment | P2 |
| MOD-01 | Pastas | 当前未在本次 domain model 清单中看到 | 独立模型原型 | P3 |
| MOD-02 | MF6 | 有 porous remote operations，但不等于原生 MF6 | FloPy/MF6 native modeling path | P3 |

## 先后关系

P0 任务先完成：UI-01、UI-03、UI-04、UI-05、UI-06、AG-01、AG-02、AG-03。
再做 Workflow 草稿、Data/Knowledge、任务工作台。
最后才开始新增算法和进阶数值模型。
