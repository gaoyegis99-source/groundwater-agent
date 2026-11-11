# 14 Operator Catalog：先复用，再新增

## 1. 当前目录事实
`source/packages/operator-pack/catalog/index.json` 包含 106 个算子。目录 entry 不是业务模型；必须继续读取每个 operator 的 `manifest.json`、implementation、smoke、fixtures 和 runtime。

## 2. 现有可复用能力映射

| 业务需求 | 已有 operator | 建议 |
|---|---|---|
| 数据画像/质量 | table.profile, table.validate | 组合成 QA workflow |
| 重复记录 | table.deduplicate | 先确认主键/重复语义 |
| 缺测段 | timeseries.gap_runs, timeseries.completeness | 分别输出 gap episodes 与总体完整性 |
| 趋势 | statistics.linear_trend, mann_kendall, seasonal_mann_kendall | 现有 trend 增强 |
| 突变 | statistics.pettitt_change_point, timeseries.period_change | 明确检验对象和时间定义 |
| 异常值 | statistics.mad_outliers | 报告 threshold 是算法默认，不是标准 |
| 事件 | timeseries.threshold_events | 阈值必须来自用户或有出处规则 |
| 降深 | groundwater.head.calculate, drawdown.calculate | 核对水位高程/埋深语义 |
| 水力梯度/速度 | groundwater.hydraulic_gradient, darcy_velocity | 核对 CRS/K/unit |
| 等值线 | points.interpolate.idw, raster.contour | 先对齐 grid/CRS |
| 保护区缓冲 | vector.from_xy, crs.reproject, vector.buffer, vector.dissolve | 用户输入半径与来源 |
| DRASTIC 基础 | raster.reclassify, raster.algebra, raster.cell_statistics | 需固定 7 因子输入领域算子 |
| 空间分类/统计 | raster.zonal_statistics, vector.spatial_join | 按输入格式与 runtime 组合 |

## 3. 新增前的强制核对
每个拟新增算子回答：
1. 是否已有相同或近似算子？
2. 是否可用已有 operator workflow 组合？
3. 是否需要新增规则表，而非新算法？
4. 当前 runtime 是 native/qgis/remote_http 哪个？
5. 输入格式、CRS、单位和 nodata 条件是什么？
6. 是否能写独立 oracle？
7. 是否会触发公共 catalog / implementation revision 的广泛变化？

## 4. Workflow Compiler 约束
当前 `graph.py` 明确拒绝 cardinality != one。因此：
- 不得在 workflow DSL 里声明 many port 并期望通过。
- 不得假设 raster.algebra 的 rasters many 输入可被 compiler 接受。
- 不得用 value output 连接 artifact input。
- operator inputs binding 必须完整。
- workflow inputs 也必须 cardinality one。

DRASTIC 当前推荐固定 7 个输入端口的 domain operator，而不是先改通用 compiler。
