# 专项维护：Operator、Workflow、Profile 与 Result Contract

本提示词不是主线阶段，仅在新增计算能力、版本升级、执行缺陷或科学验证缺口时运行。

不重写 Operator Pack 或 Workflow Compiler。精确 pin operatorId@version、Workflow/Model/Profile/Result Contract 身份。技术验证、科学复核、领域适用性和业务验收分开记录。修改结果结构时同步 Contract、Viewer、Data publication 和历史兼容策略。

检查 Manifest、implementation lock、runtime mapping、technical receipts、scientific review、Workflow DAG/ports/formats/parameters、Golden 和真实案例、Profile applicability/input roles/limitations、Result Contract named outputs/qualitySource/viewer/publication，以及 Assembler/Kernel/Executor 一致性。

验收按风险执行 Schema/Unit/Smoke/Integration/Domain Case/Regression/专业复核。没有真实科学证据时明确标记未验证。执行成功不代表科学正确。
