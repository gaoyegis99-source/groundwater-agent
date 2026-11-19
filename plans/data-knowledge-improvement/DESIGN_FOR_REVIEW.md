# Groundwater Data + Knowledge 最小增量设计草案

基线 `e6d8512797a128887cf2bab41a92629ddb06b668`。状态为待原设计包核对。当前聊天已明确 Data 与 Knowledge、仅兼容新增、先设计的边界，但上传包及 `07_CODEX_DESIGN_PROMPT.md` 尚未在 devbox 或本聊天附件中出现。本文保留可核实的源码结论和可审阅方案。字段名、接口形状、分类策略和验收门槛是待核对的设计选择。

## 1. 第一批改动与验收范围

| 范围 | 最小增量 | 交付边界 |
| --- | --- | --- |
| Data 监测语义 | 在现有 SemanticRevision 保存可选、有版本的监测 profile。复用字段含义、单位和基准，补观测角色与限定条件 | Data 服务提供声明与检查证据。研究任务的执行门禁另行审批 |
| Data 质量 | 区分物理描述、规则结果、证据核验程度及任务适用性。沿用 QualityRun 与绑定 | Data 可核验其拥有的发布结果及精确引用。完整执行归因取决于既有结果是否提供足够证据 |
| Knowledge 内容 | 逐单元整理正式专业知识、模型卡、项目知识、Demo 和待核内容。给核心方法补来源定位 | 以现有知识版本和生命周期发布，经批准再操作 |
| Knowledge 检索 | 共用现有候选过滤，默认研究检索排除 Demo，演示独立择用 | 保留绑定、权限、证据身份、双路检索、RRF 和上下文预算 |
| 持久化与兼容 | 使用 Data v8 和 Knowledge v7 的现有结构。首选零 DDL | 若实现证据要求新字段或索引，另交 additive ADR 和容量依据 |

设计涉及 Data 与 Knowledge 服务、其合同和测试，以及知识治理的版本化清单。Kernel、Agent Runtime、Prompt、Planner、Executor、前端和既有 Domain 发布身份均在设计实施范围外。本轮交付为文档，数据库结构证据来自 DDL 和版本检查源码。

## 2. Data 监测语义

### 2.1 存储和接口

`semantic_revisions` 已保存 `id`、`version_id`、`body jsonb`、`actor` 和 `created_at`。`versions.semantic_id` 指向当前修订。`add_semantics` 在单事务中先写新修订，再按旧 `semantic_id` 更新指针。冲突抛出 409 并回滚。`FieldMeaning` 已有 `meaning`、`unit`、`quantityKind`、`role`、`source`、`applicability`，`Semantics` 已有 `grain`、`timeRange`、`timezone`、`crs`、`verticalDatum`、`spatialExtent`、`limitations`。这些字段继续承担其当前职责。[DDL](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/migrations/001.sql:15) · [Semantics 与 FieldMeaning](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/models.py:29) · [CAS](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/semantics/service.py:17)

建议在 POST 语义修订的 `SemanticUpdate` 上增加可选 `monitoringProfile`。保存于同一 `body` 的扩展键，由 Data 边界负责拆分和校验。复用准确版本的 `GET /data/v3/versions/{versionId}/semantics/{semanticId}`，以可选 `includeMonitoringProfile=true` 返回扩展，旧调用的 JSON 形状保持当前行为。基础 `Semantics` 和发布输入保持原合同。需要审查全部 JSONB 读取者，查询回执、`describe`、普通版本详情、Discovery、Observation Tools 和历史默认读投影只传原有基础字段。不要把持久化 JSONB 原样交给严格的旧模型。新增请求字段须经过 Data OpenAPI 和 SDK 兼容校验，正式合同名待原提示词核对。[语义 HTTP](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/api/routers/semantics.py:15) · [SemanticUpdate](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/api/models.py:30) · [版本响应](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/api/responses.py:380) · [发布摘要](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/publication/service.py:84)

`monitoringProfile` 首期只支持一张带井号和时间的监测表。用 `(projectId,datasetId,versionId,semanticId,fieldId)` 从外层推导精确身份。井号、含水层标识和筛管层段仍是项目字段或来源声明。按观测行变化的属性绑定字段，不能用一个全表常量覆盖多个井或时段。角色不能以列名或默认模型参数推断。

| 结构 | 类型与约束 | 来源和缺项处理 |
| --- | --- | --- |
| `schemaVersion` | 字面值 `groundwater-monitoring.v1` | 缺整个 profile 的历史修订保持未知 |
| `fieldRoles` | 受控角色到同一 descriptor 的 `fN` 字段映射。单一字段可在有明确理由且不冲突时复用 | 最小登记角色为 `wellId`、`observedAt` 和至少一个观测值角色。可选 `waterLevelElevation`、`depthToWater`、`hydraulicHead`、`referenceElevation`、`x`/`y`、`aquiferId`、`screenInterval`、`measurementPoint`、`observationState`、`qualityFlag`。水温或水质以现有 quantityKind 识别，专门解释规则留给后续任务 |
| `measurementBasis` | 按观测值字段记录 `referenceKind` 与 `positiveDirection` 等缺失的结构化限定。测点可参照 `measurementPoint` 字段。共同基准复用 `verticalDatum` | `referenceKind` 仅在有依据时选择测点、井口、地面或其他明确已登记基准。来源复用 FieldMeaning，无法核对时留空 |
| `samplingInterval` | 可选，仅显式声明观察尺度。观测时区复用 `Semantics.timezone`，CRS 复用 `Semantics.crs` | 不从几行样本推断采样间隔或范围。多时区/基准用行字段及来源表达，超出首期可评估范围时返回未知 |
| `stateCodes` | 可选，有限的原始代码到静水位、动水位、恢复期和质量状态的映射 | 缺失/异常/人工修订的原始值与派生解释分别保留。未知代码不当作正常 |

结构错误如不存在字段、互斥角色、孤立 X/Y、量类型与声明相反返回明确 422。科学证据不足仍可登记，读取检查时交代 `UNKNOWN`，不把缺失事实升级为通过。`PASS` 仅表示对应声明和检查范围一致。单位检查分别报同单位、支持的可转换单位、冲突、未知。先登记后转换，转换后的真实值由既有版本化派生流程持有。趋势或空间适用性的检查结果须带精确版本、适用对象、检查项和原因，不能以 profile 存在代替研究批准。共同参考点、统一物理量和单位是高程减埋深的前提。混合基准或井段不能靠一个全表字符串解决。[字段词表](/home/mtd/liweigis/workflow/groundwater-v6/plans/knowledge-data-foundation/groundwater-field-dictionary.md) · [流场输入条件](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/models/flow-field.model.json)

新客户端通过 CAS 明确替换或清除扩展。请求省略扩展时，若 `fields/grain/timeRange/timezone/crs/verticalDatum/source/limitations` 等被 profile 引用的基础声明不变，则将原扩展随新修订复制。若存在依赖变化，新修订的 profile 需要重新确认，旧修订继续可读。明确的 `null` 表示新修订清除，缺省与 `null` 用请求字段集区分。读取和比较须在 CAS 所用的同一事务内。历史回放及 Knowledge 绑定保持旧 `semanticId`，当前 discovery 仍只返回当前状态。[历史读取](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/repositories/http_queries.py:25)

### 2.2 质量与精确证据

`QualityRun` 当前已固定 dataset、version、semantic、bindingRevision、suiteVersion、规则结果、操作算子身份与摘要。`submit_run` 检查权限、版本、语义、当前启用绑定和结果集，`operator_run_id` 和 `implementation_digest` 本身是提交值。Discovery 仅关联当前版本、当前语义及当前绑定对应的运行。建议保持三个口径：文件结构事实由现有 descriptor 提供，指定套件规则评估由 QualityRun 提供，具体研究的科学适用性从必要条件单独判断。[质量 DDL](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/migrations/003.sql:44) · [submit_run](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/quality.py:200) · [发现质量摘要](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/catalog/service.py:165)

最小方案在 `results[].evidence` 的现有 JSONB 中增设版本化引用，按项目权限核对已发布的 `datasetId/versionId/objectId/checksum` 和到被评估输入版本的 lineage。可取回的实体按现有读取规则核对。服务只对本次实际核对字段记录 `ARTIFACT_LINKED`，无此证据的结果继续视作 `DECLARED`。如需持久记录核验状态，使用现有 `quality_runs.summary` 的受服务控制扩展键，并在现有 API 的兼容投影中审查是否对旧消费者可见。禁止客户端写服务核验状态。规则结果的幂等摘要、授权及告警逻辑沿用现有机制，旧入参和原幂等键不能因新增默认值组成不同摘要。若指定证据无法匹配，拒绝带核验承诺的提交，不能隐式降级并返回成功。可按当前权限拒绝或隐藏不可读证据，与现有 Data 策略一致。字段存在也不证明文件内每条规则已真实执行。

Executor 当前发布 provenance 包含 model、workflow 及 operator catalog 摘要，Data lineage 按输入版本记录。仅凭这些字段无法证明特定 `semanticId`、套件规则摘要、节点实现和提交结果完全一致。除非找到已签发且可从 Data 核实的既有回执，否则 `EXECUTION_VERIFIED` 不应出现在第一批通过条件。质量证据引用与科研门禁分开验收，后者留给本轮边界之外的审批。[Executor provenance 只读证据](/home/mtd/liweigis/workflow/groundwater-v6/source/services/executor/src/platform_executor_service/result_publication.py:30) · [Data 发布事务](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/publication/service.py:183)

## 3. Knowledge 内容治理与检索

### 3.1 治理现有资产

按当前 6.2.0 Manifest 解析得 9 个知识资产、121 篇文档、321 个单元。以 `sourceRefs` 中的 `DEMO_ASSUMPTION`、`PROJECT_TEMPLATE` 作待审标记时，53 个单元命中，19 个文档全部命中，另有两个方法文档混合。标记用于候选筛查。正式性需要逐单元审阅来源及文字内容。当前 `requiredChunks` 出现在一个模型卡单元，任何默认候选的递归上下文也须通过分层检查。[当前清单](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/domain-manifest.json) · [逐篇出处记录](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/KNOWLEDGE_INVENTORY.md)

| 用途 | 权威材料与读取条件 |
| --- | --- |
| 专业基础与方法 | 理论、统计、质量控制和专业限制。默认科研候选须有清楚来源与适用范围。缺证据保留审阅状态 |
| 模型卡 | 使用现有 `modelId/modelVersion/modelChecksum` 绑定 Manifest。机器参数、端口与数量引用锁定声明。卡片集中说明实现差异、结果解释和限制 |
| 项目资料 | 保留精确 `DataBinding`。整版本绑定可适用于该版本同语义下的具体字段，字段绑定不能反推整版本适用。新版本或语义修订须重审绑定 |
| Demo 与模板 | 示例输出、合成情景、占位字典及混合段中的演示部分。只在明确演示请求中检索。保留来源标签以供精确回看 |
| 待核 | 题名或适用性冲突、无法定位来源、跨用途上下文尚未拆清等。保留材料并记录复核负责人及理由 |

每个治理记录建议含 `assetChecksum/knowledgeId/unitId`、用途、引用、核验记录、适用范围、限制和条款定位。核验状态与用途独立。`VERIFIED` 要求逐主张来源证据和核验记录。其余可以是 `PARTIALLY_VERIFIED`、`UNVERIFIED` 或 `SUPERSEDED`。沿当前 `Source.version`、`Unit.locator`、`requiredUnits`、`Document.provenance` 和生命周期组织。模型卡机械事实与方法解释分别维护，同义段仅可在同适用范围、相同来源及上下文闭合时通过新版本去重。优先审查 USGS Circular 1139 题录差异和核心趋势、空间方法的依据，沿用前轮证据登记。本轮不改写知识正文、来源或当前指针。[原生知识合同](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/models.py:25) · [前轮来源抽查](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/EXTERNAL_REFERENCES.md)

### 3.2 无 DDL 的默认检索隔离

建议先以发布可审阅的治理清单覆盖当前 321 个单元。在可信构建中按当前转换器生成清单，记录 `knowledgeId`、`unitId`、落库文档 checksum、落库单元 checksum 和用途。资产摘要和 `provenance.package_checksum` 用于溯源。匹配必须使用 `Importer.commit` 对实际正文计算的两个 checksum，避免调用方仅复制 provenance 或原 ID 即继承正式用途。`versionId` 由每个实例分配，因此由候选行关联真实 `versions` 和 `units` 校验，不作为跨部署通用身份。已知种子文档的未登记摘要视为待核，单元即使相同，也须重新核对文档标题、摘要、来源及所需上下文。[导入摘要](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/ingestion.py:334) · [摘要算法与索引文本](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/encoding.py:10)

服务对词法候选、向量候选和 lookup 共用同一政策，并与现有可见性、有效期、绑定及生命周期约束取交集。候选 SQL 可用只读参数表匹配 `search_entries` 所指版本和单元，确保在单路 `LIMIT` 及融合前过滤。治理清单标记正式单元前，要检查 `requiredUnits` 的传递闭包及文档标题、摘要、别名是否带演示信息，因为这些公共字段参与索引与结果呈现。共享元数据持有演示事实的混合文档暂列待核，待内容修订后再启用正式单元。读取检索结果的上下文时在同一快照再次核对用途。凭精确身份回查历史证据继续走既有 ACL、checksum 和生命周期检查。[索引投影](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/catalog.py:36) · [证据上下文](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/retrieval.py:286)

默认使用科研候选。对显式示例请求，建议仅向 Knowledge HTTP 检索与 lookup 增加可选 `audience=demo`，返回演示候选且继续受 ACL、scope 和绑定约束。现有 `kind`、`sourceKinds`、`scope` 保留原义。内部 v5 工具输入和输出形状保持原合同，默认受研究过滤约束。HTTP 策略兼容包括原请求形状继续有效，以及默认集合因 Demo 隔离而有意缩小。当前外来、非已治理知识的默认可见范围与回退策略有待核对设计包，尤其须验证对项目私有知识和既有用户自建方法的影响。演示是否经 Agent 选择另行审批。保留 RRF、候选配额及查询向量设置。没有测量依据前不增排名器或索引结构。[候选及证据实现](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/retrieval.py:40) · [原生 HTTP 路由](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/routers/retrieval.py) · [现有工具路由](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/routers/tools.py:12)

当前 Assembler 严格限制 `domain-knowledge.v2` 的 document 和 chunk 键。Bootstrap 会按锁校验文件，按 preflight 差异以 CAS 导入种子。不能直接往旧资产塞新分类键，也不能把同 ID 原生包一次导入视为持久治理，后续原种子 bootstrap 可能把同 ID 切回旧内容。因此第一批建议以独立的已锁治理清单完成过滤。真正修正正文或来源时，按需发布新资产版本并有明确受支持的部署选择。治理清单与候选过滤一并启用，旧版可以读取原归档，停用策略会恢复此前检索范围，需要记录环境切换和测试证据。原有向量、单元和回执处理保持其现行所有权。[Assembler 严格结构](/home/mtd/liweigis/workflow/groundwater-v6/source/platform/assembler/assembler_manifests.py:184) · [转换器](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/package_conversion.py:7) · [Bootstrap CAS](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/cli.py:97)

## 4. 源码映射

以下为后续实现落点。源码只读核查覆盖了现有合同和存储路径。

| 设计项 | 当前符号 / 数据 | 拟议触点 |
| --- | --- | --- |
| 监测 profile | `SemanticUpdate`、`add_semantics`、`check_semantics`、`semantic_revisions.body` | Data 语义修订接口校验、存储和投影。绑定同版本 descriptor，并保持原发布合同。见 [请求模型](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/api/models.py:30) · [语义服务](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/semantics/service.py:8) |
| 发布和查询兼容 | `begin`、`describe`、`get_semantic_revision`、Query 的语义输入快照 | 保持发布摘要，隔离旧 wire 投影，精确读取支持扩展。见 [目录](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/catalog/service.py:17) · [Query](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/query/service.py) |
| 质量回执 | `submit_run`、`_run_wire`、`quality_runs.summary`、`quality_rule_results.evidence` | 同一 Data 所有权下校验对象和血缘，将核验级别与规则状态分开。见 [质量服务](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/quality.py:175) · [质量 HTTP](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/api/routers/quality.py:108) |
| 知识治理 | `convert_domain_package`、`document_content`、`Importer.commit` 和 `versions.body` | 以精确内容和单元身份关联审阅。发布机制使用旧接口。见 [导入事务](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/ingestion.py:334) · [正文摘要](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/models.py:105) |
| 候选隔离 | `EvidenceService.filters/candidates/lookup/bundle`、`search_entries`、`required_context_ids` | 统一研究及 Demo 的过滤、回执和递归上下文核对。见 [检索](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/retrieval.py:44) · [检索预算](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/retrieval_budget.py) |
| 精确 Data 绑定 | `DataBinding`、`binding_applies`、`DataAdapter` | 沿用项目/版本/语义/字段匹配。用旧和新修订组合测试。见 [DataAdapter](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/data_adapter.py) · [匹配规则](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/retrieval.py:20) |
| 合同回归 | Data public OpenAPI v3、data-tools-v4、Knowledge public OpenAPI v3、knowledge-tools-v5 和聚合 public API v12 | 先审查新增字段的 wire，只在批准后重生受影响的合同，保留旧观察工具及前端可用性。见 [聚合合同测试](/home/mtd/liweigis/workflow/groundwater-v6/source/tests/contracts/test_public_api_contract.py) |

## 5. 兼容性与数据库方案

- 当前定义是 Data schema v8 和 Knowledge schema v7。`Database.check()` 按精确版本拒绝不匹配，故任意 schema bump 会阻止旧实例直接回切。首期复用现有 JSONB、索引和历史保留机制，结论为零 DDL、零全表回填。任何后续必要 DDL 必须兼容新增，提交新迁移及旧二进制兼容论证。历史迁移中的 DROP 或 RENAME 只是现状证据。[Data 版本检查](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/src/platform_data_service/adapters/database.py:110) · [Knowledge 版本检查](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/src/platform_knowledge_next/database.py:110)
- 旧 Data 客户端能读历史和当前语义的基础字段。扩展进入存量 JSONB 后，旧服务的严格 `VersionResponse` 可能拒绝该键。部署顺序必须先让路径具备兼容投影，再启用新写入。旧进程二进制回切需保留适配层或停止使用扩展写入；已写扩展的历史行不能靠破坏性清理回切。若必须支持原二进制读取所有历史行，须评审可由旧进程忽略的 additive 列或旁表，并把 schema 精确版本检查纳入双版本契约。仅凭无 DDL 不得承诺回滚兼容。
- 共享发布 `Semantics` 不加入默认项。保留旧请求摘要和已有幂等重放。新扩展通过独立语义 CAS 提交。Profile 的读取、写入和输出投影先用旧 SDK / 前端请求样本逐项验证。
- Knowledge 以配置撤回新的候选策略，保留 `versionId/unitId/checksum` 和回执。新知识正文版本只能经既有导入和生命周期路径，恢复应通过新修订或现有受控治理流程，保持历史可读。Demo 筛选不能影响历史精确读以及生命周期撤回的状态表达。
- 无数据库或生产变更。API 运行、版本切换、真实资产读写、索引性能与现场验证在本轮未测，归入后续批准后检查。

## 6. 测试与验收计划

| ID | 用例与主要证据 | 通过条件 |
| --- | --- | --- |
| D01 | Data v8 旧数据、无 profile 请求、版本描述、Discovery、查询、表格检查、旧 SDK、旧发布重放 | 旧 JSON 形状与摘要一致，已存在发布返回原身份。无 profile 表达为缺失。扩展只出现在约定读入口 |
| D02 | Profile 使用 `fN`，重名、异版本字段、井号前导零、同名异义、时间戳与时区 | 字段精确命中，同名异义不误绑，字符串井号保真，未知时区显式未知 |
| D03 | 水位高程与埋深对调、m/cm 可转换、未知或冲突单位、混合基准/井层、静动水位混合、变化测点 | 错误可定位，必要未知保留，转换只作声明并给理由。无实际时空记录时不标专业适用 |
| D04 | 两个并发 CAS，省略、明确清除、不相关编辑、依赖变化、读历史 | 一次成功，失败事务无孤儿修订。旧 semanticId 回放不变，依赖变化后旧 profile 不自动证实新声明 |
| D05 | QualityRun 完整规则集、已发布精确引用、错对象摘要/错项目/错版本/错 semantic、幂等与 ACL | 仅核对成功的结果得到约定证据状态，伪造运行 ID 不会获得已验证执行。历史规则质量语义与告警保持有效 |
| K01 | 9 资产/321 单元治理清单，源引用冲突、漏项、重复身份、只复用来源摘要的内容替换、相同正文但不同摘要或来源 | 当前资产逐单元有审阅或待核分配，未核实来源保持未核状态。文档或单元 checksum 漂移会停用已登记判定 |
| K02 | 纯 Demo、混合文档、演示文本是最高相似度、正式剩余少于 Top-K | 词法、向量、混合及 lookup 研究模式都无 Demo。可返回较少结果，明确 Demo 模式仍能找到合规示例 |
| K03 | 递归上下文、环、超预算、私有文档、撤回、过期、错项目/版本/语义/字段 | 任何扩展单元遵守相同用途与 ACL。证据精确读、生命周期状态及 `NEEDS_EXPANSION` 行为保持一致 |
| K04 | 锁校验、bootstrap 重跑、筛选前后回执、旧 v5 调用、现有固定检索评测 | 同包重跑保持幂等，身份不变。现有题集可用当前正式子集作回归，演示题单独验收，精度阈值有基线记录 |
| DK01 | 授权监测样本及旧版本与新语义并存。导入、语义 CAS、质量引用、绑定词法/混合检索，再读证据 | 同项目的精确版本和字段对应正确方法与来源，历史绑定不自动改指向。错误/缺项保持可诊断。验收到 Data/Knowledge 服务边界 |
| C01 | 两服务原始 schema 目录和 OpenAPI 差异、旧调用样本、恢复演练 | 零 DDL 方案不改变版本元数据。新旧 wire 边界按设计验证，旧数据与历史保全。回退方案可演练并记录限制 |

关联测试包括 [Data service](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/tests/test_service.py)、[QualityRun](/home/mtd/liweigis/workflow/groundwater-v6/source/services/data/tests/test_quality_control.py)、[Knowledge scope/retry](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/tests/test_scope_retry.py)、[Knowledge contract](/home/mtd/liweigis/workflow/groundwater-v6/source/services/knowledge-next/tests/test_functional_contracts.py)、[Domain 装配](/home/mtd/liweigis/workflow/groundwater-v6/source/tests/assembler/test_domain_packages.py) 及 [固定检索评测](/home/mtd/liweigis/workflow/groundwater-v6/source/tests/fixtures/groundwater-knowledge-retrieval.v1.json)。本轮没有执行这些测试。后续先运行纯模型与合同测试，再用专用测试库及授权样本运行联调。

## 7. 需以原设计包确认的事项

1. 指定交付文件及逐项验收条件。
2. 允许的 Data 扩展层、兼容线及对原二进制回退的要求。
3. QualityRun 第一批的验收是否需要完整的执行归因，以及现有可依赖的证据合同。
4. Demo 显式入口、未治理项目文档默认行为、治理清单持有方和作用范围。
5. 知识资产版本发布是否在本次设计覆盖范围内，以及旧项目历史引用的处理要求。

以上待核项需要以 `07_CODEX_DESIGN_PROMPT.md` 和同包相关输入消除。设计阶段完成后交用户审批，实施和迁移另行开展。
