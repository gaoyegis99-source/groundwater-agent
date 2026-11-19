# 04 Knowledge：收缩演示混用，强化证据质量

**当前RAG机制没有明显“技术堆叠过多”的证据；更大的问题是知识内容分层、来源定位、适用范围与默认检索策略。** 服务只有PG/pgvector双路检索、RRF、精确读取和生命周期，没有默认reranker、LLM查询改写或服务内Agent。不要为了减负退化为无版本、无引用的文本拼接。[E20–23]

## 所有Manifest知识资产

完整逐篇清单、来源、分块统计见 evidence/KNOWLEDGE_INVENTORY.md；全文均做结构读取/引用与精确重复检查，逐篇标题摘要核阅、专业正文定向核阅，未对每句话完成专家认证。

| 文件 | 文档 / unit | 实际角色 | 判断 |
|---|---:|---|---|
| agent-discovery-templates.v1.json | 3 / 3 | 项目概览、字典、模型依据模板 | EXPERIMENTAL/教学资料；不能证明真实Data事实 |
| demo-knowledge-interpretation.v1.json | 15 / 45 | 垂向梯度、边界、补排、密度、证据冲突等专业解释 | 不能按demo文件名整包删除；有正式来源引用，需整理归属 |
| demo-knowledge-methods.v1.json | 15 / 45 | 时间标准化、聚合、MK、IDW和敏感性 | 正式方法/产品约定/演示假设混合，逐单元分层 |
| demo-knowledge-quality.v1.json | 15 / 45 | 仪器、井况、基准、筛管、质量标记和资料验收 | 科研价值高；从文本提醒逐步转成任务检查，不另建知识引擎 |
| demo-knowledge-scenarios.v1.json | 15 / 45 | 演示场景与回答范式 | 独立case/demo scope，默认不当项目证据 |
| demo-knowledge.v1.json | 30 / 90 | 基础定义、趋势/流场、Demo操作和术语 | 多主题旧聚合资产；按权威知识主题组织引用，不改旧发布身份 |
| model-cards.v1.json | 10 / 30 | 当前10个模型用途、参数、输出/限制 | modelRef对应可执行身份；优先由声明生成机械信息 |
| porous-model-cards.v1.json | 8 / 8 | 7个Porous模型及评价准备 | 每篇约一段，缺充分科学适用诊断，不能当外部求解器验证 |
| groundwater-statistical-methods.v1.json | 10 / 10 | Sen/Pettitt、Kriging、DRASTIC、MODFLOW/FloPy/PEST/Pastas/不确定性 | 明确方法层；覆盖仍浅，不能将知识提及视为模型已装配 |
| 合计 | **121 / 321** | 44个唯一sourceId | 运行中导入/检索质量本轮UNKNOWN |

结构检查：121个唯一knowledgeId，321段无精确重复；sourceRefs不存在缺失/悬空引用；273段没有locator字段；53段至少引用DEMO_ASSUMPTION或PROJECT_TEMPLATE。**零字节重复不等于零语义重复；有sourceRefs不等于来源支持全文。** 273缺locator是字段存在性统计，不表示其余48段都有精确页码或逐主张溯源。

## 内容职责重叠

- `gw-trend-methods`、`gw-mann-kendall-cautions`、`gw-method-sen-slope`与监测趋势模型卡共同涉及MK/Sen/前提。应由method拥有科学解释，card只写该实现选择、输入和输出，并引用method。
- `gw-idw-and-gradient`、`gw-idw-sensitivity`、`gw-idw-validation`与flow-field卡共同解释IDW及外推。可以分别保留定义、验证方法和产品限制，减少相同规则多处手写。
- instructions重复许多模型卡的参数/输出，而模型JSON又是可执行事实来源。卡片、UI与系统提示的机械内容宜生成；专业限制保留人工审阅。
- 未发现独立Operator Cards资产集合；算子Manifest含description、参数、examples和smoke，不能把这些算作已经建成专业方法知识库。
- Standards以source引用为主，不是已核验、逐条可计算的标准全文库。规则分类/DRASTIC的VERIFIED是输入声明状态，不是平台独立认证。

## Demo与正式检索

`EvidenceService.filters`默认过滤可见性、生命周期/有效期和scope；source_kinds只在请求显式提供时生效。GENERAL由“无Data binding”派生，当前9个资产无真实Data binding，模板、场景和正式方法都可进入GENERAL候选。模型卡多数kind缺省为knowledge，不能只凭kind自动区分。[E21/E22]

因此存在**混合召回风险**，并未通过本轮真实Embedding评测证明“已发生污染回答”。已有领域指引明确Demo不能建立实际字段事实，这是有效保护，但不替代检索层可检验策略。建议逐unit/document设置用途与适用范围，默认科研检索排除纯演示；用户求示例时可显式启用。不能整包丢弃带demo文件名的正式知识。

## 来源、版本和引用

来源有publisher、URL、version、reviewAfter、lifecycle等信息，Knowledge保存不可变正文和独立生命周期修订；撤回/失效和binding校验有实现。导入预分块，服务不自动重新分块；requiredUnits支持上下文依赖。精确evidence/read及checksum应保留。[E22]

抽查发现 `usgs-circular-1139` 在本地题名写成“Ground Water Atlas reference”，USGS官方题录实际是 **Ground Water and Surface Water: A Single Resource**。这是来源元数据错误，不证明相关每句知识错误；修订应发布新知识版本，并核对对应段落，不能只改显示标题。见 EXTERNAL_REFERENCES.md。

目前“官方来源”多为概念依据，不能据题录推定现场参数、数据授权或当前标准适用性。所有未逐项验证的外部来源标UNKNOWN。现有受控语料文档99/291基线对应旧范围，当前Manifest为121/321；两者用途需明确，避免用旧固定数验收新目录。[E24]

## 最小补强

1. 保留Evidence身份、生命周期、精确绑定、预分块和预算；不新增GraphRAG、多层路由或自动改写链。
2. 统一method/card/instructions的事实来源，新增版本引用，保持旧版本可追溯。
3. 优先补正式知识的精确来源定位、适用条件、不可推导结论和错误示例；先覆盖六条主线中的现有模型。
4. 以“问题→正确方法/缺项→正确来源/原文单元→有界解释”为检索评测，不以文档数量增长验收。
5. 数据字典绑定按project/dataset/version/semantic身份生成；绑定知识不能自动追随latest，也不复制Data原文件。

结论：Knowledge服务KEEP，内容组织MERGE/ADVANCE，纯Demo默认入口SHRINK；研究语料授权/专家认可/运行中导入均UNKNOWN，本轮不修改任何知识资产。
