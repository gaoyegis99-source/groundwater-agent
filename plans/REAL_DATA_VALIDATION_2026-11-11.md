# 地下水真实数据案例验证（2026-11-11）

## 结论

状态：**PASS**。

已选择 3 个互补数据组并完成 4 个真实生产工作流，覆盖监测时序、地下水流场、空间插值和综合脆弱性评价。输入均按固定 SHA-256 验证；生产算子在本地 Linux/amd64 Native 镜像中以断网、只读、禁止拉取方式运行。开发准备代码、下载缓存和运行产物均与默认 Native/QGIS/root runtime 分离。

## 案例矩阵

| 数据组 | 案例 | 真实数据用途 | 结果 |
| --- | --- | --- | --- |
| 本地 groundwater v2 candidate | `local-monitoring-trend` | 2018–2025 月水位高程趋势、完整率、突变、异常和分期变化 | PASS；114 口井、9,943 条记录、10 个生产节点 |
| USGS MRVA spring 2024 | `usgs-mrva-flow-field` | 实测水头快照、IDW 水头面、水力梯度、流向和情景速度 | PASS；1,147 个唯一流场坐标、1,794 个有效栅格单元 |
| USGS MRVA spring 2024 | `usgs-mrva-kriging` | 普通克里金、方差、留一法和官方水头栅格外部对照 | PASS；1,148 点、2,025 个预测单元；外部 RMSE 1.002 m |
| ODNR Franklin County | `odnr-franklin-drastic` | 七因子 DRASTIC 相对脆弱性指数与分类 | PASS；14,700 个有效单元，与发布 `GVIndex` 零差异 |

案例声明位于 `source/products/groundwater/development/real-data-cases.v1.json`，SHA-256 为 `432c3e65d1b83d696e90216b322b09d676d047228b33f2b14331b2732396cc19`。

## 数据组与质量边界

### 1. 本地 groundwater v2 candidate

- 根目录由调用者显式传入，本次为 `/home/mtd/liweigis/workflow`；案例清单不签入机器绝对路径。
- `monthly_observation.parquet`：3,576,897 bytes，SHA-256 `85329d1f26866865ca4f7fa8fb47aecfe204e45624cd7ba6ee98796c1eb3505d`。
- 发布清单状态为 `candidate_verified`，不是 active pointer。月表粒度为 station × month × metric；目标粒度无重复。
- 使用 `water_level_elevation_monthly_mean`（m），只保留 2018–2021 和 2022–2025 两个时段各至少 24 个观测的井。25,511 条候选记录中保留 114 口井、9,943 条记录。
- 本地站点坐标仅少量存在且 CRS 未核实，因此本组只用于时序分析，不用于物理流场或全国空间插值。
- 不跨井设置绝对水位阈值，阈值事件分支明确为 `NOT_REQUESTED`。

### 2. USGS Mississippi River Valley alluvial aquifer，spring 2024

- 权威入口：[USGS 数据发布 DOI 10.5066/P1FGVGED](https://doi.org/10.5066/P1FGVGED)，许可 CC0-1.0。
- 测点 ZIP：150,872 bytes，MD5 `26510e03fb9997c34ed3646006689906`，SHA-256 `e67be1c8771e9ea522d1d8ff1b4193eb6f4ebbb35f3d618de555b4ca043d2183`。
- 官方水头栅格 ZIP：410,044 bytes，MD5 `7dc061cecf3cf321e2f967919fbb2a6e`，SHA-256 `abab209de58d0a161ac1ac2c210e0b72822d7d5cb81c8b43c42630b7131da358`。
- 1,194 个发布测点中使用 USGS 标记有效的 1,150 个；EPSG:5070 转换后有 1,148 个唯一坐标，两个重复坐标按水头均值聚合。`ALT_88 - D2W_2024 = WLA88_2024` 的最大残差为 0.0047 ft。
- 一口井的埋深为 −0.27 ft，代表承压自流状态。当前 `head.calculate` 生产算子拒绝负埋深，因此流场案例审计式排除这一口井；克里金案例仍按发布水头保留它。
- 流向和水力梯度来自实测水头面；导水速度使用 `K=10 m/day`、有效孔隙率 `0.25`，属于情景输出，不是校准后的实测地下水速度。
- 克里金使用 10 km 快速网格和固定 spherical variogram（range 250 km、sill 400 m²、nugget 0）。留一法 RMSE 为 1.478 m；与 USGS 1 km 官方栅格在 931 个重叠单元上的 RMSE 为 1.002 m、MAE 0.616 m、bias −0.039 m。外部对照只设覆盖门槛，未用官方栅格反向调参。

### 3. ODNR Franklin County DRASTIC

- 权威入口：[Ohio DNR Groundwater Vulnerability Map](https://ohiodnr.gov/discover-and-learn/safety-conservation/about-ODNR/geologic-survey/groundwater-resources/groundwater-vulnerability-map)。
- 官方 ZIP：1,657,140 bytes，MD5 `ecd1bdb62f7b2313656cc8c3e0518339`，SHA-256 `332c07050ef2f7bff7722f9030cb254bc3b6455bd53f8e9b8d1ecf9c279da623`。
- 786 个多边形，EPSG:3735。763 条记录具有完整的七因子评分；所有完整记录都满足 ODNR 发布的标准权重公式 `5D + 4R + 3A + 2S + T + 5I + 3C = GVIndex`。
- 七个评分字段在同一 1,000 US survey ft 网格上栅格化，零评分按未覆盖处理。计算指数与独立栅格化的发布 `GVIndex` 比较 14,700 个有效单元，错配 0，最大绝对差 0。
- ODNR 没有为该发布提供离散官方类别。案例使用发布多边形记录的四分位断点 107.5、127、142 仅作开发展示，并在配置和摘要中标为 `DERIVED_NOT_ODNR_OFFICIAL`。
- DRASTIC 表示相对固有脆弱性，不是污染概率，也不包含污染源、浓度、运移时间、暴露或后果。

## GEMS-GER 附件判断

附件数据与当前 [Zenodo GEMS-GER 发布](https://doi.org/10.5281/zenodo.23733524) 的文件大小和 MD5 一致，3,207 口井的周序列、静态属性和预测表结构完整。它适合时序、机器学习和 Pastas 风格压力—响应实验；由于坐标舍入、可能的高程基准差异、站点筛选偏差以及数据发布许可/上游提供方条款边界，本轮不把它作为全国物理流场、DRASTIC 或 MF6 完整输入。方法和限制另见 [ESSD 论文](https://doi.org/10.5194/essd-18-77-2026)。它保留为后续时序泛化验证数据，而不是本轮三个主数据组之一。

## 复现命令

在 `source/` 目录执行：

```bash
../bin/dev real-case \
  --case local-monitoring-trend \
  --source-root /home/mtd/liweigis/workflow \
  --runtime-image source-groundwater-executor-native-worker:latest

../bin/dev real-case \
  --case usgs-mrva-flow-field \
  --source-root /home/mtd/liweigis/workflow \
  --runtime-image source-groundwater-executor-native-worker:latest

../bin/dev real-case \
  --case usgs-mrva-kriging \
  --source-root /home/mtd/liweigis/workflow \
  --runtime-image source-groundwater-executor-native-worker:latest

../bin/dev real-case \
  --case odnr-franklin-drastic \
  --source-root /home/mtd/liweigis/workflow \
  --runtime-image source-groundwater-executor-native-worker:latest
```

`--case all` 可一次运行全部案例。网络文件只允许来自清单中的固定 HTTPS 官方 URL；已缓存文件仍重新验证 size、MD5（如有）和 SHA-256。原始下载位于 `$XDG_CACHE_HOME/groundwater-v6/real-cases/sources/`，运行结果位于同级 `runs/`，均不签入仓库。

## 运行证据

| 案例 | 运行目录 | 摘要 SHA-256 | 大小 |
| --- | --- | --- | ---: |
| 本地趋势 | `/home/mtd/.cache/groundwater-v6/real-cases/runs/df2adbbb362d4f3ca009d98e851fa7e6` | `756b94fa72cac7b8db811869212642bfe9f0b0242212cc4905daf7a621d44768` | 1,120,018 B |
| MRVA 流场 | `/home/mtd/.cache/groundwater-v6/real-cases/runs/4bcbb71e953142dabe1b88557e15f7e0` | `903a57576ed705c39662179e4c02497e44d73a904680204052af80f6748196c3` | 3,834,201 B |
| MRVA 克里金 | `/home/mtd/.cache/groundwater-v6/real-cases/runs/c935de70ffdc471fb0c90c8e0513315f` | `08ed69809cdadf1607ad7402e3620085e6ff5c65f8bea01163a694ee7a6c06af` | 3,444,714 B |
| Franklin DRASTIC | `/home/mtd/.cache/groundwater-v6/real-cases/runs/8531d9bfb239454faa5d9e22ca457d03` | `1e7b77aa972a1daa696ffcb925fa74255a4a70538be8f9ff9999d972919b2e06` | 2,721,132 B |

四次运行共 11,120,065 bytes。当前 Native 镜像为 Linux/amd64、5,367,459,549 bytes；每个生产任务限制为 2 CPU、8 GiB，准备任务限制为 2 CPU、2 GiB。案例产物很小，适合放入 Docker；镜像应共享，不应为每个模型复制。运行时校验了镜像 ID、架构、GDAL 3.8.4 和项目 rootless daemon，默认运行时变更为 `NONE`。

本轮只完成目标范围内的真实案例构建与科学/数据边界核验。完整全项目回归按用户顺序在目标完成后单独执行，不能由这些案例 PASS 替代。
