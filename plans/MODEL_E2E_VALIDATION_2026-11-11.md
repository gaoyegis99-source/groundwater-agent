# 地下水 6.1 模型端到端验收（2026-11-11）

状态为 **PASS**。18 个模型均有当前 6.1.0 产品的端到端通过证据。模型范围以当前装配的 18 个 executable model 为准。此前六个模型的隔离生命周期证据保存在 [工作流与任务专项报告](WORKFLOW_TASK_VALIDATION_2026-11-11.md)，本轮补齐其余十二个，并复验真实数据。

| 模型范围 | 测试数据与运行方式 | 当前结果 |
| --- | --- | --- |
| 汇总、覆盖、趋势、流场、迁移学习、制图 | 6.1 隔离 Compose，固定 Provider，生产 Worker | 上轮 6/6 PASS。趋势和流场另使用真实输入补验通过 |
| 克里金、DRASTIC | USGS/ODNR 公开数据的可追溯衍生输入 | 生产算子链与 6.1 Gateway 验收均 PASS |
| 规则分类 | Operator Pack 中有独立类别预期的固定夹具 | 6.1 Gateway 验收 PASS，LOW 和 HIGH 各 1 条，1 条空值未评价 |
| 水源地综合评价、评价输入准备 | 本机 HTTPS 评价服务与 Native 准备算子，三份受治理合成夹具 | 两个模型 PASS。有效候选经独立确认完成真实评价，时段冲突按预期阻断 |
| 孔隙型地下水七个模型 | 本机实际 Linux 服务与求解器，独立测试算例 | 七个模型 PASS。PEST 重启恢复、远端取消和真实地图补测通过 |

证据位于 `/home/mtd/.local/state/groundwater-v6/validation/model-e2e-20261111/`。使用此前验收的固定 Provider 路由，以及实际部署的数值服务。输入、工作流身份和发布产物都按各自证据核对。

## 真实数据重新验证

三组已授权来源重新通过尺寸和 SHA-256 核对，并运行四个生产工作流。当前 6.1 的这四份工作流定义与真实案例入口所引用的 6.0 定义逐字节一致。使用本地已有 Native 镜像与离线准备环境，产生新运行目录 `4076824ab978469d8bdb7e4e56dffe92`，共核对 11 份准备后输入。

| 案例 | 结果 |
| --- | --- |
| 本地趋势 | 114 井、9,943 条记录，通过 |
| USGS MRVA 流场 | 通过。水力传导系数和有效孔隙率来自案例情景设定 |
| USGS MRVA 克里金 | 通过。与官方栅格重叠 931 单元，RMSE 1.002402 m |
| ODNR Franklin DRASTIC | 通过。14,700 个有效单元与发布指数一致，离散类别是案例派生口径 |

原始证据见 `real-data/case-index.json`、`real-data/run.log` 和运行目录内的案例摘要。6.1 API 验收已复用这些经校验的输入。确认前未创建 WorkflowRun 或成果。执行后逐项核对冻结参数与定义、发布血缘、下载校验、会话恢复和重点科学输出。

## 两个真实远程服务

孔隙型地下水 1.1.1 通过 HTTPS 运行流与运移、PEST 率定、情景对比、地图准备、反演训练、反演采样及农业反演。合成矩形算例的水头和浓度各保留 8 个时刻，每时刻 1,500 个网格单元。240 个 AFC 步未出现负浓度，成对通量的最大相对质量残差为 4.15×10⁻¹⁶。PEST 重启后继续原远端任务并只发布一次。远端训练取消后确认终止，发布数为零。七条流程各自的数值和成果见 [孔隙模型报告](/home/mtd/.local/state/groundwater-v6/validation/model-e2e-20261111/porous/REPORT.md)。

同一地图模型另使用 USGS MRVA 2024 春季官方水头面完成实数验收。368 × 931 栅格共 94,568 个有效像元，下载成果与源像元和掩膜逐项一致。投影、仿射、NoData、单位、比例和偏移均保留。源投影保留 Albers_NHG WKT，未解析出 EPSG 编号。该官方栅格属于真实观测衍生产品。 [真实栅格证据](/home/mtd/.local/state/groundwater-v6/validation/model-e2e-20261111/porous/mrva-map.json)

评价输入准备和真实 HTTPS 综合评价 0.7.0 均通过。两条链路分别使用独立三源夹具，以及真实孔隙求解器生成的模拟摘要与合成监测和管理证据。独立合成夹具得到 96.0625 分、等级 I，三个成果和运行凭据的摘要相符。时段冲突返回 BLOCKED。腰站堡后续复核发现，原表的 41 行包含单位和检出限两行，实际为 39 个候选井号。原 15 项及水质点分母均待证，重新运行输入准备后仍为 BLOCKED，未产生场地评分。逐项取证记录保存在本机。 [综合评价工程验收](/home/mtd/.local/state/groundwater-v6/validation/model-e2e-20261111/sustainability/SUMMARY.md)

## 测试中发现的问题

| 复现 | 修复及证据 |
| --- | --- |
| 孔隙验收写入只读占位凭据失败，附加测试路径有中文时配置覆盖失败 | 在隔离容器启动前替换占位文件并保留 0444，使用正确的路径编码。真实服务通过，配置回归 20 项通过 |
| 趋势与流场使用列名声明字段语义，Data 返回 UNKNOWN_FIELD，第一次修订也误读物理字段 ID | 先发布并读取公共 descriptor，再按 fieldId 和 expectedSemanticId 追加语义修订。保留前两次运行日志 |
| 克里金栅格字节不同，像元误差最大 1.91×10⁻¹⁰ m。DRASTIC 使用 NaN NoData，直接相等比较误报网格不同 | 下载仍按发布对象 SHA-256 校验。科学对照校验 CRS、网格、数据类型、NoData、掩膜和有效像元，DRASTIC 要求指数逐单元完全一致 |

修复集中于验收入口和夹具。复验使用当前数值模型和锁定运行环境。

## 最终结果和覆盖核对

新增 Native 验收最终退出码为 0，运行 202.948 秒。四个真实数据案例与规则分类对照均通过。趋势聚合摘要与独立算子运行完全一致。流场水头面 2,000 个有效像元差值为零。克里金 2,025 个有效像元的最大差值为 1.91×10⁻¹⁰ m。DRASTIC 14,700 个指数像元差值为零。规则分类按固定规则得到 LOW 和 HIGH 各一行，并保留一行空值为未评价。该验收的两次失败记录和最后通过记录均已留存。 [最终 Native 结果](/home/mtd/.local/state/groundwater-v6/validation/model-e2e-20261111/native/attempt-3/native-real-lifecycle.json)

| 产品模型 | 验收次数 | 发布成果数量（按验收顺序） | 状态 |
| --- | ---: | --- | --- |
| `groundwater.drastic` | 1 | 3 | PASS |
| `groundwater.flow-field` | 2 | 8, 8 | PASS |
| `groundwater.kriging` | 1 | 5 | PASS |
| `groundwater.monitoring-coverage` | 1 | 2 | PASS |
| `groundwater.monitoring-trend` | 2 | 21, 21 | PASS |
| `groundwater.porous-agricultural-inversion` | 1 | 4 | PASS |
| `groundwater.porous-calibration` | 1 | 9 | PASS |
| `groundwater.porous-inversion-sampling` | 1 | 4 | PASS |
| `groundwater.porous-inversion-training` | 1 | 4 | PASS |
| `groundwater.porous-map` | 1 | 3 | PASS |
| `groundwater.porous-scenario-comparison` | 1 | 4 | PASS |
| `groundwater.porous-simulation` | 1 | 12 | PASS |
| `groundwater.render-map` | 1 | 2 | PASS |
| `groundwater.rule-classification` | 1 | 4 | PASS |
| `groundwater.source-sustainability` | 1 | 3 | PASS |
| `groundwater.summary` | 1 | 2 | PASS |
| `groundwater.sustainability-input-preparation` | 1 | 2 | PASS |
| `groundwater.transfer-learning-analysis` | 1 | 25 | PASS |

表中计入此前六条生命周期、五条 Native 复验、七条孔隙主流程和两条评价主流程。孔隙地图的真实栅格复跑、评价桥接、重启和取消记录另见各子报告。按当前领域 manifest 对照，模型 ID 无缺失和多余项。45 项验收环境、数据性质和目录约束测试通过。评价服务 63 项、准备模块 3 项通过。核对本轮六个隔离项目的容器、网络、卷和镜像均无残留。 [模型覆盖、运行身份及测试源码摘要](/home/mtd/.local/state/groundwater-v6/validation/model-e2e-20261111/model-coverage.json)

真实观测衍生数据的验证范围为本地趋势、USGS 流场与克里金、ODNR DRASTIC 以及 USGS 地图发布。孔隙流动、运移、率定、反演和综合评价以合成工程算例验证真实服务。场地尺度的科学等价、校准外推及真实大模型 Provider 表现仍未评估。

后续腰站堡模拟案例已以独立的年度水量、井试、水质和管理数据完成综合评价与发布。 [模拟案例测试报告](YAOZHANBAO_SYNTHETIC_VALIDATION_2026-11-11.md)
