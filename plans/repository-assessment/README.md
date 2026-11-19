# Groundwater Agent 全仓库产品定位与架构评估

评估日期：2026-11-14（Asia/Shanghai）。源码基线：`e6d8512797a128887cf2bab41a92629ddb06b668`。状态：**评估交付，等待审批；未实施重构。**

## 先回答九个问题

1. **究竟是什么产品？** 面向地下水科研人员与专业技术人员的任务型研究工作台：自然语言提出问题，利用明确版本的数据、专业方法和可追溯执行交付研究结果。当前已经实现该链条的工程骨架和若干分析方法，尚不能称为完成全科研流程的自主研究Agent。正式产品证据仍以可信单机/小型实验室为中心。见[产品定位](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/00_PRODUCT_POSITIONING.md)，E01–E04。
2. **核心科研工作是什么？** 监测资料整理与语义核实、统计和变化分析、空间水头与简化流场、结果解释及可追溯交付。规则分类/DRASTIC是专题能力；复杂模拟、率定和反演应由高级用户按需启用。它们都服务研究任务，GIS、RAG、MLOps和通用工作流不是产品目标。见[六条业务主线](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/02_CORE_BUSINESS_CAPABILITY.md)。
3. **当前系统是否围绕这些工作组织？** 部分是。Domain选择模型，Agent取证并生成确认，Executor执行和恢复，Data原子发布，Web审阅；权威身份与执行链较明确。欠缺集中在“科研前提是否成立”和“文件产生后是否回答研究问题”。高级/实验能力、固定案例和独立轻量Agent交付面又增加了维护范围。见[Agent评估](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/05_AGENT_ASSESSMENT.md)，E30–E36/E49/E52。
4. **哪些模块没有足够产品价值？** 没有证据支持立即删除任何完整服务。Data内通用实验/模型注册/alias治理，固定19输入深度学习案例，Porous窄形状学习反演，以及独立lightweight-agent-core，都缺少成为核心默认路径的充分依据；真实外部使用量UNKNOWN。应先隔离默认入口、明确owner和使用证据。见[模块减负](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/07_MODULE_SIMPLIFICATION.md)。
5. **哪些能力重复建设？** 显式prepare与提案边界维护两套不等价检查；同一模型的机械参数/输出在Manifest、instructions、card和UI重复维护；取证/准备有重复描述和样本查询；多个前端存在可复用行为。Dataset/Version/Semantics、Data/Knowledge、Task/DAG、Kernel/Gateway则承担不同职责，不宜按名称相似合并。见[去重决策矩阵](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/08_REDUNDANCY_DECISION_MATRIX.md)。
6. **哪些功能应该收缩？** 收缩默认加载的高级/实验模型、纯Demo检索范围、MLOps默认入口、冗长计划文字与重复说明。保留用户批准、精确版本、证据读取、沙箱、执行恢复和原子发布。旧版本/未引用算子先查历史消费者，不依据引用数删除。见[领域能力评估](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/06_DOMAIN_WORKFLOW_ASSESSMENT.md)。
7. **哪些地下水科研能力不足？** 必需单位/垂向基准/井层位及观测状态的强制前提；质量检查执行回执；统计显著性与自相关/季节/采样前提的联动；独立数值和现场验证；来源精确定位；读取数值诊断后的解释和最小可复现报告。完整水量平衡、井试验、水化学/检出限等不是当前闭环，也不应一口气全部新增。见[Data](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/03_DATA_ASSESSMENT.md)、[Knowledge](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/04_KNOWLEDGE_ASSESSMENT.md)。
8. **最精简合理的系统架构是什么？** 保留现有Kernel、Data、Knowledge、Executor四个状态所有者，以及薄Gateway和研究工作台；Groundwater Domain保持版本化声明/方法集合，Agent Intelligence先仍归Kernel。六个逻辑职责不变成六个新服务。远程模型按需部署，核心路径优先复用现有算子和合同。见[目标架构](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/09_TARGET_ARCHITECTURE.md)。
9. **如何以最小改造成本实现？** 先A01统一准备/提案检查，A02约束关键物理前提，A03补统计适用性；随后闭合质量、解释、报告和知识分层；再按实际测量缩工具/重复读取/默认能力。多数任务不需要先迁库、迁仓或重写服务。见[改造路线](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/10_REFACTORING_ROADMAP.md)和[24项实施任务](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/11_IMPLEMENTATION_ROADMAP.md)。

## 最需要先处理的证据

**确认前的科研输入验证并不统一。** 在无显式`prepare_task_inputs`的合成工具轨迹中，来源高程单位为ft、提案参数为m，`prepare_interruption`仍返回一个Action及READY，检查仅含DATA_IDENTITY。纯函数探针已复现；没有调用真实Provider、服务或数据库，也不代表可以绕过用户授权。即使显式prepare，部分关键缺项仍以UNKNOWN进入READY。这使“执行可追溯”不能直接等同于“研究前提有效”。[源码与调用链E32/E33](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/SOURCE_INDEX.md)，[探针结果](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/preparation-boundary-probe.json)。

## 本轮重算与验证

| 范围 | 当前事实 | 不可据此推断 |
|---|---|---|
| 仓库 | 2,139个已跟踪文件；完整文件/符号/测试定义索引 | 不表示逐行完成所有代码的形式化证明 |
| 当前Domain 6.2.0 | 18模型、18Workflow、1Execution Case、7Observation Tools | 不等于18个独立成熟科学模型 |
| 算子 | 113版本条目、111个ID；native86/qgis15/remote_http12；54条目被当前Workflow引用 | 其余59条目不是自动删除对象 |
| 当前知识 | 9资产、121文档、321单元、44个唯一sourceId | 声明和引用完整不等于正文科学认证 |
| 测试静态盘点 | Python测试定义2,131个/254文件；JS/TS测试文件68个 | 定义数不是参数化收集数或通过数 |
| 根pytest收集 | 2,287项，仅收集 | 不含所有独立包/算子/第三方测试套件，未全量执行 |
| 本轮选定执行 | 495项合同/准备等检查最终通过，另45项算子检查通过，共540项；两项环境设置问题修正后重跑通过 | 不是全套发布验收、真实服务端到端或现场科学验证 |

详见[测试证据与失败记录](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/TEST_EVIDENCE.md)、[审计覆盖与限制](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/AUDIT_COVERAGE.md)。未核实的使用量、活动数据库状态、实际召回质量、真实场地准确度一律UNKNOWN。

## 阅读入口

上述00–11为主体。另提供以下可核查附件：

- [逐模块仓库盘点](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/01_REPOSITORY_INVENTORY.md)、[源码/符号/合同/测试/调用链索引](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/SOURCE_INDEX.md)。
- [全部算子分类](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/OPERATOR_MATRIX.md)、[全部模型与Workflow](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/MODEL_WORKFLOW_MATRIX.md)、[全部当前知识文档与来源](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/KNOWLEDGE_INVENTORY.md)。
- [算子源码与测试导航](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/OPERATOR_TEST_INDEX.md)、[模型/工作流/知识卡/测试连接](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/MODEL_EVIDENCE_DETAIL.md)。
- [外部一手来源抽查](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/EXTERNAL_REFERENCES.md)、[进度](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/progress/PROGRESS.md)、[评估决策](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/progress/DECISIONS.md)、[风险与未知项](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/progress/RISKS.md)。

证据等级始终分开：**源码实现 / API公开 / Manifest声明 / 测试通过 / 真实运行验证 / 科研结果验证**。本报告是评估和待审批方案，不授权修改任何生产代码、数据库、知识资产或公开合同。本轮到此交付并停止。
