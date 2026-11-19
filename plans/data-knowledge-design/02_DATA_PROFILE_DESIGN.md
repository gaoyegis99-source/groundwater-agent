# 地下水数据 Profile 设计草案

> 逻辑设计，不是已批准 Data Schema。

## 目标
保存地下水数据的来源声明、字段语义、单位、时间/空间参考和核验状态。Data 不替 Agent 判定数据是否适合所有研究任务。

## 首版范围
仅聚焦地下水监测观测表：
- 监测对象标识（井/监测点）
- 观测时间
- 观测变量
- 观测值及单位
- 位置引用（可选）
- 高程/垂向基准（按变量需要）
- 来源和测量方法（如有）

含水层、筛管层段、井结构、抽水试验、水质样品作为后续扩展，不在首版强制要求。

## 语义映射
优先复用现有 Field / SemanticRevision，不另建字段注册中心。建议表达：
- fieldId：精确字段身份
- concept：受控概念标识
- displayName / definition
- quantityKind / role
- unit
- source / sourceRef
- applicability
- verification 状态及依据

以上逐项与现有合同对照，已有字段不重复增加。

## 禁止的自动推断
`water_level` 不自动等于水位高程；`depth` 不自动等于地下水埋深；`elevation` 不自动等于井口或地面高程；`head` 必须明确水头定义和基准。垂向基准未知时不计算绝对水头；CRS 未知时不执行依赖坐标系的距离/面积运算。单位转换要求物理量语义一致且规则明确。

## 核验状态建议
DECLARED、INFERRED、USER_CONFIRMED、SOURCE_VERIFIED、UNKNOWN、CONFLICTED 是设计候选，不代表现有 API 已支持。状态必须有来源和时间，不能只用布尔 verified。

## 身份与继承
Profile 绑定 datasetId + versionId + semanticId；字段映射绑定稳定 fieldId，不只依赖列名。新版本可产生继承候选，但已核验事实不得自动继承为新版本事实。

## 最小实施
先用 SemanticRevision 表达监测 Profile。只有真实样例证明合同不足，才提出 additive Schema 扩展。不新增 Well/Aquifer 微服务或大型领域数据库。
