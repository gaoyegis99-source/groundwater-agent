# Knowledge 内容与来源治理设计

## 目标
复用 Knowledge Next 的 Document / Version / Unit / Source / Lifecycle / Binding / Evidence，不新建知识服务或知识图谱。

## 最小分类
- METHOD：原理、公式、假设、输入条件、限制、解释
- MODEL_CARD：模型用途、输入输出、参数和实现约束
- STANDARD：标准/规程版本、条款、有效期
- DATA_DICTIONARY：项目或数据字段语义
- CASE_STUDY：真实或明确标记的合成案例
- TROUBLESHOOTING：症状、诊断、原因和处理
- DEMO_TEMPLATE：演示教学内容，默认不进入正式科研检索

先映射现有 metadata，避免重复字段。

## 职责去重
Model Manifest / Operator Contract 是机器参数、输入输出的权威来源。Method Knowledge 维护科学原理、假设、适用条件和限制。Model Card 解释模型用途和操作语义，引用而不复制完整理论。Case Study 记录案例。Demo Template 不得被当成真实项目事实或权威依据。

## 来源定位
核心知识尽量补齐机构/作者、标题、版本/出版信息、发布日期、获取日期、章节/页码/条款 locator、稳定 URL/标识、checksum、核验时间。未核实的题名、版本、页码不得编造。

## 内容质量
每个方法单元尽量自洽：定义、前提、步骤、参数、限制、输出解释和来源。公式、变量定义和单位保持在同一单元或可追溯相邻单元。

## 生命周期
保留历史版本。过期/撤回/替代知识不得进入默认有效结果，但允许授权历史审计读取。正式知识与 Demo 检索隔离优先通过现有范围过滤实现。
