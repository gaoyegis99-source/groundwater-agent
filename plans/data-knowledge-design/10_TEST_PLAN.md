# Data + Knowledge 验收与测试计划

本轮只读核对合同、DDL 和测试源码，并静态核对资产、全部文档转换及评测目标。下列用例均为批准实施后的测试计划。未连接数据库或启动 Data、Knowledge、Embedding 运行栈。对照设计包 `06_ACCEPTANCE_MATRIX.md` 的 16 项逐项验收。 [输入验收矩阵](/home/mtd/liweigis/workflow/groundwater-v6/plans/data-knowledge-design/06_ACCEPTANCE_MATRIX.md)

## 1. 验收对照

| ID | 正向用例 | 负向、版本及隔离用例 | 预期与证据 |
| --- | --- | --- | --- |
| D-01 | 监测表先只用 `fields`、量/角色、单位、时空及来源声明登记 | 老合同无 Profile 仍可读写。空/窄样本不足，先记录缺口和原因。条件性扩展不得重复已有字段 | 建立字段矩阵和样本对应项。原共享 Semantics、发布摘要及幂等重放保持不变 |
| D-02 | 固定项目、dataset、version、semantic 和 `fN`；检索历史 Revision | 重名列、换列位置、错 field、跨版本、越权、并发 CAS、旧写省略/清除扩展 | 精确字段归属正确，CAS 仅一方成功，失败事务无孤儿修订。已核验值不会跨版本自动继承 |
| D-03 | 有可定位采集文件的声明、用户确认及来源核验分别记录 | `water_level/depth/elevation/head` 同名异义，缺来源或用户确认不等于核验原件，冲突及旧记录 | 核验等级和凭据能解释，未知/推测/冲突保留，不能升级成事实 |
| D-04 | 同物理量 m/cm 显式换算，登记 IANA 时区、水平 CRS、垂向基准及测点 | 错单位、未知单位/CRS/基准、混合测点、位置缺失、长表多单位、DST 和重复时间 | 单位、CRS、垂向基准相互独立。缺基准的记录无法通过绝对水头适用检查。无坐标系不评依赖 CRS 的距离/面积 |
| D-05 | 可读文件、规则运行和针对趋势/流场的可用性分别显示 | 可解析但数据异常，五行样本掩盖全表重复，规则 PASS 但流场基准未知，空表结构检验 | 三类证据分开。保留局部适用结果，不输出 Dataset 全局科研 PASS |
| D-06 | 运行锁定 `table.validate`，精确字节和规则两次复跑，保存输出引用与摘要；对照声明和产物关联 | 伪填运行 ID/实现摘要、错规则/语义/对象、权限不足、待发布/过期引用、规则缺失、空表、绑定并发改变、超时、旧重放 | `DECLARED`、`ARTIFACT_LINKED`、`VERIFIED_EXECUTION` 分别准确。真实执行记录可追到结果，失败有终态且不能假造规则通过 |
| K-01 | 一次对比 9 个资产、121 文档、321 单元与原生转换器 | 漏项、重复身份、仅复制 provenance、自改正文或源、未知用户导入、多项目 | 用途按经复核文档和单元摘要匹配。初始登记的待核材料留在管理及审计范围 |
| K-02 | 核对核心方法原文、sourceId、版次、locator、适用范围与审阅人时间 | 缺 locator、只有内部章节、循环引用、题名错误、版权或获取权限不清、过期标准 | 来源本身可识别，能定位主张。未核明确保留，不能补造 locator |
| K-03 | 样本比较 Model Manifest、Operator、Model Card、Method 单元和唯一维护方 | 改模型版本或参数默认，旧卡引用失配。不同适用性的同义单元不被归并 | 卡片引用准确三元组，默认值和端口由锁定合同给出。去重不损害旧证据身份与方法条件 |
| K-04 | 正式及显式 Demo 各命中同主题正确单元 | Demo 比正式相似度更高、正式少于 K、mixed 单元、带官方来源的 Demo、Demo 递归依赖及混合标题摘要 | 词法、向量 exact/HNSW、混合和 lookup 的硬过滤先于 limit。默认结果与上下文 Demo 漏出为 0。Demo 显式路径保留 ACL |
| K-05 | 当前有效版可检索，历史授权范围可审计 | 替代版本精确读、过期、撤回、来源失效、并发版本更新、bootstrap 重启 | 候选只来自当前允许版本。精确审计生命周期状态保真。回执固定候选快照，旧 bootstrap 不会被当成新内容审批 |
| K-06 | 候选后用 `versionId/unitId/checksum` 完整读取，闭合依赖 | 错 checksum、`validate`、`NEEDS_EXPANSION`、环、越权、生命周期变更、索引与回执跨快照 | 只有完整 `OK` 内容有引用资格。validate/候选不足不能取代读取。未检绑定的读回执保持 `NOT_CHECKED` |
| I-01 | 授权监测表完成 Data 版本/语义登记、QualityRun、项目字典绑定、方法检索与读取 | 通用模板冒充项目事实，错 project/dataset/version/semantic/field，管理员撤回 | 数据事实归 Data，方法归 Knowledge。绑定精确，History 与 latest 不互换。两服务不复制对方的权威载荷 |
| I-02 | 旧新 SDK/HTTP、观察工具、历史库、原版本对象、无新增值与有新增值的读写 | 旧二进制检查 schema、严格响应拒绝扩展、断电恢复、停用新增写入、旧客户端漏字段 | 当前方案不需 DDL。预案若变更，只加、无回填、原资料与收据可读取。必须实际演练兼容回滚再批准生产应用 |
| I-03 | 审核文件差异、依赖、路由与受影响测试 | 禁止范围内出现执行路径、Manifest、生产库、语料改动 | 实施范围停在 Data/Knowledge。设计评审时只增加设计文档。原用户输入、原评估和固定题集保留 |
| I-04 | 本矩阵每项对应一项实现前证据和后续测试负责人 | 缺样本、无真执行、运行环境混用、本轮仅静态检查 | 静态、合同、隔离运行、真实授权样本报告分开。未跑状态写入最终回执，不能用测试名称代替结果 |

## 2. 固定评测和新增覆盖

按原测试文件静态核对共有 297 道固定题。其中 57 道指向 19 篇全部标记 Demo/模板来源的文档，另有 6 道指向两篇含正式和演示候选的混合文档。这个冲突意味着不能用同一个默认正式查询同时满足 57 道 Demo 命中和 Demo 隔离。保留原题集、评分器和门槛，使用隔离的旧策略基线单独复验。正式策略使用扩展集，另外在报告中逐题记录老题在新策略的范围差异，不以删除题目冒充独立复验。现有专业扩展题及所需确切引用也保留。[固定题集](/home/mtd/liweigis/workflow/groundwater-v6/source/tests/fixtures/groundwater-knowledge-retrieval.v1.json) · [专业集](/home/mtd/liweigis/workflow/groundwater-v6/source/tests/fixtures/groundwater-knowledge-professional.v1.json) · [评分器](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/scripts/evaluate.py:108)

| 冻结对象 | SHA256 或条件 |
| --- | --- |
| 原固定题集 | `848f2a42843a46c4b9e69e203c5352ab24978fa47c8bec79a1c01095694a7c6d` |
| 原专业题集 | `4c367cb08bcc68713a88b4b5277cfd51f24564e3767a50d05569d00164d41d68` |
| 原评分器 | `54f8ee4e549332a2cdf0da4d9ce33649a3a73ce671c92a4934a11b37ab5efff7` |
| 原固定门槛 | 297/297 Top-5，269 以上 Top-1，趋势/流场反向召回 0，297 项证据身份有效，无请求失败。legacy Top-5 279/279、Top-1 至少 252，modelCards Top-5 18/18、Top-1 至少 17。附加绑定及专业题门槛保持原实现 |

新增小型扩展集另行保存，锁定题和标注后再测。建议 24～36 道，用案例标签登记允许范围、目标文档/单元、必读多个单元、禁止单元、知识当前性、绑定和预期无结果。至少覆盖以下对照：

- 高频相似主题。MK 与 Pettitt，IDW 与 Kriging，Demonstration 水位与专业场景量。负例为相似但方法不适用或物理含义不同。
- 双用途隔离。同问题走默认和显式 Demo，含混合文档、共享标题和递归上下文。另有测试让 Demo 占满预筛前 K 个候选。
- 生命周期。已过期来源、撤回或替代及正确的当前版本各一对，按请求授权核查历史结果。
- Data 绑定。完全正确、错 `semanticId`、错 `fieldId`、错 dataset/version/project、整版本与字段方向分别覆盖。未知项目只可返回适用通用知识或无结果。
- 组合证据。方法限制、预测不确定性、单位/基准，分别要求两个互补的权威单元及一次精确读取。也包含预算不足和 `validate` 不返回正文的负例。

正式策略硬门槛建议为 Demo/待核/过期/无授权误召回 **0**，强标注的所有精确绑定和证据 Mismatch 拒绝正确率 **100%**，所有声明为可引用的 fixture 结果精确读取 **100%**。目标方法/卡片按 Recall@5 并列报告。Recall 分母只含有审阅相关性的查询，并同时报告预期空结果通过率。MRR 在有首选目标时报告，nDCG 只用于确定等级相关性的题。相关性和性能门槛在隔离基线和人审确定后批准，不预报未经测量的提升。按相同语料、Embedding、请求预算和并发报告 P50/P95，将 Provider 和 DB 耗时拆开。过滤和检索相互影响的漏召回单列；若预先选定阈值未达标，不以扩大 top-K 或引入新检索栈掩盖。

## 3. 执行顺序与已有测试入口

1. 离线模型和契约。先做结构、严格字段、CAS 定义、OpenAPI 与工具锁文件、来源和引用闭合测试。原固定评测文件按 hash 核对。修改公共合同前先交 ADR。 [公共 API 契约](/home/mtd/liweigis/workflow/groundwater-v6/source/tests/contracts/test_public_api_contract.py) · [资产装配和模型身份](/home/mtd/liweigis/workflow/groundwater-v6/source/tests/assembler/test_domain_packages.py)
2. 专用测试库与 Native 环境。跑 Data `test_service` 的精确发现/历史/单位，`test_quality_control` 的 suite/binding/run 及要新增的真实执行证据。验证查询、样本、并发、幂等、归因、发布产物和重复规则输出。规则复跑对比内容与结果摘要，不要求执行 ID 或时间一致。 [Data service](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/tests/test_service.py) · [QualityRun](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/tests/test_quality_control.py)
3. 专用 Knowledge 实例。mock Embedding 验证候选硬过滤、ACL 与版本，真实现有 Embedding 验证固定和扩展评测。测试候选不足时 HNSW exact 回退也执行过滤。测试查询期间更新只见前或后快照，证据历史不重新指向新版本。 [Scope 与重试](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/tests/test_scope_retry.py) · [Evidence 与 HTTP](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/tests/test_functional_contracts.py) · [预算](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/tests/test_retrieval_budget.py)
4. 服务边界联调和回滚。使用授权样本和独立 `_test` 库。对照旧客户端/工具录制请求与新服务响应，分别验证旧策略回归、受治理策略、兼容应用版本回滚。范围截止 Data 质量回执和 Knowledge 证据读取。联调记录保存 SHA、输入身份、规则、服务版本、清单版本及结果。生产及 UI 的执行属于另行批准项目。 [联调入口源码](/home/mtd/liweigis/workflow/groundwater-v6/source/scripts/checks/check_data_knowledge_e2e.py)
