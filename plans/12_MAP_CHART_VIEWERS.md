# 12 地图、栅格与图表体验

## VIEW-01 GeoViewer 地图生命周期（P0）
文件：`source/web/src/viewers/SpatialViewers.tsx`。
当前 effect 依赖 `[url, colorField]`，每次字段切换都会重新建 VectorSource、VectorLayer、Map。
目标：Map/Source 与 style state 解耦。
步骤：
1. Map 只在 url/资源身份变化时创建。
2. colorField 变化只更新 layer style。
3. source ready 后缓存 numeric field list 和 feature value range。
4. 不重复下载 GeoJSON。
5. style 更新使用最新 colorField，不使用过期 closure。
6. Map unmount 时 dispose target/listeners/source。

验收：切换着色字段不重新请求 URL；地图实例数量不增加；图例与样式一致。

## VIEW-02 分级图例（P1）
当前 GeoViewer 用 min/max 连续 ratio 映射 red/blue 色阶。
完善为可解释的分级模式：
- continuous / quantile / equal interval
- 显示字段名、单位、分级断点
- null/NaN 单独表示
- 常量字段降级显示
- 统计范围说明全量或当前加载 features

## VIEW-03 RasterViewer 物理值探针（P0）
当前 `GeoTIFF({ sources: [{url}], normalize: true })`，探针将 `layer.getData()` 结果转 Uint8Array。
目标：
1. 查 OpenLayers GeoTIFF source 的 normalize/renderer semantics。
2. 保留显示归一化与数值探针的独立路径。
3. 从原始 band / raster window 获取 pixel value。
4. 处理 nodata、scale/offset、band index、单位。
5. 显示 pixel coordinate、map coordinate、value、unit。
6. 不把 canvas RGBA 当原始栅格值。

验收：已知 GeoTIFF fixture 中选定像元返回原始物理值；nodata 明确显示；渲染色带变化不改变 probe value。

## VIEW-04 多图层地图（P2）
先盘点当前地图支持 GeoJSON 单层、GeoTIFF 单层的组件结构。目标是同一工作区叠加栅格、等值线、井点、保护区，并支持 visibility/opacity/order/legend。投影转换失败单层报错，不使整图空白。

## VIEW-05 ChartView 数据语义（P1）
文件：`source/web/src/viewers/ChartView.tsx`。
现状：最多 query 1000 rows，最多显示 8 groups，xAxis 固定 category。
完善：
1. 若 x field 是时间，按时间升序排序并使用 time axis（若数据解析可信）。
2. 若 x field 为 numeric，使用 value axis。
3. 字段单位进入轴标题和 tooltip。
4. null values 作为 gap，不用 0 代替。
5. 多井 group 支持筛选与聚焦。
6. 显示 query truncated、total groups、rows read。
7. 大数据量先聚合或采样，明确说明口径。
8. ECharts instance 复用并正确 dispose。

验收：时间序列乱序 fixture 输出按时间排序；单位显示；缺测不连成假曲线；超过 1000 行显示截断提示。

## VIEW-06 结果联动（P2）
从 ResultCard 点击空间结果进入 Viewer；从表格选择字段更新图表/地图；从地图 feature 选择显示属性。交互状态不能改变原始 DataVersion。
