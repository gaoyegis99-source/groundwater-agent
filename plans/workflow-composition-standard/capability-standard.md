# 通用能力与工作流组合契约标准草案

本标准草案用于 Operator、Workflow 和 Executable Model 的统一发现、组合验证与执行。文中的 MUST 表示通过本标准符合性验收所必需，SHOULD 表示有理由时可偏离并记录理由，MAY 表示可选能力。标准的实现及协议版本由后续发布定版。[设计方案](README.md)

## 基础要求与扩展条件

| 对象 | 基础要求 | 按需声明 |
| --- | --- | --- |
| 算子 | 精确身份与实现版本、用途、运行环境与限制、输入输出端口 | 有参数时声明 Schema，涉及业务或科学适用性时声明语义与条件 |
| 端口 | 名称、逻辑类型、artifact 格式或 value Schema。默认基数为单项 | 可选或集合端口声明基数，存在内容约束时声明字段、单位、基准或形状 |
| 工作流 | 精确输入、节点依赖、已绑定参数与来源、命名输出、冻结摘要 | 按所用算子和目标声明约束、检查及所需特性 |
| 执行 | 绑定用户确认和冻结定义，Executor 复核输入与图，Data 发布选定结果 | 依运行环境与领域契约启用额外校验 |

同一种格式可以表达不同的科学含义。只有算子或目标依赖这些含义时，才需要声明和验证相应的语义事实。

## 对象与身份

单个算子的权威定义由 operator-pack 持有。组合定义由 Workflow 持有。面向用户的模型由 Executable Model 绑定工作流、数据与参数。外部服务绑定由 Product 部署持有。Data 保存数据字节、语义事实与版本，Knowledge 保存方法证据。Kernel 生成面向 Agent 的只读投影。

| 规则 | 规范要求 | 检查责任 |
| --- | --- | --- |
| ID 01 | 能力 MUST 有稳定 ID、精确版本、定义摘要和实现身份。发布身份不可原地替换 | Assembler |
| ID 02 | 图 MUST 固定算子、词表、验证器、复合图和环境的精确身份。运行时解析结果必须写入执行定义 | 编译器与 Executor |
| ID 03 | 单位、模型、CRS 等注册引用 MUST 来自装配选定的版本集合，未知引用产生诊断 | Assembler 与编译器 |
| ID 04 | 定义和执行实例 MUST 使用不同身份。一次重试沿用冻结定义并产生可追溯尝试记录 | Kernel 与 Executor |
| ID 05 | 定义摘要 MUST 采用固定规范化 JSON 规则。对象键递归排序，数组保序，UTF-8，禁止非有限 JSON 数值，排除自身摘要字段。数字采用 RFC 8785 的序列化规则，并固定规范化实现测试向量 | 共享契约库 |

摘要计算还必须拒绝重复对象键。JSON 字符串保留原始 Unicode 内容，键排序遵循 RFC 8785。声明中的整数需要精确表达时限于安全整数范围，更大整数和高精度十进制使用带类型的字符串，避免解析时损失精度。规范化规则升级生成新的摘要算法版本。Data 字节 checksum 继续按原字节计算。语义修订 ID 和 checksum 与字节版本共同冻结。规范化选用 [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785)。

## 声明结构

算子声明使用 `operator-manifest.v2`。基础字段包括身份和摘要、标题与说明、领域与分类、运行环境及资源、输入和输出端口。分类与语义可用于 Agent 检索和编译器校验。目标产物类型从输出端口推导。

| 扩展字段 | 何时填写 | 省略时的含义 |
| --- | --- | --- |
| `intent` | 需要额外检索关键词、适用性和局限说明 | 可根据基本描述和输出端口检索 |
| `parametersSchema` | 算子接受参数 | 算子无参数 |
| `portSemantics` | 某个输入要求字段映射、单位、基准、粒度或形状 | 该端口只有声明的结构约束 |
| `parameterPolicies` | 参数允许数据、知识、推导、上游值或算子默认值等来源 | 参数值由用户提供 |
| `constraints` | 需要额外检查跨端口关系或内容 | 无附加谓词 |
| `outputSemantics` | 需要声明输出的固定、继承、变换或实测事实 | 编译器只推导端口结构，输出语义保持未知 |
| `executionPolicy` | 远程执行，或需要声明重试、确定性、缓存及额外环境约束 | 本地离线校验只接受调用声明。执行接入须明确解析运行政策 |
| `conformanceEvidence` | 要声明可组合、可执行或缓存适用性 | 无相应验收证据 |

`tags`、`errorCodes`、`examples` 可按实际内容补充。普通算子只填写有实际约束的扩展字段。涉及科学或业务含义的输入要求仍须完整声明。

描述文本 SHOULD 说明该能力能完成的具体操作，使用者需要提供的事实和输出的可解释范围。编译器 MUST 使用结构化字段作硬约束判断。能力名称、自然语言说明或模型推测不能覆盖契约条件。

## 端口类型与基数

每个端口 MUST 声明 `kind`、`logicalType` 和格式或值约束。只有可选端口或集合端口需要显式声明 `cardinality`。以下集合和值端口的规则在使用对应特性时适用。

| 内容 | 规范 |
| --- | --- |
| `kind` | `artifact` 或 `value`。artifact 通过受控数据引用传递，value 使用有界 JSON Schema |
| `logicalType` | 首版含 table、vector、raster、tensor、model、document、image、record。逻辑类型决定适用的语义字段 |
| 格式 | artifact 声明明确的 format 与 mediaType 对，兼容性按可能输出集合验证 |
| `cardinality` | `minItems`、`maxItems`、`ordered`、`duplicates`。省略时为 1/1。可选单文件为 0/1，集合显式设置边界。顺序默认保留，重复项默认拒绝 |
| 选择器 | XLSX sheet、GeoPackage layer、raster band 等 MUST 显式声明，单文件容器里的多层内容与多个文件分别表示 |
| 值端口 | 明确 JSON 类型、大小限制和来源。首版跨节点 value 支持布尔、有限数值、字符串及有界同类型数组，其他类型通过 record artifact 或后续 feature 支持 |
| 空与缺省 | 端口缺席、空集合、零行表、null 值、NoData MUST 分别规定行为 |

集合按规范顺序冻结。无序集合只有在算子声明排列不影响结果并通过测试后才可在规范化时排序。调用 manifest 的输入文件是不可变数据，Worker 生成新输出并记录来源。

## 数据语义

语义由能力的要求、Data 当前可验证的事实和编译器推导结果共同构成。每个用来证明硬约束的事实 MUST 绑定来源、精确版本或语义修订、校验范围与产生者。事实状态为 `known`、`unknown` 或 `conflict`。调用方声明、文件结构读取和全量内容校验分别保存其来源类别。

| 类型 | 按用途必须描述的语义 |
| --- | --- |
| table | 字段数据类型、角色、量的概念 quantityKind、unit、允许空值、主键或唯一性、粒度、排序、字段名与角色的绑定 |
| vector | 几何类型、维度、CRS、轴顺序、单位、属性字段以及拓扑前提 |
| raster | band 意义和单位、CRS、transform、宽高、分辨率、NoData 和 mask 政策、分类或连续数值 |
| tensor | dtype、带名字的维度、shape、特征顺序、标签、归一化状态与 sampleIndex 关系 |
| model | methodId、结构摘要、权重格式、输入特征顺序、输出目标、预处理引用、训练数据范围、环境和随机状态 |
| document | 格式、语言或编码需求、页或块位置、抽取方法与截断状态 |
| image 与 record | 媒体类型或 JSON Schema，加上解释输出所需的单位、图例或业务字段含义 |

时间数据还 MUST 按算法需要声明时间字段、时区、日历、瞬时或区间值、区间开闭、频率与聚合方法。空间数据按算法需要声明垂向基准。默认时区、同名字段和同一物理单位都不能自动证明口径相同。

`quantityKind` 使用版本化概念 ID，例如地下水水头和距参考面的深度使用不同 ID。领域词表由领域包声明，Kernel 按统一规则处理。单位先解析 UCUM，再检查 quantityKind、垂向基准、浓度基准和空间时间口径。摄氏度等带偏移换算必须使用对应变换定义。[单位规范](https://ucum.org/ucum)

采样结果可用于候选发现。对全量唯一性、完整性、数值范围等要求，证明材料 MUST 覆盖全部输入，或在节点执行前由相应验证器完成检查。未知事实允许保留在草稿中，执行前的具体处置按后文检查阶段执行。

事实至少包含 `subjectRef`、`property`、`state`、`value`、`sourceRef` 与 `verificationScope`。unknown 省略 value，conflict 保存相互冲突的事实引用。源文件结构、用户声明和验证器结果发生冲突时保留冲突，由拥有该事实的服务产生新的修订。Agent 可以提出修订建议，生效必须有显式来源。

## 参数与字段绑定

参数 MUST 用 JSON Schema 2020-12 表达类型和局部约束。Schema 引用必须在装配时从锁定文件解析，运行时校验使用已打包的定义。超出受支持 Schema 子集的声明必须拒绝并说明关键字。[JSON Schema 规范](https://json-schema.org/draft/2020-12/json-schema-validation)

| 参数来源 | 允许条件 |
| --- | --- |
| USER | 绑定当前目标修订中的用户选择 |
| DATA | 绑定数据或语义修订的明确字段及回执 |
| KNOWLEDGE | 绑定证据版本、位置和适用条件，采用时记录选择理由 |
| OPERATOR_DEFAULT | 默认值存在、类型合法，且声明允许在当前条件下采用 |
| DERIVED | 使用版本化、受支持的纯规则与已知事实推导，保存全部依赖 |

上述来源逐一映射到现有参数来源记录，DERIVED 为新增来源。多个来源冲突时返回诊断，由明确的用户选择或声明的条件规则解决。隐式覆盖顺序不得改变已确认方案。

字段角色 MUST 由参数或独立规则文件映射到真实列名。参数政策区分固定算法定义、可采用的工程默认和需要领域证据的选择。例如算法定义中的总体方差计算可以明确固定，补给量、渗透系数、评价权重和目标坐标系需要各自的来源。声明允许采用的默认值在冻结定义中写成具体值，保持可重放。

## 前置条件与结果保证

当端口格式和参数 Schema 无法表达必要条件时，采用有限、版本化的谓词。谓词参数只包含常量、端口引用、字段角色、参数引用或受控事实路径。条件组合支持 `all`、`any` 和显式 `when`，使用三值逻辑。

all 中任一 false 得到 false，全部 true 得到 true，其余为 unknown。any 中任一 true 得到 true，全部 false 得到 false，其余为 unknown。when 的条件为 false 时跳过该条件，为 unknown 时保持 unknown。检查结果另外保存 skipped 原因。用户输入不能携带可执行谓词代码，新增谓词随受信验证器包发布。

| 谓词组 | 示例 | 典型证据 |
| --- | --- | --- |
| 结构 | fieldExists、dtypeCompatible、uniqueKeys、tensorShape | 文件 schema、全量检查回执 |
| 量与口径 | sameQuantity、unitCompatible、sameDatum、sameMeasurementBasis | Data 语义修订 |
| 空间 | sameCrs、sameGrid、metricCrs、geometryAllowed | 空间元数据及检查回执 |
| 时间 | timeAxisPolicy、calendarCompatible、nonOverlappingIntervals | 数据范围和全量时序检查 |
| 模型 | featureOrderMatches、preprocessorMatches、trainingDisjointFromEvaluation | 模型与预处理 artifact 的内容身份及训练来源 |
| 内容 | finiteValues、minimumRows、coverageAtLeast | 全量内容检查或节点检查结果 |

每条条件 MUST 声明 `id`、`predicate`、`arguments`、`severity`、`checkStage` 和可用检查器身份。严重性为 `error` 或 `warning`。未知谓词或未安装验证器不能获得符合性通过。

格式、必需字段、算法定义所需单位与基准、模型结构和输出完整性属于 error。warning 用于声明为可接受的质量退化及解释限制。generic 分类本身不能降低硬条件的严重性。谓词的初版行为至少固定如下规则。

| 谓词 | 确定性判断 |
| --- | --- |
| sameGrid | 规范化 CRS 相同、尺寸相同、六参数 transform 逐项按固定比较政策一致。容差及单位属于谓词版本或显式参数，像元重采样进入图 |
| sameQuantity | 版本化 quantityKind 相等，或存在该任务接受的显式等价声明 |
| unitCompatible | 单位可解析且换算关系明确，同时满足量与口径约束。需要换算时产生 ADAPTER_REQUIRED |
| featureOrderMatches | 规范化后的特征身份、顺序及 dtype 满足模型要求 |
| trainingDisjointFromEvaluation | 比较精确训练、拟合和评估样本范围，在方法声明的隔离维度上交集为空。只有时间范围而方法要求实体隔离时保持 unknown |

| 检查阶段 | 处理方式 |
| --- | --- |
| compile | 用声明和当前事实求值。硬条件为 false 时拒绝该图，为 unknown 时列出缺项 |
| beforeNode | 允许内容相关条件延迟到输入存在时检查。图中必须冻结对应检查及失败策略，在实际执行目标算子前通过 |
| afterNode | 检查输出结构和结果保证，下游消费前通过 |

编译报告逐项记录 `PASS`、`FAIL`、`UNKNOWN` 或 `DEFERRED`。UNKNOWN 只有在注册检查器能在指定阶段求证时才可改为 DEFERRED。输入的科学含义、用户意图与方法选择必须在冻结前解决。运行时检查失败终止受影响节点及其下游，并返回原因。

输出语义支持固定事实、从输入继承事实、按受控规则变换事实，以及运行后测得事实。每条传播规则 MUST 指定来源字段和覆盖范围。行筛选可以保留列单位，聚合改变粒度，标准化改变尺度与单位表达，重投影改变 CRS，重命名更新字段映射。信息已被删除或尚未知的事实不能从上游继续传播。

条件输出按所有可能分支检查。只有分支由冻结参数确定，或各分支均满足下游要求时，连线才通过。首版以确定分支为主，无法确定的运行时分支返回 feature 诊断。

## 连线兼容与显式适配

一条边只有在上游结果保证足以满足下游全部输入要求时才直接兼容。格式集合使用包含关系校验，字段、单位、粒度和状态模型逐项求值。对一般 JSON Schema 的任意蕴含关系不作全功能假设，首版使用明确支持的结构子集或相同 schema 身份，其余情形要求显式检查器。

| 结果 | 语义 | 后续动作 |
| --- | --- | --- |
| DIRECT | 已知要求全部满足 | 直接连接 |
| ADAPTER_REQUIRED | 存在已安装的转换能力及可确定参数 | 展示候选转换，加入图后重新编译 |
| INFORMATION_REQUIRED | 单位、基准、字段角色或选择尚缺 | 给出精确缺项 |
| INCOMPATIBLE | 已知语义冲突或转换无法满足 | 排除该连线并给出原因 |

转换包括格式、字段映射、单位、坐标系、网格与时间聚合。信息损失、插值、精度变化、NoData 政策和目标范围 MUST 进入方案与授权内容。特定算法本身包含的固定预处理可以在该算子契约内声明并测试。跨算子自动加入的处理必须作为图节点显示。

## 工作流定义

工作流 MUST 包含精确能力引用、声明输入、节点依赖、输出选择和发布配置。采用参数时必须记录参数与来源。涉及结果语义或运行检查时，声明目标要求与相应检查。循环、悬空端口、重复身份、未使用绑定和未声明最终产物产生稳定诊断。

目标要求逐项包含产物角色、期望逻辑类型、需要满足的语义、对应输出端口与验收检查。同一算子可在不同 nodeId 上重复使用，图内 nodeId 必须唯一。资源与搜索预算取自部署或用户选择，实际估算及缺失值明确记录。

基础连接语法使用 `workflowInput`、`nodeOutput` 和 `workflowParameter`。集合与值绑定仅在消费者支持相应特性时启用。可选输入的缺席必须显式表达，集合元素带稳定次序。value 到 parameter 的绑定须得到目标参数声明允许，通过类型校验，并明确消费时点。

子工作流按精确引用展开为有限 DAG，保留外层端口映射、原始引用和展开后的摘要。递归引用被拒绝。动态 scatter 和条件分支需要对应的 feature 实现及验收，消费者 MUST 拒绝不支持的 requiredFeatures。

同一 workflowParameter 被多个节点使用时，其值必须同时满足所有节点的参数约束。目标输出必须绑定实际生产端口，viewer 和发布媒体类型由编译结果核对。数据集合元素的身份和顺序全部进入定义摘要。

## 编译与执行结果

编译器输出冻结图、推导语义、检查报告、缺项、转换说明、资源需求与定义摘要。状态包含 INVALID、INCOMPLETE、VALIDATED。VALIDATED 要求所有硬条件已 PASS 或具有合法 DEFERRED 检查。执行授权单独绑定 VALIDATED 的精确内容。

诊断 MUST 包含 `code`、节点、端口或参数路径、expected、actual、事实来源和 repairOptions。repairOptions 描述明确的数据补充或候选算子，实际修改图后重新编译。

建议首批诊断码包括 `SEMANTIC_QUANTITY_MISMATCH`、`UNIT_UNKNOWN`、`VERTICAL_DATUM_MISMATCH`、`GRID_MISMATCH`、`FIELD_ROLE_UNBOUND`、`COLLECTION_CARDINALITY_INVALID`、`MODEL_FEATURE_ORDER_MISMATCH`、`TRAINING_EVALUATION_OVERLAP`、`PARAMETER_SOURCE_MISSING`、`FEATURE_UNSUPPORTED` 和 `OUTPUT_CONTRACT_VIOLATION`。与现有错误码相同含义的项目沿用现有码并增加诊断字段。

Executor MUST 重新校验提交主体、项目、授权绑定、定义 checksum、装配目录、数据引用和资源上限。Kernel 和 Executor 使用固定版本的共享编译器和同一组语义规则，规则集合不一致时拒绝提交并报告装配差异。

执行授权绑定执行定义摘要和完整参数。相同请求重放复用同作业，变更请求内容返回冲突。部分节点失败允许按声明政策恢复，已成功节点的输出只有在 checksum、来源和环境条件仍满足时才能复用。替换算子、变更参数或输入产生新定义。

## 运行行为与溯源

| 属性 | 要求 |
| --- | --- |
| 副作用 | executionPolicy 声明只生成新产物或委托服务产生作业。外部副作用按服务绑定及恢复协议执行 |
| 幂等 | 指明节点尝试、外部任务协调和发布的幂等身份，已发生的外部提交先协调再重试 |
| 确定性 | 声明 bitwise、tolerance 或 nondeterministic，并给出环境、种子和比较方法 |
| 缓存 | 只有可重用政策与验证证据均满足时启用。键覆盖定义、输入字节和语义、参数、环境、模型状态及随机种子 |
| 资源 | 声明超时、内存、设备及并行要求，执行受部署资源预算约束 |
| 输出 | 执行后核验端口、格式、内容结构、摘要和结果保证，下游仅消费已验证输出 |
| 发布 | 最终结果经校验及溯源记录后原子公开，失败记录可诊断 |

记录 workflow 与 node 身份、attempt、输入版本、语义修订、参数来源、实现与环境、运行时间、检查报告和输出版本。可映射到 PROV 的 Entity、Activity、Agent 及 used、wasGeneratedBy、wasDerivedFrom 关系。[PROV-O](https://www.w3.org/TR/prov-o/)

## 声明与连线示例

以下节选来自表格筛选样例。该算子绑定用户指定的字段，保留已有字段和单位，并在运行后测量行数。完整声明同时包含端口、身份、资源和参数 Schema。[表格筛选样例](../../source/packages/workflow-compiler/tests/fixtures/composition/sample.table.filter.json)

```json
{
  "portSemantics": {
    "data": {
      "requiredFacts": ["fields"],
      "fieldBindings": {"value": {"parameter": "column"}}
    }
  },
  "outputSemantics": {
    "data": {
      "inherit": [{"inputPort": "data", "facts": ["fields", "quantityKind", "unit"]}],
      "measured": ["rowCount"]
    }
  }
}
```

训练与推理形成如下契约链。

```text
精确训练数据 → scaler 拟合 → scaler artifact
训练数据 + scaler → 标准化 → 训练张量 → 模型训练 → 权重与 modelSpec
评估数据 + 同一 scaler → 标准化 → 推理张量 + 权重 → 预测
预测 + 目标 scaler 映射 → 逆标准化 → 同量同单位评估
```

缩放器的训练范围、输入特征顺序与模型的引用必须一致。对验证集单独拟合并直接连接既有模型时，应在 preprocessorMatches 中检测身份不一致。合法的迁移学习如果需要不同预处理，必须在方法契约中明确接受关系，避免以一条通用限制排除有效方法。

栅格集合组合使用显式 collection，例子中两个来源均为上游 artifact。

```json
{
  "inputs": {
    "rasters": {
      "collection": [
        {"nodeOutput": {"nodeId": "alignedA", "port": "raster"}},
        {"nodeOutput": {"nodeId": "alignedB", "port": "raster"}}
      ]
    }
  }
}
```

目标栅格统计算子的真实端口名称和集合上限由 manifest 决定。编译器对集合每个元素检查 format、量、单位和网格，对未知网格冻结运行前检查。只有已声明的对齐策略才能生成 alignedA 和 alignedB。

## 符合性与测试

| 等级 | 必备材料与行为 |
| --- | --- |
| described | 身份、调用契约、用途、分类与局限完整，通过 Schema 和引用校验 |
| composable | 增加全部适用语义、前后条件、参数来源、输出传播，至少一个有效组合和针对关键约束的失败组合 |
| executable | 增加目标运行环境上的真实执行、输出检查、资源和失败恢复验证，以及可核验的授权与服务绑定 |

每个 generic 声明 SHOULD 用独立字段命名或独立场景验证复用性。具体方法可以限制输入结构和假设，只要限制完整声明并有验证证据。domain 和 scenario 能力遵循相同的组合检查。

必须覆盖结构测试、关键语义反例、输出传播、确定性或容差、旧版本读取、并发与幂等、集合边界、端到端目标产物。模型类增加特征次序、预处理身份与训练评估范围测试。测试数量按条件覆盖确定。

验收工具产出定义摘要、验证器集合摘要、测试版本、环境、通过项、失败项与适用范围。目录符合性由这些材料计算。调用实现、语义或参数政策变更后重做受影响的检查。

## 版本迁移与标准维护

旧 manifest 通过版本化适配器进入统一目录。调用契约已有事实可直接映射，缺少的语义保留 unknown。补充旧版本语义时，可使用单独版本化的组合描述，严格引用原 manifest 和实现摘要，并将自身身份加入装配锁。新发布 v2 则采用新的算子版本。两种路径都必须验证描述与实现一致。既有模型按原发布契约运行，新自主编排入口对所需事实逐项检查。登记覆盖率统计 described、composable、executable 的精确集合，避免用目录总数代替可编排数量。

标准 Schema、词表、谓词注册表、正反样例和版本迁移规则必须同版发布。扩展字段使用明确命名空间并声明消费者是否必须理解。涉及硬约束的未知扩展拒绝使用。新增装配能力包后按内容摘要验证集合，目录大小由配置派生。

## 契约实现与验证

Schema 与校验器随 workflow-compiler wheel 安装。应用调用 `validate_composition` 时提供可信算子目录、词表和环境身份。数据事实和引用来自调用方提供的快照。表格试点接入了 Data、Kernel 和 Executor，运行范围见下文。

| 对象 | 实现 |
| --- | --- |
| 算子 | [operator-manifest.v2](../../source/packages/workflow-compiler/src/platform_workflow_compiler/composition/schemas/operator-manifest.v2.schema.json) |
| 工作流 | [workflow.v7](../../source/packages/workflow-compiler/src/platform_workflow_compiler/composition/schemas/workflow.v7.schema.json) |
| 数据事实与词表 | [composition-fact.v1](../../source/packages/workflow-compiler/src/platform_workflow_compiler/composition/schemas/composition-fact.v1.schema.json)、[composition-vocabulary.v1](../../source/packages/workflow-compiler/src/platform_workflow_compiler/composition/schemas/composition-vocabulary.v1.schema.json) |
| 通用结构与验证报告 | [composition-types.v1](../../source/packages/workflow-compiler/src/platform_workflow_compiler/composition/schemas/composition-types.v1.schema.json)、[composition-validation.v1](../../source/packages/workflow-compiler/src/platform_workflow_compiler/composition/schemas/composition-validation.v1.schema.json) |
| Python 接口 | [platform_workflow_compiler.composition](../../source/packages/workflow-compiler/src/platform_workflow_compiler/composition/__init__.py) |

首版词表由调用方提供精确版本。单位条目声明量纲、比例和偏移，坐标系条目声明是否为米制，物理量采用精确概念 ID。坐标系按词表中的规范 ID 比较。条件用 `condition` 包裹 `predicate`、`all`、`any` 或 `when` 与 `then`。每个叶子谓词的有序参数可引用端口事实、已绑定参数或常量。参数 Schema 支持受控的内联子集，采用带时区的日期时间，最多六位小数秒。字段绑定可以额外要求 dtype、quantityKind 和 unit。

集合按显式顺序连接单项输入或单项上游产物。可选端口用 `{"absent": true}` 表达缺席。条件输出按已冻结的枚举参数选择。节点的标量或有界同类型数组输出可以接入显式接受该来源、且 schema 一致的参数。推导语义当前支持固定、继承、按已绑定参数赋值和按声明执行单位换算。

报告中的 `VALIDATED` 表示通过离线验证。对明确声明为运行后测得的事实，注册检查器可生成 `DEFERRED` 检查。数据含义和参数缺项返回 `INCOMPLETE`，已知冲突或不支持的结构返回 `INVALID`。这份报告可用于下一阶段冻结执行计划。

从 `source/` 运行以下命令。样例为合成契约，检验连线和数据语义。

```bash
python -m pytest -q packages/workflow-compiler/tests/test_composition.py

fixtures=packages/workflow-compiler/tests/fixtures/composition
PYTHONPATH=packages/workflow-compiler/src python -m platform_workflow_compiler.composition \
  "$fixtures/table-positive.json" \
  --operator "$fixtures/sample.table.filter.json" \
  --operator "$fixtures/sample.table.summary.json" \
  --vocabulary "$fixtures/vocabulary.json" \
  --environment "$fixtures/environment.json"
```

换成 `table-negative.json` 可得到 `SEMANTIC_QUANTITY_MISMATCH`，退出码为 1。栅格和模型样例覆盖显式单位转换、集合网格检查、特征次序与训练评估范围。测试还验证默认值来源、未知事实、循环、摘要、Schema 子集和延后校验。[样例与断言](../../source/packages/workflow-compiler/tests/test_composition.py)

## 表格试点

Groundwater 装配从已安装目录显式启用 `table.filter`、`table.sort` 和 `table.profile` 的 1.0.0 版本。Agent 按需求组合 DAG，Kernel 绑定本轮读取的 Data 对象及语义修订，将通过校验的 Workflow v7 摘要写入确认动作。用户批准后，Executor 复核同一份定义与输入摘要，经原有 Native Worker 执行，并由 Data 发布选定的文件输出。[产品装配](../../source/products/groundwater/product-manifest.json) [共享执行适配](../../source/packages/workflow-compiler/src/platform_workflow_compiler/composition/execution.py)

表格试点支持单项文件端口及表格产物发布。筛选和排序规则使用已登记的 JSON 数据版本，结构与字段适用性由对应算子执行时检查。

产品还启用了 `table.project@1.0.0`、`table.deduplicate@1.0.0` 和 `table.validate@1.0.0`。列投影按规则选择、排序、重命名和严格转换字段。稳定排序后，去重按规则键分组并应用显式的保留策略。行级校验分别输出有效行、违规行及违规明细，画像可只消费有效行。三个算子各绑定精确的 JSON 规则版本。缺少必需规则文件时，草稿无法确认。已绑定的规则文件内容由算子校验，规则内容导致作业失败时，Data 无成果发布。

从 `source/` 执行 `python -m pytest -q tests/e2e/test_composed_workflow.py` 可核验从草稿到成果发布。测试使用隔离的 PostgreSQL 和 Native 锁定依赖，默认使用脚本化 Agent。设置 `COMPOSITION_E2E_LIVE=1` 并提供 `DEEPSEEK_API_KEY` 后，可用 `-k table-quality` 让真实 DeepSeek Router 和 Agent 验证新增表格流程、缺规则和无效规则三个场景。[端到端验收](../../source/tests/e2e/test_composed_workflow.py)

## 栅格试点

Groundwater 启用 `raster.reproject@1.1.0`、`raster.align_grid@1.0.0`、`raster.cell_statistics@1.0.0` 和 `raster.reclassify@1.0.0`。逐像元统计接受 2 至 16 份有序 GeoTIFF 或 COG 输入。重分类接受上游 GeoTIFF 和精确的规则文件版本。

用户指定参考栅格后，Agent 查阅各输入版本的物理元数据。CRS 不同的源用 `raster.reproject@1.1.0` 一次转换到参考网格。同 CRS 且网格不同的源用 `raster.align_grid@1.0.0`。已匹配的输入可直接参与统计。两个转换算子都绑定同一精确的参考版本，参考栅格仅提供网格，用户要求参与统计时才使用其像元。需要转换时，缺少参考版本或缺少重采样选择的草稿留待澄清。

| 检查 | 依据 |
| --- | --- |
| CRS | Data 在发布准备时读取文件中的 CRS 标识，优先使用精确权威代码，其他 CRS 使用 WKT 摘要。逐像元统计要求标识相同 |
| 网格 | 两个转换算子复制参考栅格的 CRS、宽、高与六个 GDAL 仿射系数。逐像元统计按零容差检查输入网格 |
| 重采样 | 每条转换分支冻结所选方法和来源。支持 `nearest`、`bilinear` 和 `cubic`。最近邻适用于保留类别编码，后两种用于允许插值的连续数据 |
| NoData | 转换读取选定的源波段及其有效掩膜，输出单波段 Float64 GeoTIFF。源掩膜、非有限值及无来源覆盖的输出按 GDAL 重采样规则处理，无有效结果的位置写 NaN。逐像元统计将有效输入不足的位置写为 NaN。重分类将上游无效像元写为用户指定的 NoData，校验目标类型能否精确表示该值 |
| 复核 | Executor 重新验证冻结图，并以 Data 的精确版本比对物理元数据和文件校验和。Data 发布时读取产物实际元数据 |

文件元数据绑定原始对象摘要，规则中的科学含义和栅格内容检查仍按对应算子契约。历史版本缺少物理元数据时，确认被阻止，可用原文件重新发布。通过校验后，用户确认的摘要涵盖参考版本、转换节点、重采样方式及产物选择。均值与分类结果可同时发布。

端到端测试覆盖已对齐输入、指定参考栅格下的两种转换、未解决的 CRS 或网格冲突、CRS 缺失以及 NoData 越界。参考网格用例检查发布文件的像元值、网格、有效掩膜和输入溯源。用 `COMPOSITION_E2E_LIVE=1` 和 `-k reference-grid` 验证真实 Agent 的编排与执行。[端到端验收](../../source/tests/e2e/test_composed_workflow.py) [Rasterio 重投影](https://rasterio.readthedocs.io/en/stable/api/rasterio.warp.html) [Rasterio 掩膜](https://rasterio.readthedocs.io/en/stable/topics/masks.html)
