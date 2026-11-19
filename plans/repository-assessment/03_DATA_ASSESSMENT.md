# 03 Data：科研资料的唯一权威，而非通用MLOps中心

**保留Data独立所有权和版本/语义/发布链；收缩实验管理的默认范围；优先修复科研适用门禁。** 当前Data并非只管理文件：已有字段物理类型、语义修订、单位、quantity_kind、role、来源、适用限制、CRS、vertical_datum、时间范围与空间范围。把这些能力描述成“完全缺失”不符合源码。[E10–16]

## 数据实体与职责

| 实体/能力 | 当前实现与实际调用 | 是否重复 / 处置 |
|---|---|---|
| Dataset | 稳定身份、title/tags/current_version；catalog用于发现 | KEEP；不能与不可变Version合并 |
| DatasetVersion | 不覆盖快照，期望版本CAS、历史查询 | KEEP；复现必需 |
| DataObject | raw与query Parquet分开、各自SHA256 | KEEP；派生查询表示不是第二份业务数据模型；转换配置和Arrow身份需保留 |
| SemanticRevision | 独立解释版本，与字节版本分离；可读取历史semantic ID | KEEP；改单位说明不应覆盖原字节或旧查询解释 |
| Field | 位置型f0/f1标识+原名，描述类型/空值/profile；FieldMeaning保存业务量 | ADVANCE；跨版本列位变化时不假设字段ID永久稳定；需受控井观测语义模板 |
| QualityRun | suite/binding/results/alerts；submit_run接收调用方报告并检查规则集合 | KEEP核心台账，SHRINK外部告警扩张；不能声称已自动执行专业质量检查 |
| Publication | OPEN→PREPARING→READY→PUBLISHED；输出组原子公开 | KEEP；Executor成功依赖Data commit，而非临时文件 |
| DataLineage | 发布input_versions、provenance、lineage边与OpenLineage事件 | KEEP内部关系；外发outbox可选，不需新增血缘平台 |
| Discovery/Inventory | catalog当前事实请求时投影；query/tag/field/unit/quantity/time/space过滤 | KEEP；不是第二数据库。字面匹配不等于语义搜索和适用性判断 |
| Query | 已登记Parquet、结构化受限DSL、队列/沙箱/回执 | KEEP；与完整科研变换算子不是同一任务 |
| Preview | XLSX/GeoPackage等有上限只读视图，不能据此宣称完整解析/质量 | KEEP按需；与Query共享操作设施但保留语义差异 |
| ML Experiment/Run/Registry/Alias | mlops.py与公开ml路由，记录环境、metrics、model版本和alias；MLflow outbox | SHRINK→可选研究扩展。主Kernel/Executor/领域Workflow未发现实际消费入口；真实外部用户UNKNOWN |

## 已有专业表达与实际缺口

`Semantics`有单个数据集级 `vertical_datum`；`FieldMeaning`有meaning/unit/quantity_kind/role/source/applicability。它可以表达“参考高程”“地下水埋深”，但仍主要是调用方声明。它没有默认的井构造、筛管层段、含水层、测量点变化史、静/动水位状态、仪器校准及记录质量限定的强类型业务对象。

不建议立即建立庞大的Well/Aquifer领域数据库。最小补强是一个版本化监测表profile：井标识、观测时刻、物理量、单位、测量基准/高程、层段与质量状态，以及来源证据；允许用现有表格+语义扩展承载。先明确同一研究能否比较这些记录，再决定是否需要新实体。[E10/E32/E42]

流场源码确实计算 `head = reference elevation − depth to water`，且支持m/cm/mm。不能简单说“混淆水位和埋深”。**风险是输入证据强制性不足，而非这条算术公式缺失。** 缺单位/基准时准备结果可能READY；更严重的是跳过显式准备时，提案边界未等价执行字段、语义和CURRENT_REQUIRED检查。[E32/E33；preparation-boundary-probe.json]

## 三种质量不能混为一谈

| 层次 | 能证明什么 | 当前实现 | 还缺什么 |
|---|---|---|---|
| 文件/结构质量 | 字节完整、可解析、类型转换、空值、栅格网格信息 | ingestion全表扫描、field profile、raster metadata | 不能直接证明测量可靠 |
| 规则符合性 | 明确规则版本下哪些行违反约束 | table.validate、QualitySuite/Run | 当前质量台账结果由调用方提交；主Workflow未见统一自动回写闭环 |
| 科研适用性 | 本任务时段、含水层、基准、单位、采样、方法假设是否成立 | Domain指引+局部门禁+模型限制 | required/optional/waivable规则、证据来源、人工判断冻结、结果限制自动带入 |

`quality.py`验证input/semantic/binding和结果规则全集，但没有据此证明真正运行过声明算子；`publication/service.py`也没有按QualityRun做统一科研拒绝门禁。这是业务闭环不足，不能指称现有文件发布事务错误。建议复用Executor已验证输出引用来登记专业检查回执，不再建第二质量执行引擎。[E13/E16/E50]

## 重复读取及成本

实际链：`discover_datasets`候选→`inspect_data_version`身份→`inspect_data_table`（内部又describe并提交5行Query，50ms轮询，最多25s）→`prepare_task_inputs`逐输入再次inspect→Executor admission再次核精确身份/读取字节。19输入迁移案例还要求逐项核实。这证明存在重复往返的路径，但没有生产trace，**实际延迟、重复率、Token占比UNKNOWN**。[E12/E32/E45]

最终Executor重核不能删，它防止跨服务身份漂移。可减少的是同一Turn内重复的描述/样本查询：单次获取带精确身份的元数据快照；准备和提案复用同一验证函数；只在需要看样本时调用TableInspector；CURRENT_REQUIRED/权限/生命周期仍需边界重新确认。不要跨Turn无条件复用旧证据，也不要新增一套缓存失效服务。

## Data减负结论

- KEEP：Dataset/Version/Object/SemanticRevision、对象校验、查询沙箱、发布、lineage、权限。
- MERGE：发现/详情/准备的重复读取流程与展示组件，合并公共实现，不合并权威事实。
- SHRINK：MLflow/outbox外发、注册/alias治理默认入口；保留公开合同兼容与历史可读，先统计使用再弃用。
- ADVANCE：强制专业前提、QualityRun可验证执行来源、语义编辑向任务导向收敛。

P0不能用删除模型或笼统禁止所有UNKNOWN替代。允许可选字段未知；关键物理前提必须补证或由明确适用的情景假设替代，并冻结到批准内容。详见任务A01/A02。
