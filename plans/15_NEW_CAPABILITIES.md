# 15 真正缺失的分析能力：Kriging、分类、DRASTIC、多图层

## NEW-01 Ordinary/Universal Kriging（P2）
现有只有 `points.interpolate.idw`。新算子建议 `points.interpolate.kriging@1.0.0`，但实施前先核实 GSTools / PyKrige 的版本、license、Python ABI 与现有 Native runtime。
参数：value field、x/y、source CRS、target CRS、grid resolution、extent、kriging type、variogram model、neighbor settings、exact。
输出：prediction raster、variance raster、variogram diagnostics、LOO table、summary。

验收：
- 平面解析场检验 Universal Kriging drift。
- 固定 seed 随机场检验复现性。
- exact 模式井点回代误差。
- variance 非负及空间行为。
- LOO ME/RMSE/standardized residual/RMSSE。
- 与 IDW 同网格对照；不强制所有真实数据 Kriging 必须优于 IDW。

## NEW-02 table.classify（P2）
目前 table operators 有 validate/profile/join/filter 等，没有通用规则表分类算子。
规则必须作为独立输入数据，包含 indicator、unit、basis、bounds、class order、source、verification status。
需要处理闭/开区间、缺测、边界相等、超范围、规则未核实。
pH 双侧区间若规则 schema 无法表达，不能伪造近似规则。
输出逐指标分类、综合类别、未评价原因和规则摘要。

## NEW-03 DRASTIC（P2）
采用固定七端口领域算子，例如 `groundwater.drastic.evaluate`，避免 compiler many port 限制。
输入：
- D
- R
- A
- S
- T
- I
- C
- rules/config artifact

步骤：
1. 七层 CRS、shape、transform、resolution 对齐检查。
2. 只有用户明确选择才执行重采样/重投影。
3. rating rules 与 weights 独立配置并记录来源。
4. nodata 传播规则明确。
5. 输出指数栅格、分类栅格、summary。
6. 报告各因子权重、规则版本、未覆盖像元比例。
7. 明确 DRASTIC 是相对脆弱性指数，不是污染概率。

验收：七层恒定 rating 的手算结果、随机栅格逐像元 numpy oracle、nodata 和 grid mismatch 负例。

## NEW-04 多图层地图（P2）
先完善现有静态 render_map，再新增地图工作区：
- raster + contour + well points + protection zone
- visibility / opacity / z-order
- per-layer legend / unit / source
- CRS transformation
- offline package behavior
地图显示范围和颜色不能被当作数值算法结果。
