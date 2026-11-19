# Groundwater Agent — Data + Knowledge 第一批设计包

批准边界：仅 Data + Knowledge；数据库 additive only；design-first。本包是设计草案，不是已批准 API/Schema，不实施代码或迁移。

目标：在现有 Data v3 上补足地下水数据语义与质量证据；在现有 Knowledge Next 上治理知识内容、来源、适用范围和检索隔离。复用现有服务，不新建数据库、服务、向量库或第二套 RAG。

文件：
- 01_IMPLEMENTATION_PLAN.md：范围、依赖和门禁
- 02_DATA_PROFILE_DESIGN.md：地下水监测 Profile
- 03_DATA_QUALITY_DESIGN.md：质量层次与回执
- 04_KNOWLEDGE_GOVERNANCE_DESIGN.md：知识治理
- 05_RETRIEVAL_POLICY_DESIGN.md：检索策略
- 06_ACCEPTANCE_MATRIX.md：验收矩阵
- 07_CODEX_DESIGN_PROMPT.md：Codex 执行提示词
