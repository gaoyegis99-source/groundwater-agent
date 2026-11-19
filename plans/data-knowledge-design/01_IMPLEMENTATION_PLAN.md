# 第一批实施设计计划

## 范围
包含：Data v3 现有 Dataset、DatasetVersion、DataObject、Field、SemanticRevision、QualityRun 的审计和增量设计；地下水监测数据最小语义 Profile；文件质量、规则质量、科研适用性边界；Knowledge Next 文档、来源、单元、版本、生命周期、Binding、Evidence 治理；正式知识与 Demo/Template 检索隔离；检索评估设计。

不包含：Kernel、Agent Runtime、Prompt、Planner、Executor、前端；新建服务/数据库/向量库/GraphRAG；更换 Embedding 或默认 Reranker；修改公共 API、数据库、Manifest；自动导入/删除/覆盖正式知识；生产写操作。

## 任务
| ID | 任务 | 输出 | 依赖 |
|---|---|---|---|
| D1 | 核对 Data 模型/API | Data 现状映射 | 无 |
| D2 | 地下水最小 Profile | Profile 草案 | D1 |
| D3 | 质量层次和执行证据 | Quality 设计 | D1,D2 |
| K1 | Knowledge 文档和来源盘点 | 资产清单/重复矩阵 | 无 |
| K2 | 分类与来源治理 | 治理规则 | K1 |
| K3 | 检索范围策略 | Retrieval Policy | K1,K2 |
| I1 | Data/Knowledge 边界核对 | 边界图 | D2,D3,K2,K3 |
| A1 | 验收矩阵 | 测试和验收标准 | 全部 |

## Additive 数据库原则
先复用已有字段和 JSON 扩展点；只有证明现有结构不足才提新增 nullable 字段或新表。禁止重命名、删除、收窄或改变旧字段语义；禁止重写既有版本身份；禁止自动回填未经证实的语义。迁移须可前向、可回滚且不丢失旧数据。Schema 变更单独 ADR，未经批准不实施。

## 门禁
每项提案映射到真实源码和合同；说明复用点、兼容行为、未知值处理、测试、迁移和回滚。设计完成后停止等待批准。
