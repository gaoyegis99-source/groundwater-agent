# 审计覆盖与证据等级

## 覆盖方式

本轮完成用户要求的全仓**产品、模块、合同、领域能力和减负评估**。全量清单与关键路径审查相结合，不声称对2,139个文件逐行完成安全/数学证明。

| 范围 | 实际完成 | 证据 |
|---|---|---|
| 全部Git跟踪文件 | 路径、模块、大小、行数、SHA256；Python AST符号与测试定义 | files.json、inventory.json、SYMBOLS.tsv、TEST_FUNCTIONS.tsv |
| Product/Domain当前装配 | 当前Manifest、领域instructions、产品UI和主要设计/合同对照 | E01–E04/E54 |
| 全113算子版本 | Manifest逐项解析、端口/runtime/静态实现/Workflow引用/smoke分类；重点科研实现审查 | OPERATOR_MATRIX、operators.json；E40–E47 |
| 当前18模型/18Workflow/1Case | 全量JSON、绑定、节点依赖、输出、专业前提；18能力逐项判断 | MODEL_WORKFLOW_MATRIX、models/workflows.json |
| 9知识资产 | 全部121文档321单元结构与正文载入、引用/分块/精确重复扫描；标题摘要核阅、专业内容定向核阅 | KNOWLEDGE_INVENTORY、knowledge_assets.json；E20–E24 |
| Agent/Kernel | Router→Context/Tools→准备→提案→确认→执行→续接；状态所有权和纯函数探针 | E30–E36/E51/E54 |
| Data/Knowledge | 数据模型、发现/读取/质量/发布/实验治理，检索/绑定/版本/生命周期 | E10–E24 |
| Executor/部署 | DAG/lease/checkpoint/发布/恢复，Gateway/装配/topology/XDG | E03/E50/E53 |
| 前端/交付 | 产品UI规范、展示/状态派生、Data与Knowledge管理界面、Task review/成果与研究包 | E04/E49/E51 |
| 实验及独立交付 | Porous、Transfer、MODFLOW6/Pastas spike、lightweight-agent-core依赖和产品价值 | E45–E48/E52 |
| 科学/测试 | 选定合同和专业算子检查；来源抽查与科学前提核验 | TEST_EVIDENCE、EXTERNAL_REFERENCES |

9资产全文已载入用于结构/引用和重复检查，**不等于对321单元逐句进行了专家认证**。当前未发现“精确文本重复”，不推导“语义无重复”。未跟踪的私有vendor、用户文件、活动数据库和密钥没有读取，不纳入文件哈希盘点。

## 六种成熟度分别记录

| 等级 | 可接受的证据 | 不能升级成 |
|---|---|---|
| 源码已实现 | 可定位函数/实现和调用链 | 服务当前健康或输出正确 |
| API已公开 | 当前HTTP/tools schema及路由 | 已有实际消费者 |
| Manifest已声明 | Product/Domain/catalog选择及绑定 | 实际执行完成 |
| 测试已通过 | 本轮明确命令、环境、exit、日志 | 全发布验收或专家认可 |
| 真实运行已验证 | 真实服务/输入/运行ID与可核回执 | 现场科学准确性 |
| 科研结果已验证 | 独立参考、适用条件、误差/守恒/泛化/现场复核 | 无条件适用其他场地 |

本轮主要覆盖前四类的特定范围；后两类不统一授予通过。本报告的UNKNOWN保留到有独立证据，不以仓库旧报告、来源标签或测试总数填补。

## 分类可信度

KEEP/MERGE/SHRINK等是根据当前产品目标、源码耦合和维护面提出的建议。未测得真实使用量或工时，成本只作相对判断。算子主分类用于审阅，不取消其他合法使用场景；未被Workflow引用、同名不同版本、两份持久快照，都不是删除充分条件。

所有重要结论通过E编号接到SOURCE_INDEX的文件/符号、合同、测试和调用链。符号索引提供完整定位；测试关联表示测试存在，只有TEST_EVIDENCE列出的范围才标本轮通过。科学、法定标准、生产安全和市场定位的外推均受上述边界约束。
