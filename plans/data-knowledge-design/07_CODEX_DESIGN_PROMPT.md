# Codex 提示词：Data + Knowledge 第一批 Design-First

项目：`/home/mtd/liweigis/workflow/groundwater-v6`

用户批准边界：
- 仅 Data + Knowledge。
- 数据库 additive only。
- design-first，本轮不实施代码或迁移。

## 执行
1. 检查 HEAD、git status、AGENTS.md，保留所有用户改动。
2. 以当前 checkout 核对 Data v3 和 Knowledge Next 的 Schema、API、模型、迁移、测试和 Domain Package。
3. 阅读本目录所有设计文件。
4. 对每项提案建立“现有字段/合同 → 需求 → 缺口 → 最小增量”映射。
5. 优先复用 Field / SemanticRevision / QualityRun 和 Document / Version / Unit / Source / Lifecycle / Binding / Evidence。
6. 地下水首版只聚焦监测观测数据最小 Profile，不设计全量水文地质数据库。
7. Knowledge 先治理现有资产、来源、职责和检索范围；不自动重写或导入正式知识。
8. Demo Knowledge 逐文档/逐单元判断，不按文件名整包删除。
9. 数据库只提出 additive 方案；不得删除、重命名、收窄或改变旧字段语义。优先 nullable/defaulted 字段；新表必须证明扩展点不足。
10. 每个 Schema/API 变化提供兼容、迁移、回滚和测试设计。
11. 不更换 Embedding，不默认引入 Reranker、GraphRAG 或新服务。
12. 不修改 Kernel、Runtime、Prompt、Planner、Executor、前端。
13. 不执行生产写操作，不运行迁移，不覆盖原始评估集。

## 必须交付
- Data 现有合同映射
- 地下水监测 Profile 最小字段设计
- QualityRun 真实执行证据设计
- Knowledge 资产清单和重复内容矩阵
- 来源与 locator 补齐清单
- Demo/正式知识检索隔离方案
- Model Card / Method Knowledge 职责边界
- Retrieval Policy 和扩展测试方案
- Schema/API 增量提案及兼容性分析
- 风险、回滚和实施顺序

## 停止条件
只完成设计，不修改生产代码、数据库、Manifest 或正式 Knowledge 内容。更新设计包进度后停止，等待用户批准实施。
