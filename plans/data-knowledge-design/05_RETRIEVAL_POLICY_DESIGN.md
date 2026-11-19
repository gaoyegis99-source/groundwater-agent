# Knowledge 检索策略设计

## 保留现有技术栈
继续使用 Lexical + Vector + RRF、Evidence Read、Binding 和 Lifecycle。暂不更换 Embedding，不默认增加 Reranker，不引入 GraphRAG。

## 检索范围
- 默认科研任务：有效 METHOD / STANDARD / MODEL_CARD / DATA_DICTIONARY / CASE_STUDY / TROUBLESHOOTING。
- 项目数据问题：优先精确项目和 Data Binding。
- 模型规划：先取当前模型/算子 Model Card，再补方法知识。
- DEMO_TEMPLATE：默认排除，用户明确要求示例/教学时才纳入。
- 历史/过期知识：默认排除，审计时显式请求。

所有过滤字段必须先核对当前 Knowledge API。合同不支持时登记缺口，不假设可以过滤。

## 证据
检索命中不等于证据已读取。只有精确读取成功、身份和生命周期核验通过的 Evidence 才能作为本轮依据。

## 评估
保留原始检索集、评分器和门槛。新增小型扩展集：同主题不同方法、相似术语不同物理含义、Demo/正式知识混淆、过期来源、错误 Binding、多知识组合、方法限制。

指标：Recall@K、MRR/nDCG（标注支持时）、错误知识误召回率、证据读取成功率、Binding 正确率、P50/P95 延迟。

不默认 Query Rewrite、Reranker、GraphRAG 或更大 Top-K；先用评估证明瓶颈。
