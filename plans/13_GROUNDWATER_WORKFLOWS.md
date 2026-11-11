# 13 现有 Groundwater 工作流逐项完善

## 当前 7 个模型
来自 `source/domains/groundwater/6.0.0/domain-manifest.json`：
- `groundwater.monitoring-trend`
- `groundwater.flow-field`
- `groundwater.transfer-learning-analysis`
- `groundwater.summary`
- `groundwater.monitoring-coverage`
- `groundwater.render-map`
- `groundwater.source-sustainability`

对应 workflows/models 文件名相同。执行前读 model schema 和 workflow JSON，不凭 UI 文案猜参数。

## GW-01 monitoring-trend（P1）
当前 workflow 6 节点：
- timeseries.aggregate
- timeseries.completeness
- statistics.linear_trend
- statistics.mann_kendall
- statistics.mad_outliers
- timeseries.period_change

已安装但当前 workflow 未使用：
- statistics.pettitt_change_point
- statistics.seasonal_mann_kendall
- timeseries.gap_runs
- timeseries.threshold_events

改进：
1. 增加突变检测和季节趋势结果前，明确时间频率、季节周期、缺测要求。
2. gap_runs 与 completeness 输出区分“连续缺测段”与“总体完整率”。
3. threshold_events 的阈值由用户提供，不用任意默认值冒充规范阈值。
4. 输出 schema 增加 summary 的解释字段和 limitations。
5. 为每个新增节点写独立 synthetic case。
6. 不破坏旧模型的固定案例与已有 output names，除非用户明确批准合同变化。

## GW-02 flow-field（P1）
先读：
- `models/flow-field.model.json`
- `workflows/flow-field.workflow.json`
- groundwater.head.calculate / hydraulic_gradient / darcy_velocity manifests and implementations
重点核对：
- water level elevation vs depth-to-water
- reference elevation basis
- K 单位与 gradient CRS
- 输出速度方向与单位
- 简化 Darcy 假设
地图结果不能表述为真实地下水流速观测。

## GW-03 monitoring-coverage（P1）
当前凸包结果表示井点几何覆盖范围。完善：
- 少于 3 个不共线点处理
- 重复点处理
- CRS 与投影说明
- hull 面积/周长摘要
- 明确不是含水层边界或地下水影响范围

## GW-04 render-map（P1）
当前模型的 final output 是静态 PNG，domain instructions 明确只支持单文件静态地图，不支持多图层叠加、PDF、交互地图、在线底图。
完善现有 PNG：
- title / valueField / palette / width / height
- CRS 与数据范围
- colorbar / unit / nodata
- 输入格式错误提示
先补足静态 PNG 的科学表达，再做多图层交互地图专项。

## GW-05 summary（P1）
读 summary workflow/model 和实际 summary operator chain，确认数值摘要、空值、分组和字段语义。UI 应解释统计范围和单位，不把 sample 当 full dataset。

## GW-06 transfer-learning-analysis（P1）
该模型是单步留出回测，不是递归未来预测。保持训练/验证时间切分、scaler fit 仅使用训练集。R²/null 原因保留。输出 metrics、target split、imagery coverage、map-month diagnostics 等具名结果应分别核对。

## GW-07 source-sustainability（P2）
它通过 remote service。需明确该能力依赖外部服务，不可把远程黑盒结果描述成平台内置、已复现的科学算法。先完善输入说明、结果 provenance、错误与限制展示，不在本轮重构 remote auth/deployment。
