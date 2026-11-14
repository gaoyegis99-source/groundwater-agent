# 地下水字段登记与分析适用性

项目 Data 使用同一条 `datasetId/versionId/semanticId/fieldId` 关联字段。量类型和角色来自资料、显式映射或人工核验。字段名只作候选提示。以下值用于 Data `quantityKind`，角色另写入 `role`。每项以 `source` 标记出处，`applicability` 记层位、时期、测量点或工况。未知值留空。

| 量类型 | 量与候选单位 | 应核对的限定 |
| --- | --- | --- |
| `water_level_elevation` | 水位高程，m | 垂向基准、测量点与有效期 |
| `depth_to_water` | 地下水埋深，m | 深度零点、正方向、井口或地面 |
| `hydraulic_head` | 水头，m | 共同基准、连通层段、观测或派生出处 |
| `drawdown` | 降深，m | 参考水头、正方向、抽水工况与时刻 |
| `hydraulic_conductivity` | 水力传导系数，m/d 或 m/s | 测试方法、方向、层位与尺度 |
| `transmissivity` | 导水系数，m²/d 或 m²/s | 有效厚度、试验解释、假设与层段 |
| `specific_yield` | 给水度，1 或 % | 潜水条件、比例尺度、试验依据 |
| `effective_porosity` | 有效孔隙度，1 或 % | 连通流动、试验或情景范围 |
| `recharge_depth` / `recharge_volume` / `recharge_flux` | 补给深度、体积或通量 | 面积、时间窗、方法和空间支持 |
| `abstraction_volume` / `abstraction_rate` | 开采体积或流量 | 井群、计量、时间窗和缺报 |
| `monitoring_well_id` | 监测井标识 | 井号、井段、位置与测量点有效期 |
| `aquifer_id` | 含水层标识 | 层位来源、井段关联和解释版本 |

- 文件质量采用 Data 的校验和、格式解析和物理字段画像。物理行数、全表缺失值、非有限值与样本各有覆盖口径。
- 数据质量复用 `table.validate` 套件，明确必填字段、单位对应、值域及井号与时间的组合唯一性。字段重复与异常候选应保留原因，避免覆盖原始版本。绑定或语义改变后，发现不会沿用旧运行的状态。
- 趋势分析以井、层位和测量口径分组，核对统计窗口、时区、采样频率、有效样本、缺测连续性和异常原因。Pettitt、季节趋势与其他分支分别解释可评估状态。
- 流场分析核对参考高程与埋深的共同零点、单位、垂向基准、含水层和目标时点。空间侧核对真实井位、同一水平 CRS、井网覆盖及外推。空间包围盒只支持筛选，覆盖与共线性仍需用实际井点判断。
- 可转换单位和坐标先经过显式处理，派生数据记录输入版本、语义、转换和校验和，再发布为新版本。只有具备精确数据身份的项目字典可作为数据绑定知识。

[Data v3](../../source/services/data/README.md) · [趋势模型](../../source/domains/groundwater/6.2.0/models/monitoring-trend.model.json) · [流场模型](../../source/domains/groundwater/6.2.0/models/flow-field.model.json)
