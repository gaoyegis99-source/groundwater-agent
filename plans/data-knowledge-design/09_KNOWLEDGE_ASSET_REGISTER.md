# Knowledge 资产、重复内容及来源定位登记

基线 `e6d8512797a128887cf2bab41a92629ddb06b668`。按当前 Domain Manifest 只读解析 9 个资产、121 篇文档、321 个单元、44 个唯一来源。53 个单元带明确 Demo/模板来源，273 个单元没有 locator。所有逐单元用途均为发布前待复核建议。来源字段是仓库声明，外部原文的逐主张核验仍待执行。

对正文做 NFKC 和空白折叠后，得到 0 组完全重复。本页的重复矩阵按可定位的语义重叠记录，不能据此自动删除或合并。

## 资产登记

| 资产 | 内容版本 | 文档 | 单元 | Demo 标记单元 | 原文件 SHA256 |
| --- | --- | ---: | ---: | ---: | ---: |
| [agent-discovery-templates.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/agent-discovery-templates.v1.json) | 1.2.0 | 3 | 3 | 3 | `6d8beb3b6e57fbba6f62ffa8a25157153beee33e9f85c2ab5b0e6440fadfe8a9` |
| [demo-knowledge-interpretation.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-interpretation.v1.json) | 1.3.0 | 15 | 45 | 0 | `9cc72354bb5f083540c2cd1dcd0d2ca2f7f3a1392191f0a0005051bfe6e0faf7` |
| [demo-knowledge-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-methods.v1.json) | 1.4.0 | 15 | 45 | 2 | `56f1846eeb6dc81acb3d2119e5c14d72f0bca8f9c886cafd4f8950045f4f958d` |
| [demo-knowledge-quality.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-quality.v1.json) | 1.2.0 | 15 | 45 | 0 | `3127e4dec5635e01f1a213a3b7639d43c04e925aa233d254d1e91d6120befdf8` |
| [demo-knowledge-scenarios.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-scenarios.v1.json) | 1.2.0 | 15 | 45 | 45 | `50252815011afa72d7e64a46073017bafe7c6faa11584f9d9804b9b9f6a02fe7` |
| [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | 1.2.0 | 30 | 90 | 3 | `1d6709659f68e859b2b0deb29c842db3809b2fea30357c9b3fa96f8ee8624f6e` |
| [model-cards.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/model-cards.v1.json) | 1.1.0 | 10 | 30 | 0 | `be5d7f5f329335d7451f2805611b8fb43027295165261d334ca6c5b47a6c1cf8` |
| [porous-model-cards.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/porous-model-cards.v1.json) | 1.1.0 | 8 | 8 | 0 | `95f86732500257dfd6c348a92184dad22a3b7500b35da664a8c17ef00d02759e` |
| [groundwater-statistical-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/groundwater-statistical-methods.v1.json) | 1.1.0 | 10 | 10 | 0 | `35ec6f5d50c4c8e72256e2bebb548ea87d4099492fc3d8a40143b7472c15d36e` |

## 重复内容与冲突矩阵

单元 ID 由转换器按原数组零基下标产生。合并建议只针对后续受审知识发布。保留旧证据身份和研究限制。

| 主题 | 现有位置 | 重叠或冲突 | 归属与处置 |
| --- | --- | --- | --- |
| 趋势与 MK | gw-trend-methods/1、gw-mann-kendall-cautions/0–2、gw-method-sen-slope/0、gw-model-card-monitoring-trend/0–2 | 重复解释单调趋势、显著性和 `alpha`。Sen 讲斜率，不能等同 MK 检验。 | MK 注意项与 Sen 分别为方法事实入口。移除手写当前默认值的重复维护，模型卡引用冻结的 Model Manifest。 |
| 异常与质量 | gw-trend-methods/2、gw-measurement-quality-control/1–2、gw-quality-flags-qualifiers/0–2 | MAD 异常、测量质量和审核标签都有留痕要求，判断对象不同。 | 保留统计候选、仪器核验和数据状态的三种职责。方法文档互引，不把 MAD 阈值当场地标准。 |
| 时间覆盖 | gw-completeness-design/0–2、gw-model-card-monitoring-trend/1–2 | 期望窗口、缺测和完整率输出重复。completeness/2 含演示门槛。 | 保留方法判断和缺项说明。演示单元只进 Demo。 |
| 水位、埋深、水头 | gw-water-level-head/0–2、gw-measurement-quality-control/0、gw-model-card-flow-field/0–1 | 共同测点、基准和符号条件重复。head/1 有数值示例。 | 保留量的定义和测点前提，由模型卡引用实际计算链。复核混合单元，必要时拆出示例。 |
| IDW 与 Kriging | gw-idw-and-gradient/0–2、gw-idw-validation/0–2、gw-idw-sensitivity/0–2、gw-model-card-kriging/0–2、gw-method-kriging-variogram/0 | IDW 距离幂、邻点数重复。validation/2 声称产品不提供 kriging，与当前装配冲突。 | 方法定义、验证、模型实现分别保留。待核冲突单元先不作为正式证据。模型参数归 Manifest。 |
| Darcy 与简化流场 | gw-darcy-velocity/0–2、gw-darcy-sensitivity/0–2、gw-model-card-flow-field/0–2 | 重复 K、梯度、孔隙度及敏感性阐释。sensitivity/2 含演示情景。 | 保留公式及假设专章，模型卡留具体限制。将情景单元隔离。 |
| 任务选择 | gw-task-routing/0–2、gw-model-card-monitoring-trend/0–2、gw-model-card-flow-field/0–2 | routing/0–1 重写模型输入输出。routing/2 涉及能力限制，需核对适用范围与当前能力。 | 方法选择保留问题到方法的映射。产品事实引用清单。遇到含混历史断言先登记待核。 |
| 专题方法和实现 | gw-method-drastic-index/0、gw-model-card-drastic/0–2、gw-method-pest-and-inversion/0、gw-model-card-porous-calibration/0 | 理论权重、适用范围与当前实现约束接近。 | 保留方法的来源与限制。Model Card 只写该实现的口径与输入。 |

## 核心方法来源与 locator 补齐清单

表内已有 locator 从现存资产提取。未登记时列出核验对象，章、页、条款由后续对照原文填写。当前内部卡片的 `section` 不能直接当作外部来源条款。

| 文档/单元 | 原登记 sourceRefs | 已有 locator | 后续核验 |
| --- | --- | --- | --- |
| `gw-mann-kendall-cautions/chunk-0` | epa-groundwater-statistics-guide | 缺失 | 查 sourceRefs 原文的对应段、页或条款并记录版次 |
| `gw-mann-kendall-cautions/chunk-1` | epa-groundwater-statistics-guide | 缺失 | 查 sourceRefs 原文的对应段、页或条款并记录版次 |
| `gw-mann-kendall-cautions/chunk-2` | epa-groundwater-statistics-guide, groundwater-domain-definitions | 缺失 | 查 sourceRefs 原文的对应段、页或条款并记录版次 |
| `gw-idw-validation/chunk-0` | groundwater-domain-definitions, qgis-interpolation-3-40 | 缺失 | 查 sourceRefs 原文的对应段、页或条款并记录版次 |
| `gw-idw-validation/chunk-1` | groundwater-domain-definitions, qgis-interpolation-3-40 | 缺失 | 查 sourceRefs 原文的对应段、页或条款并记录版次 |
| `gw-idw-validation/chunk-2` | qgis-interpolation-3-40 | 缺失 | 查 sourceRefs 原文的对应段、页或条款并记录版次 |
| `gw-water-level-head/chunk-0` | usgs-gwpd | 缺失 | 查 sourceRefs 原文的对应段、页或条款并记录版次 |
| `gw-water-level-head/chunk-1` | usgs-circular-1217-box-a | 缺失 | 查 sourceRefs 原文的对应段、页或条款并记录版次 |
| `gw-water-level-head/chunk-2` | gbt-51040-2023-release, usgs-circular-1217-box-a | 缺失 | 查 sourceRefs 原文的对应段、页或条款并记录版次 |
| `gw-trend-methods/chunk-0` | groundwater-domain-definitions, usgs-groundwater-trends-2020 | 缺失 | 查 sourceRefs 原文的对应段、页或条款并记录版次 |
| `gw-trend-methods/chunk-1` | epa-groundwater-statistics-guide, groundwater-domain-definitions | 缺失 | 查 sourceRefs 原文的对应段、页或条款并记录版次 |
| `gw-trend-methods/chunk-2` | epa-groundwater-statistics-guide, groundwater-domain-definitions, usgs-groundwater-trends-2020 | 缺失 | 查 sourceRefs 原文的对应段、页或条款并记录版次 |
| `gw-idw-and-gradient/chunk-0` | groundwater-domain-definitions, qgis-interpolation-3-10 | 缺失 | 查 sourceRefs 原文的对应段、页或条款并记录版次 |
| `gw-idw-and-gradient/chunk-1` | qgis-interpolation-3-10 | 缺失 | 查 sourceRefs 原文的对应段、页或条款并记录版次 |
| `gw-idw-and-gradient/chunk-2` | groundwater-domain-definitions, usgs-circular-1217-box-a | 缺失 | 查 sourceRefs 原文的对应段、页或条款并记录版次 |
| `gw-darcy-velocity/chunk-0` | groundwater-domain-definitions, usgs-sir-2016-5021 | 缺失 | 查 sourceRefs 原文的对应段、页或条款并记录版次 |
| `gw-darcy-velocity/chunk-1` | usgs-wri-95-4015 | 缺失 | 查 sourceRefs 原文的对应段、页或条款并记录版次 |
| `gw-darcy-velocity/chunk-2` | groundwater-domain-definitions, usgs-sir-2016-5021 | 缺失 | 查 sourceRefs 原文的对应段、页或条款并记录版次 |
| `gw-measurement-quality-control/chunk-0` | usgs-gwpd | 缺失 | 查 sourceRefs 原文的对应段、页或条款并记录版次 |
| `gw-measurement-quality-control/chunk-1` | usgs-furnished-records-policy | 缺失 | 查 sourceRefs 原文的对应段、页或条款并记录版次 |
| `gw-measurement-quality-control/chunk-2` | usgs-data-revisions-guidance | 缺失 | 查 sourceRefs 原文的对应段、页或条款并记录版次 |
| `gw-model-card-monitoring-trend/chunk-0` | groundwater-6-2-model-cards | {"section": "模型与适用任务"} | 核对每项主张与原文指向，记录审阅时间和适用范围，实现事实绑定准确模型/Workflow 版本 |
| `gw-model-card-monitoring-trend/chunk-1` | groundwater-6-2-model-cards | {"section": "输入与待补信息"} | 核对每项主张与原文指向，记录审阅时间和适用范围，实现事实绑定准确模型/Workflow 版本 |
| `gw-model-card-monitoring-trend/chunk-2` | groundwater-6-2-model-cards | {"section": "输出、成本与后续"} | 核对每项主张与原文指向，记录审阅时间和适用范围，实现事实绑定准确模型/Workflow 版本 |
| `gw-model-card-flow-field/chunk-0` | groundwater-6-2-model-cards | {"section": "模型与适用任务"} | 核对每项主张与原文指向，记录审阅时间和适用范围，实现事实绑定准确模型/Workflow 版本 |
| `gw-model-card-flow-field/chunk-1` | groundwater-6-2-model-cards | {"section": "输入与待补信息"} | 核对每项主张与原文指向，记录审阅时间和适用范围，实现事实绑定准确模型/Workflow 版本 |
| `gw-model-card-flow-field/chunk-2` | groundwater-6-2-model-cards | {"section": "输出、成本与后续"} | 核对每项主张与原文指向，记录审阅时间和适用范围，实现事实绑定准确模型/Workflow 版本 |
| `gw-model-card-kriging/chunk-0` | groundwater-6-2-model-cards | {"section": "模型与适用任务"} | 核对每项主张与原文指向，记录审阅时间和适用范围，实现事实绑定准确模型/Workflow 版本 |
| `gw-model-card-kriging/chunk-1` | groundwater-6-2-model-cards | {"section": "参数与诊断"} | 核对每项主张与原文指向，记录审阅时间和适用范围，实现事实绑定准确模型/Workflow 版本 |
| `gw-model-card-kriging/chunk-2` | groundwater-6-2-model-cards | {"section": "输出与科学边界"} | 核对每项主张与原文指向，记录审阅时间和适用范围，实现事实绑定准确模型/Workflow 版本 |
| `gw-model-card-rule-classification/chunk-0` | groundwater-6-2-model-cards | {"section": "模型与适用任务"} | 核对每项主张与原文指向，记录审阅时间和适用范围，实现事实绑定准确模型/Workflow 版本 |
| `gw-model-card-rule-classification/chunk-1` | groundwater-6-2-model-cards | {"section": "输入与规则边界"} | 核对每项主张与原文指向，记录审阅时间和适用范围，实现事实绑定准确模型/Workflow 版本 |
| `gw-model-card-rule-classification/chunk-2` | groundwater-6-2-model-cards | {"section": "输出与未评价语义"} | 核对每项主张与原文指向，记录审阅时间和适用范围，实现事实绑定准确模型/Workflow 版本 |
| `gw-model-card-drastic/chunk-0` | groundwater-6-2-model-cards | {"section": "模型与适用任务"} | 核对每项主张与原文指向，记录审阅时间和适用范围，实现事实绑定准确模型/Workflow 版本 |
| `gw-model-card-drastic/chunk-1` | groundwater-6-2-model-cards | {"section": "配置、网格与缺失"} | 核对每项主张与原文指向，记录审阅时间和适用范围，实现事实绑定准确模型/Workflow 版本 |
| `gw-model-card-drastic/chunk-2` | groundwater-6-2-model-cards | {"section": "计算、输出与科学边界"} | 核对每项主张与原文指向，记录审阅时间和适用范围，实现事实绑定准确模型/Workflow 版本 |
| `gw-method-sen-slope/chunk-0` | sen-1968 | {"reference": "Sen (1968), pp. 1379–1389", "implementation": "statistics.mann_kendall@1.0.0"} | 核对每项主张与原文指向，记录审阅时间和适用范围 |
| `gw-method-pettitt/chunk-0` | pettitt-1979 | {"reference": "Pettitt (1979), pp. 126–135", "implementation": "statistics.pettitt_change_point@1.0.0"} | 核对每项主张与原文指向，记录审阅时间和适用范围 |
| `gw-method-kriging-variogram/chunk-0` | usgs-kriging-sir20145038 | {"reference": "USGS SIR 2014-5038, spatial interpolation"} | 核对每项主张与原文指向，记录审阅时间和适用范围 |
| `gw-method-drastic-index/chunk-0` | epa-drastic-1987 | {"reference": "EPA/600/2-87/035, DRASTIC factors and rating"} | 核对每项主张与原文指向，记录审阅时间和适用范围 |
| `gw-method-modflow-six/chunk-0` | usgs-modflow6-tm6a55 | {"reference": "USGS TM 6-A55, Groundwater Flow model"} | 核对每项主张与原文指向，记录审阅时间和适用范围 |
| `gw-method-flopy-reproducibility/chunk-0` | usgs-flopy-workflows | {"reference": "USGS FloPy workflows, reproducible model construction"} | 核对每项主张与原文指向，记录审阅时间和适用范围 |
| `gw-method-pest-and-inversion/chunk-0` | usgs-pest-inversion | {"reference": "USGS SIR 2010-5211, highly parameterized inversion"} | 核对每项主张与原文指向，记录审阅时间和适用范围 |
| `gw-method-pastas-response/chunk-0` | pastas-2019 | {"reference": "Collenteur et al. (2019), model structure and examples"} | 核对每项主张与原文指向，记录审阅时间和适用范围 |
| `gw-method-groundwater-uncertainty/chunk-0` | usgs-pest-inversion | {"reference": "USGS SIR 2010-5211, predictive uncertainty"} | 核对每项主张与原文指向，记录审阅时间和适用范围 |

来源工作项按风险排序。优先核对 `usgs-circular-1139` 的题名和相应水文过程段落。前轮题录核验已登记。随后核验趋势独立性与季节性约束、监测基准、插值验证、Darcy 适用性、DRASTIC 原始方法和标准版本。来源级著录、源文档 checksum、逐主张 locator 与核验时间只在取得原文及权限后录入。 [前轮来源抽查](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/EXTERNAL_REFERENCES.md)

## 全部来源

| Source ID | Kind | 标题与发布方 | 版本 / 出版 / 获取 / 复核日 | 状态 |
| --- | --- | --- | --- | --- |
| `epa-drastic-1987` | OFFICIAL_GUIDANCE | [DRASTIC, EPA/600/2-87/035](https://nepis.epa.gov/Exe/ZyPURL.cgi?Dockey=20007KU4.TXT) · US EPA | 1987 / 未登记 / 2026-11-14 / 2027-11-14 | 待核原文 |
| `epa-groundwater-statistics-guide` | OFFICIAL_GUIDANCE | [Statistical Analysis of Groundwater Monitoring Data](https://semspub.epa.gov/work/HQ/100001733.pdf) · U.S. Environmental Protection Agency | 未登记 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `epa-unified-guidance` | OFFICIAL_GUIDANCE | [Unified Guidance for Statistical Analysis of Groundwater Monitoring Data](https://archive.epa.gov/epawaste/hazard/web/pdf/unified-guid.pdf) · U.S. Environmental Protection Agency | 未登记 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `gbt-51040-2023-release` | OFFICIAL_STANDARD | [地下水监测工程技术标准发布信息](https://app.www.gov.cn/govdata/gov/202312/28/510656/article.html) · 中华人民共和国住房和城乡建设部 | GB/T 51040-2023 / 2023-12-28 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `groundwater-6-2-model-cards` | DOMAIN_DEFINITION | Groundwater 6.2.0 model-cards · Groundwater Platform | 6.2.0 / 2026-11-14 / 未登记 / 2027-11-14 | 待核原文 |
| `groundwater-6-2-porous-model-cards` | DOMAIN_DEFINITION | Groundwater 6.2.0 porous-model-cards · Groundwater Platform | 6.2.0 / 2026-11-14 / 未登记 / 2027-11-14 | 待核原文 |
| `groundwater-demo-assumptions` | DEMO_ASSUMPTION | Groundwater demonstration assumptions · Groundwater Platform | 1.0.0 / 2026-09-28 / 未登记 / 2027-09-28 | 待核原文 |
| `groundwater-domain-definitions` | DOMAIN_DEFINITION | Groundwater Domain Model and Workflow Definitions · Groundwater Platform | 1.1.0 / 2026-09-28 / 未登记 / 2027-09-28 | 待核原文 |
| `groundwater-project-templates` | PROJECT_TEMPLATE | Groundwater project knowledge templates · Groundwater Platform | 1.1.0 / 2026-10-09 / 未登记 / 2027-10-09 | 待核原文 |
| `pastas-2019` | RESEARCH_PAPER | [Pastas: Open Source Software for the Analysis of Groundwater Time Series](https://doi.org/10.1111/gwat.12925) · Groundwater | 2019 / 未登记 / 2026-11-14 / 2027-11-14 | 待核原文 |
| `pettitt-1979` | RESEARCH_PAPER | [A Non-Parametric Approach to the Change-Point Problem](https://doi.org/10.2307/2346729) · Royal Statistical Society | 1979 / 未登记 / 2026-11-14 / 2027-11-14 | 待核原文 |
| `qgis-interpolation-3-10` | OFFICIAL_GUIDANCE | [QGIS interpolation documentation](https://docs.qgis.org/3.10/en/docs/user_manual/processing_algs/qgis/interpolation.html) · QGIS Project | 3.10 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `qgis-interpolation-3-40` | OFFICIAL_GUIDANCE | [QGIS interpolation documentation](https://docs.qgis.org/3.40/en/docs/user_manual/processing_algs/qgis/interpolation.html) · QGIS Project | 3.40 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `sen-1968` | RESEARCH_PAPER | [Estimates of the Regression Coefficient Based on Kendall’s Tau](https://doi.org/10.1080/01621459.1968.10480934) · American Statistical Association | 1968 / 未登记 / 2026-11-14 / 2027-11-14 | 待核原文 |
| `usgs-aquifers-groundwater` | OFFICIAL_GUIDANCE | [Aquifers and Groundwater](https://www.usgs.gov/water-science-school/science/aquifers-and-groundwater) · U.S. Geological Survey | 未登记 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-california-groundwater-levels` | OFFICIAL_GUIDANCE | [Groundwater Levels in California](https://ca.water.usgs.gov/sustainable-groundwater-management/groundwater-levels-california.html) · U.S. Geological Survey | 未登记 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-circular-1139` | OFFICIAL_GUIDANCE | [Ground Water Atlas reference](https://pubs.usgs.gov/circ/circ1139/) · U.S. Geological Survey | Circular 1139 / 未登记 / 2026-09-28 / 2027-09-28 | 题名待复核修正 |
| `usgs-circular-1217-box-a` | OFFICIAL_GUIDANCE | [Hydraulic Head and Gradient](https://pubs.usgs.gov/circ/circ1217/html/boxa.html) · U.S. Geological Survey | Circular 1217 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-circular-1217-essential` | OFFICIAL_GUIDANCE | [Ground-Water System Essentials](https://pubs.usgs.gov/circ/circ1217/html/essential.html) · U.S. Geological Survey | Circular 1217 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-circular-1224` | OFFICIAL_GUIDANCE | [Ground-Water-Level Monitoring and the Importance of Long-Term Water-Level Data](https://pubs.usgs.gov/circ/2002/circ1224/html/understanding.html) · U.S. Geological Survey | Circular 1224 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-data-revisions-guidance` | OFFICIAL_POLICY | [Water Data Revisions Guidance](https://water.usgs.gov/osw/RevisionsGuidance.html) · U.S. Geological Survey | 未登记 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-flopy-workflows` | OFFICIAL_GUIDANCE | [FloPy workflows for creating structured and unstructured MODFLOW models](https://www.usgs.gov/publications/flopy-workflows-creating-structured-and-unstructured-modflow-models) · USGS | 2023 / 未登记 / 2026-11-14 / 2027-11-14 | 待核原文 |
| `usgs-furnished-records-policy` | OFFICIAL_POLICY | [Policy for Furnished Groundwater Records](https://water.usgs.gov/usgs/ogw/policy/furnished-records/) · U.S. Geological Survey | 未登记 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-groundwater-discharge-cycle` | OFFICIAL_GUIDANCE | [Groundwater Discharge and the Water Cycle](https://www.usgs.gov/special-topic/water-science-school/science/groundwater-discharge-and-water-cycle) · U.S. Geological Survey | 未登记 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-groundwater-levels-time-series` | OFFICIAL_GUIDANCE | [Groundwater Levels Time-Series Guidance](https://water.usgs.gov/osw/time-series-guidance/AAA_Groundwater_Levels.pdf) · U.S. Geological Survey | 未登记 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-groundwater-streamflow-trends` | OFFICIAL_GUIDANCE | [Trends in Groundwater and Streamflow](https://www.usgs.gov/mission-areas/water-resources/news/above-so-below-trends-groundwater-and-streamflow-across-united) · U.S. Geological Survey | 未登记 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-groundwater-trends-2020` | OFFICIAL_GUIDANCE | [Long-term monotonic trends in annual groundwater metrics](https://www.usgs.gov/data/long-term-monotonic-trends-annual-groundwater-metrics-united-states-through-2020) · U.S. Geological Survey | through 2020 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-gwpd` | OFFICIAL_GUIDANCE | [Groundwater Technical Procedures Training](https://water.usgs.gov/ogw/video/gwpd.html) · U.S. Geological Survey | 未登记 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-kriging-sir20145038` | OFFICIAL_GUIDANCE | [USGS Scientific Investigations Report 2014-5038](https://pubs.usgs.gov/sir/2014/5038/pdf/sir2014-5038.pdf) · USGS | 2014 / 未登记 / 2026-11-14 / 2027-11-14 | 待核原文 |
| `usgs-modflow6-tm6a55` | OFFICIAL_GUIDANCE | [Documentation for the MODFLOW 6 Groundwater Flow Model](https://pubs.usgs.gov/publication/tm6A55) · USGS | 2017 / 未登记 / 2026-11-14 / 2027-11-14 | 待核原文 |
| `usgs-ofr-95-398` | OFFICIAL_GUIDANCE | [Groundwater reference](https://pubs.usgs.gov/of/1995/ofr-95-398/of95-398.html) · U.S. Geological Survey | OFR 95-398 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-ofr-97-11-gwqap` | OFFICIAL_GUIDANCE | [Ground-Water Quality Assurance Practices](https://water.usgs.gov/ogw/pubs/OFR9711/gwqap.html) · U.S. Geological Survey | OFR 97-11 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-pest-inversion` | OFFICIAL_GUIDANCE | [Approaches to highly parameterized inversion, SIR 2010-5211](https://pubs.usgs.gov/publication/sir20105211) · USGS | 2010 / 未登记 / 2026-11-14 / 2027-11-14 | 待核原文 |
| `usgs-rivers-groundwater` | OFFICIAL_GUIDANCE | [Rivers Contain Groundwater](https://www.usgs.gov/special-topics/water-science-school/science/rivers-contain-groundwater) · U.S. Geological Survey | 未登记 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-sir-2006-5294` | OFFICIAL_GUIDANCE | [Groundwater reference](https://pubs.usgs.gov/sir/2006/5294/pdf/sir06-5294_508.pdf) · U.S. Geological Survey | SIR 2006-5294 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-sir-2016-5021` | OFFICIAL_GUIDANCE | [Groundwater flow and Darcy law reference](https://pubs.usgs.gov/sir/2016/5021/sir20165021.pdf) · U.S. Geological Survey | SIR 2016-5021 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-specific-yield-wtf` | OFFICIAL_GUIDANCE | [Water-Table Fluctuation Method: Estimating Specific Yield](https://water.usgs.gov/ogw/gwrp/methods/wtf/estimating_sy.html) · USGS | 未登记 / 未登记 / 2026-11-14 / 2027-11-14 | 待核原文 |
| `usgs-time-series-guidance` | OFFICIAL_GUIDANCE | [Water Time-Series Guidance](https://water.usgs.gov/osw/time-series-guidance/) · U.S. Geological Survey | 未登记 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-tm-1a1` | OFFICIAL_GUIDANCE | [USGS Groundwater Technical Procedures](https://pubs.usgs.gov/tm/1a1/) · U.S. Geological Survey | TM 1-A1 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-transmissivity-interpretation` | RESEARCH_PAPER | [Interpretation of transmissivity estimates from single-well pumping aquifer tests](https://www.usgs.gov/publications/interpretation-transmissivity-estimates-single-well-pumping-aquifer-tests) · USGS | 2005 / 未登记 / 2026-11-14 / 2027-11-14 | 待核原文 |
| `usgs-twri-3-b2` | OFFICIAL_GUIDANCE | [Introduction to Ground-Water Hydraulics](https://water.usgs.gov/ogw/pubs/TWRI3-B2/TWRI3-B2-with-links.pdf) · U.S. Geological Survey | TWRI 3-B2 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-water-memo-2049` | OFFICIAL_POLICY | [Water Datas Memorandum 2049](https://water.usgs.gov/water-resources/memos/memo.php?id=2049) · U.S. Geological Survey | Memo 2049 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-wri-95-4015` | OFFICIAL_GUIDANCE | [Groundwater monitoring reference](https://pubs.usgs.gov/wri/1995/4015/report.pdf) · U.S. Geological Survey | WRI 95-4015 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |
| `usgs-wri-97-4225` | OFFICIAL_GUIDANCE | [Groundwater reference](https://pubs.usgs.gov/wri/wri97-4225/wrir-97-4225.pdf) · U.S. Geological Survey | WRI 97-4225 / 未登记 / 2026-09-28 / 2027-09-28 | 待核原文 |

## 全部文档身份

文档 checksum 对应现行转换器及 `document_content` 生成的落库正文。新导入或不同规范化方式形成的记录须与实际 `versions.checksum` 比对。读取受保护的历史库相关核对属于后续验收。非 Demo 标记并不确认正文为正式知识。

| 文档和标题 | 资产 | kind / modelRef | 单元 / 标记单元 | 文档 checksum |
| --- | --- | --- | --- | --- |
| `gw-project-overview-template` 项目概览填写模板（演示，未绑定真实项目） | [agent-discovery-templates.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/agent-discovery-templates.v1.json) | knowledge | 1 / 1 | `sha256:7a676231c4939b2eb770e8fe2bdb4bfc5e4a714af238a86b9fa01f0ddf944804` |
| `gw-data-dictionary-template` 精确资源数据字典填写模板（演示，未绑定资源） | [agent-discovery-templates.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/agent-discovery-templates.v1.json) | knowledge | 1 / 1 | `sha256:e149fa6a697af5fac2c7410063a408d212acadd35aceb72d6a8854949e35ce84` |
| `gw-model-evidence-template` 模型参数依据填写模板（演示） | [agent-discovery-templates.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/agent-discovery-templates.v1.json) | knowledge | 1 / 1 | `sha256:298718dfca6d90b1af1983d15db8b860341ed89e8866d0112e9e87e8dd134bcd` |
| `gw-vertical-hydraulic-gradient` 地下水垂向水力梯度 | [demo-knowledge-interpretation.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-interpretation.v1.json) | knowledge | 3 / 0 | `sha256:dc7185b823d7b2525a7af54cedab0a323cbd3c89fc2f993f09e873c1419b3aae` |
| `gw-nested-well-comparison` 成组浅深监测井比较 | [demo-knowledge-interpretation.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-interpretation.v1.json) | knowledge | 3 / 0 | `sha256:4ba51bc0f03e8399ad5fcdca74bbfc945cb8f7e411dca994bd977a1146738f45` |
| `gw-surface-water-groundwater-interaction` 河流补给地下水或接受地下水排泄的交换解释 | [demo-knowledge-interpretation.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-interpretation.v1.json) | knowledge | 3 / 0 | `sha256:688786f4d06db6e189400887d6a79106b6def37efe638a8a49bab3d1fc512cdc` |
| `gw-recharge-response-lag` 地下水补给响应与时间滞后 | [demo-knowledge-interpretation.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-interpretation.v1.json) | knowledge | 3 / 0 | `sha256:1f8b2134d433eb41a4ef939cd4f2699983fe71182c6e49cb6eec13d9df051bd6` |
| `gw-hydrogeologic-boundaries` 地下水水文地质边界概念 | [demo-knowledge-interpretation.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-interpretation.v1.json) | knowledge | 3 / 0 | `sha256:556452576b15b392c8a2eedda50432e66f3588c7b2fc146d2680f35f0f6a43a7` |
| `gw-source-sink-conceptualization` 地下水源汇与局部水头形态 | [demo-knowledge-interpretation.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-interpretation.v1.json) | knowledge | 3 / 0 | `sha256:bdc7174ef730721c3d9f68d7e96df242a7229fe2504a83c01e106677ed6b3763` |
| `gw-anisotropy-heterogeneity-scale` 含水层各向异性非均质与尺度效应 | [demo-knowledge-interpretation.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-interpretation.v1.json) | knowledge | 3 / 0 | `sha256:b7588a2ac94c168aceaa264e8dfa1c22d26aca0cb7b71eefbc4f1e77977b34aa` |
| `gw-density-temperature-limitations` 地下水密度温度与淡水水头限制 | [demo-knowledge-interpretation.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-interpretation.v1.json) | knowledge | 3 / 0 | `sha256:b035061e6f953ce2e489e1a07d8961826b7158554a533b9bc559dfb4a22ddb88` |
| `gw-confined-pressure-response` 承压含水层压力响应解释 | [demo-knowledge-interpretation.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-interpretation.v1.json) | knowledge | 3 / 0 | `sha256:7859d18873ba93faef548902d59b947e46f95fb36b5d411d52b70b7846f48848` |
| `gw-contours-gradient-flow` 等水头线水力梯度与流向关系 | [demo-knowledge-interpretation.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-interpretation.v1.json) | knowledge | 3 / 0 | `sha256:ed8e3f3d917966d423d4ada42f2cd71b35c0592512177070fba12605059fc8af` |
| `gw-multi-snapshot-comparison` 多时点地下水流场比较 | [demo-knowledge-interpretation.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-interpretation.v1.json) | knowledge | 3 / 0 | `sha256:096187b214937edb7198b74077053ed498b1cbf030c702fef42318c70b52be8c` |
| `gw-spatial-validation-boundaries` 地下水空间插值验证边界 | [demo-knowledge-interpretation.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-interpretation.v1.json) | knowledge | 3 / 0 | `sha256:9ac0474a51d045d010f60ab12b957d31d573a791290bfeaf9a8a7aa1208165fe` |
| `gw-conflicting-evidence-resolution` 地下水冲突证据处理 | [demo-knowledge-interpretation.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-interpretation.v1.json) | knowledge | 3 / 0 | `sha256:500d7ef8d86536f2e0ba2ac57563d60b9fce5a9985d6c024a74008f384f819d6` |
| `gw-source-freshness-citation` 地下水知识来源时效与引用 | [demo-knowledge-interpretation.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-interpretation.v1.json) | knowledge | 3 / 0 | `sha256:1417c65337cb061ec246fa1c536c202e58245ede4c3a125fae7c0b7decba062f` |
| `gw-untrusted-knowledge-safety` 地下水知识不可信内容安全 | [demo-knowledge-interpretation.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-interpretation.v1.json) | knowledge | 3 / 0 | `sha256:230b90b8726ff5f721f2482885978c5d9e754e721d90e94af778b4edc9eec505` |
| `gw-time-normalization` 日期格式对流场快照的影响与地下水监测时间标准化 | [demo-knowledge-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-methods.v1.json) | method | 3 / 0 | `sha256:704b6a13ed129c1e4b0ec10eb66097eb6179872358dd7ada3dc40057da3cdee0` |
| `gw-sampling-frequency-bias` 采样频率与趋势偏差 | [demo-knowledge-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-methods.v1.json) | method | 3 / 0 | `sha256:63ab1de945b130b592a2bac3abfd534175035e9a03bec0de3abe0b13dc57ebd9` |
| `gw-aggregation-choice` 地下水时间聚合选择 | [demo-knowledge-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-methods.v1.json) | method | 3 / 0 | `sha256:307927417c7a064ae2ed601b93a7bc467fa1abea6de96e08eefeaa50fb1abb9e` |
| `gw-completeness-design` 地下水监测完整率设计 | [demo-knowledge-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-methods.v1.json) | method | 3 / 1 | `sha256:248c3362c833207856a4136ce4f3afc606e5a227026a328d828000aa76b3ebc3` |
| `gw-linear-trend-diagnostics` 线性趋势诊断与限制 | [demo-knowledge-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-methods.v1.json) | method | 3 / 0 | `sha256:1f715885171accee2d0a8852162ec3c51fdb1094ee32265f0ecc7a59272fc183` |
| `gw-mann-kendall-cautions` Mann–Kendall趋势检验注意事项 | [demo-knowledge-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-methods.v1.json) | method | 3 / 0 | `sha256:b849747bdc944f0ab744734811beffc85e46438f7358ae513df0e2e77df6434b` |
| `gw-mad-outlier-review` MAD异常点复核方法 | [demo-knowledge-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-methods.v1.json) | method | 3 / 0 | `sha256:b70e7b702a094b61790e61da8b9261a95dab1b97605eefd11e48d5bd6e210af9` |
| `gw-period-comparison-design` 基准期与对比期设计 | [demo-knowledge-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-methods.v1.json) | method | 3 / 0 | `sha256:13c5267cf2845e8f5d89fcadcc66469b4fb22e50bd458edd3e2c9a2e0ba535d4` |
| `gw-indicator-direction` 地下水埋深（depth to water）与水头指标方向解释 | [demo-knowledge-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-methods.v1.json) | method | 3 / 0 | `sha256:a82254e85569e861446b617631e0bbce519da07a3b60995ff7683052e048e1b7` |
| `gw-snapshot-selection-policy` 流场快照选择策略 | [demo-knowledge-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-methods.v1.json) | method | 3 / 0 | `sha256:554324ed83b34a2ab9614dba6f5ed03c14d0313bdbcbd0f62dbecf9da4484f77` |
| `gw-idw-validation` IDW插值验证与敏感性 | [demo-knowledge-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-methods.v1.json) | method | 3 / 0 | `sha256:9f2cebe702bc0b23f286682a7f86882b44ae8ba8d1d9764d3e00cb5d3598b84e` |
| `gw-raster-resolution` 流场栅格像元与尺度 | [demo-knowledge-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-methods.v1.json) | method | 3 / 0 | `sha256:8860614d87511942d3efba90317061d11fa3245e2747a25a85db9c12b79d4258` |
| `gw-boundary-effects` 地下水流场边界效应 | [demo-knowledge-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-methods.v1.json) | method | 3 / 0 | `sha256:57c7a07c6cee42f6038f4d18e67c2ef331d46ac8d822582795b4bb91ca005744` |
| `gw-darcy-sensitivity` Darcy速度参数敏感性 | [demo-knowledge-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-methods.v1.json) | method | 3 / 1 | `sha256:9e0f6cc9691d76369d11da61c16187f5b42236e54e1046974d3a88177c03c7d2` |
| `gw-evidence-provenance` 地下水分析证据链与溯源 | [demo-knowledge-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-methods.v1.json) | method | 3 / 0 | `sha256:e3ffc3429ef7d6bc0ee29cf4528a47bc630b0f41dfa3a93e46688ad4532608b8` |
| `gw-pressure-transducer-records` 地下水压力传感器连续记录 | [demo-knowledge-quality.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-quality.v1.json) | knowledge | 3 / 0 | `sha256:c09a6436d842f95f9d087ae84cd08cf2b77e13584219020f0aee8a5b1e432742` |
| `gw-barometric-temperature-effects` 气压温度对地下水位记录的影响 | [demo-knowledge-quality.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-quality.v1.json) | knowledge | 3 / 0 | `sha256:0fd7f4560e09786c279d4460a900474ac4529dc549be62d720a0f641ccf51d02` |
| `gw-manual-calibration-checks` 人工水位测量与仪器校准检查 | [demo-knowledge-quality.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-quality.v1.json) | knowledge | 3 / 0 | `sha256:0b6d20f517bfcc78ccfb39d3a11005fcc685dd96c2b10d9bfcadbfb7b265dff8` |
| `gw-dry-flowing-well-states` 低于探头量程、井干与自流井状态记录 | [demo-knowledge-quality.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-quality.v1.json) | knowledge | 3 / 0 | `sha256:908e667b06d3563a42fd91c104bc3a30e9adf7070fdc1cf5856f2991f3c2ff0c` |
| `gw-static-pumping-recovery-state` 静水位动水位与停泵恢复状态 | [demo-knowledge-quality.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-quality.v1.json) | knowledge | 3 / 0 | `sha256:5de3cf43f3dda5249b447a816b827c5fd557cca99583e933fa626e1e9c64d4c1` |
| `gw-well-integrity-obstruction` 监测井完整性淤积与堵塞检查 | [demo-knowledge-quality.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-quality.v1.json) | knowledge | 3 / 0 | `sha256:b0788b851687706755b9448547c50aa8a71d935f4aabfb49b4c2bfc513ee0d40` |
| `gw-telemetry-clock-transfer` 地下水遥测时钟与数据传输质量 | [demo-knowledge-quality.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-quality.v1.json) | knowledge | 3 / 0 | `sha256:90d6dcbd15983d990e1e280ac50b5605bd9ad36c9bf9905c7d871cfa4b64f445` |
| `gw-sensor-range-resolution` 水位传感器量程分辨率与精度 | [demo-knowledge-quality.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-quality.v1.json) | knowledge | 3 / 0 | `sha256:01e131ddc17cb03edd7921c288c26846011586502fc2d05dde3803de2a5ca334` |
| `gw-quality-flags-qualifiers` 地下水记录质量标记与状态限定 | [demo-knowledge-quality.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-quality.v1.json) | knowledge | 3 / 0 | `sha256:757e215ac8878bb072eff502a73d08f975cd320a58c909f0f24b747292886d32` |
| `gw-record-revision-audit` 地下水记录修订与审计链 | [demo-knowledge-quality.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-quality.v1.json) | knowledge | 3 / 0 | `sha256:afbb5a1e85f237291535ce1af778e7c4a3496087262f846af33eba49063473bf` |
| `gw-elevation-survey-datum` 井点高程测量与统一基准控制 | [demo-knowledge-quality.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-quality.v1.json) | knowledge | 3 / 0 | `sha256:a8f7e8e5df6d64a6e3ef045f956ad9f1ea4a99eef03e324817b7eaf2ca1d1ed6` |
| `gw-screen-construction-metadata` 监测井筛管与开放层段元数据 | [demo-knowledge-quality.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-quality.v1.json) | knowledge | 3 / 0 | `sha256:fa7d2b065074ac84374d0deefd1712140b39a1e44fb30306be7e1d609e007146` |
| `gw-well-replacement-continuity` 监测井替换与长期序列连续性 | [demo-knowledge-quality.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-quality.v1.json) | knowledge | 3 / 0 | `sha256:dec08907d0edcd9f65d1574c995d403f2f29c6df107c00b13346c7352c976e2c` |
| `gw-network-change-control` 地下水监测井网变化控制 | [demo-knowledge-quality.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-quality.v1.json) | knowledge | 3 / 0 | `sha256:ba0faf9d6ea35bc9633b7481ecc1bfedb70a8267fb5c1cd2afa34dd9c14495a5` |
| `gw-source-acceptance-provenance` 外部地下水数据接收与来源溯源 | [demo-knowledge-quality.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-quality.v1.json) | knowledge | 3 / 0 | `sha256:9081e0abb07a405157e42f2a0f353da75a9000eb5a90a3843d33b53699cb52a8` |
| `gw-scenario-seasonal-decline` 季节性下降趋势演示场景 | [demo-knowledge-scenarios.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-scenarios.v1.json) | case | 3 / 3 | `sha256:38c30138e88142c1902fbcd900b3d59bcc2db6f97605782fb3505ed65471fe10` |
| `gw-scenario-recovery-recharge` 补给后水位恢复演示场景 | [demo-knowledge-scenarios.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-scenarios.v1.json) | case | 3 / 3 | `sha256:2ee45c56c9727ed20c40457fcb24e2e0bd31355a9c712c6e4759c1112f8f8d4c` |
| `gw-scenario-pumping-disturbance` 抽水扰动水位演示场景 | [demo-knowledge-scenarios.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-scenarios.v1.json) | case | 3 / 3 | `sha256:7c44b5cb0c16759e36e9d6178d66d5ba584df1524658874cdcaf5a4e63ec8f99` |
| `gw-scenario-sensor-drift` 水位传感器漂移演示场景 | [demo-knowledge-scenarios.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-scenarios.v1.json) | case | 3 / 3 | `sha256:3e2636b88e82517d4462df6d8f40715e9897403527f85dbec4b2ebcd3adc3b31` |
| `gw-scenario-datum-change` 测量基准变化演示场景 | [demo-knowledge-scenarios.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-scenarios.v1.json) | case | 3 / 3 | `sha256:2c747755ee46c2021a85cc6eef34751d06f99d5bce0d9c8e990ecc8a1235706f` |
| `gw-scenario-missing-months` 连续缺测月份演示场景 | [demo-knowledge-scenarios.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-scenarios.v1.json) | case | 3 / 3 | `sha256:d1a0ffca2661410ce886b5e6964a3b9afc8cbef5a9f4d61a096abf1f5bfa7532` |
| `gw-scenario-duplicate-records` 重复与冲突记录演示场景 | [demo-knowledge-scenarios.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-scenarios.v1.json) | case | 3 / 3 | `sha256:0014a9748dfebec13d4e63f20fb42d41f55dbed50de62a4373d499d5b80e1320` |
| `gw-scenario-small-sample` 小样本趋势演示场景 | [demo-knowledge-scenarios.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-scenarios.v1.json) | case | 3 / 3 | `sha256:c302fe7292f0dfa8d089d77d2c9ff2376aae6735ceff09c7fa8ee760044d4ad9` |
| `gw-scenario-stale-snapshot` 过旧流场快照演示场景 | [demo-knowledge-scenarios.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-scenarios.v1.json) | case | 3 / 3 | `sha256:0211b17712012ee41b25e05315c95f7eb101647c287ef9c3c95b4bad817b99cf` |
| `gw-scenario-crs-mismatch` 坐标系错误流场演示场景 | [demo-knowledge-scenarios.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-scenarios.v1.json) | case | 3 / 3 | `sha256:95406881fefd51e7d27fdf0619f5a9572e883b5cc9c4f80d5e586b5315466563` |
| `gw-scenario-sparse-edge` 稀疏井网边缘外推演示场景 | [demo-knowledge-scenarios.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-scenarios.v1.json) | case | 3 / 3 | `sha256:3316ba652f3d0386ead89e9aeefe242844b794e97f3ded49bc24ca1476b75411` |
| `gw-scenario-idw-bullseye` 局部高水头传播到 Darcy 速度的 IDW 靶心效应场景 | [demo-knowledge-scenarios.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-scenarios.v1.json) | case | 3 / 3 | `sha256:659b73427e66c0dbb8544ab3a66b0df80e6fb2eae3fc3146ae168eb0428c7a04` |
| `gw-scenario-parameter-range` 水力参数范围演示场景 | [demo-knowledge-scenarios.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-scenarios.v1.json) | case | 3 / 3 | `sha256:d1f5490325d3f299da230f26743b121bea421b686860d3214a6cb1c5e2c5c0aa` |
| `gw-scenario-conflicting-results` 趋势结果冲突演示场景 | [demo-knowledge-scenarios.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-scenarios.v1.json) | case | 3 / 3 | `sha256:c57e830043d725d4e311bfffc75d89fa26b33ef41e0849103cdf8a073c37971e` |
| `gw-scenario-flow-direction-shift` 流向随参数变化演示场景 | [demo-knowledge-scenarios.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge-scenarios.v1.json) | case | 3 / 3 | `sha256:849f1e11bf56ef29c3e4d7fcf729d6e9e505cd7b124e642502bb015051282b33` |
| `gw-task-routing` 地下水分析任务选择 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:2e02d648dbc3b031496d792ba80049e0b8c63629f2056755076b3779c1d1b7d1` |
| `gw-water-level-head` 地下水位埋深与水头 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | definition | 3 / 0 | `sha256:fe5d25f783eac64fee91a2fbb4da11554a3d5b4ba904f17c59f8968b9d2349a3` |
| `gw-monitoring-data-contract` 地下水监测数据约定 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:5043c0f49033b4a986df9327561b7b511dd8335b6dda565290e5e38aeb7ecdad` |
| `gw-trend-preparation` 地下水趋势分析准备 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:c80189eacf32e833328d7494b25936d9ca58efb489c0b2b1a8dc94caa7e063e0` |
| `gw-trend-methods` 地下水趋势统计方法 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | method | 3 / 0 | `sha256:630382af26f5f59e28083731fab077cbb55a8f09d0fadf0c06510574198abb92` |
| `gw-trend-interpretation` 地下水趋势结果解释 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:4eaf80da3a4dafef7abcf39a028ee512c91c0ea6a10e2a00be54fcb037d72032` |
| `gw-flow-input-and-snapshot` 地下水流场输入与快照 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:46826d91c956153efb4160c74ed7a73d8541ab2c747653d983133072caf3b13b` |
| `gw-idw-and-gradient` IDW插值与水力梯度 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | method | 3 / 0 | `sha256:99245f1ff6af4e2979cbea0253278544fbcbbbb6060c13101f41e902cfdfb7d8` |
| `gw-darcy-velocity` Darcy通量与渗流速度 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | method | 3 / 0 | `sha256:435eb5a835272076ccc54016c59faa6e29dcef2f2ae796be5e5d985efe4feb23` |
| `gw-demo-playbook` 地下水演示操作手册 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 3 | `sha256:6b953744667c9b14b58af24e14d3780249c41bdeabd8aa3d10330dfa187008ee` |
| `gw-aquifer-framework` 含水层与地下水系统 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | definition | 3 / 0 | `sha256:69aeb75051c4ed12d9b415225b5ae7778594fef28b6b05c578fe52ea144d17a4` |
| `gw-recharge-discharge-context` 地下水补给排泄背景 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:b06c5753d3d317ed729474aa495f7a3c83fe0aea488fbb8b5d33f46a786332d8` |
| `gw-well-representativeness` 监测井代表性与层位 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:dbc7b9b01d1f6ea7c370ce091c66ad7786238582a6ba12e70072541c9d288494` |
| `gw-measurement-quality-control` 水位测量质量控制 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:740cd2668f65df8a2880a39c410eaf06738eebc926e0ec8aad51562d0131a52f` |
| `gw-time-series-seasonality` 地下水时间序列与季节性 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:3c85042738be9b2dc4820bc38ff5ccabf541c054d2c8f95c0efe9abac431f584` |
| `gw-missing-duplicate-review` 缺测重复与异常复核 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:dbc4ff65966317725f4392e41b39a273d1d917a410e3ee275fee1c8deb0a6455` |
| `gw-trend-period-design` 趋势窗口与期间设计 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:69d1d78e9700ccca1ae67753fd26056a8855db4f7004ace912c629d2e77b15cd` |
| `gw-cross-well-comparison` 监测井之间的趋势比较 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:c62e42833a78f529fc2e717cd62d300947a5b190c331bb33b00ce388e7bb81ee` |
| `gw-snapshot-consistency` 流场快照时间一致性 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:af25cc5b43936ba102e494c8ac8cc03a71a552987980906ca78f4f51b504d5f7` |
| `gw-coordinate-systems` 地下水空间坐标系统 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:6bb54269b546db19ff9a63353fd8e792ceac088e71bb733e5e225272c6287ee6` |
| `gw-spatial-sampling-coverage` 井点空间覆盖与外推 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:69c28990886bf16a69db2c6d9274103bdd052f767458c1c0837fe473f12a30c1` |
| `gw-idw-sensitivity` IDW参数敏感性 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:eb8a6c22e7e3362c548bf5c683c956c532ee02557772617a0d061d3746098695` |
| `gw-gradient-quality` 水力梯度质量检查 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:f3bc7f89de0d508aa071351a5b54f914f26d6f63f22ca8c623ce3fca4b58dab4` |
| `gw-hydraulic-conductivity` 水力传导系数参数 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:7a0903c8c31905b5aa8e01013361c21aaa21e213638c9537a9918bea2eb79a18` |
| `gw-effective-porosity` 有效孔隙度参数 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:433575fb0991ebc207cbf7ec650487862dc9894895e875b2e9b64e6caca9f808` |
| `gw-flow-result-interpretation` 地下水流场结果解释 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:ea8c8a0af1f3c594192f53026e4f67eae2d1aecbbc6398f977cc69fe7377e7ed` |
| `gw-uncertainty-communication` 地下水分析不确定性表达 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:b660846569aeedf4d362867bdc4bdeb621798d454d5eb0ed93fda6fb74ebac51` |
| `gw-analysis-checklists` 地下水分析检查清单 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:0e4bba74022b6dac9fd8d6dd0b4f2f57af8c0ef29964bade86c4f85a3d5fdd7b` |
| `gw-question-patterns` 地下水问题问法映射 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | knowledge | 3 / 0 | `sha256:786c5374d9ccd8718a5bfa40c22c56afa25edf48fe2ac24dec84ea952802dc56` |
| `gw-domain-glossary` 地下水分析术语表 | [demo-knowledge.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/demo-knowledge.v1.json) | definition | 3 / 0 | `sha256:50d8d7bc32fd558a67acf0e657ce400840a31bdaa4902627ec5335b763eb04fc` |
| `gw-model-card-summary` 描述统计模型卡：数值概览与参数依据 | [model-cards.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/model-cards.v1.json) | knowledge / groundwater.summary / 3.1.0 | 3 / 0 | `sha256:0753d16ea18d648004842b33834418cb084c50996dc3e4f166976536002c2b4f` |
| `gw-model-card-monitoring-trend` 监测趋势模型卡：逐井时间序列、统计结果与空间制图限制 | [model-cards.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/model-cards.v1.json) | knowledge / groundwater.monitoring-trend / 3.1.0 | 3 / 0 | `sha256:95312fb2f46bee28f2876f92ed0aa11fbc1b71ca8aef76e0bda8a8bc7edf0d52` |
| `gw-model-card-flow-field` 地下水流场模型卡：水头计算到速度栅格及制图 | [model-cards.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/model-cards.v1.json) | knowledge / groundwater.flow-field / 3.1.0 | 3 / 0 | `sha256:944951f47011e990dd34ddf69107aad3b7e0563b7c8c180deca39e4e26fadd76` |
| `gw-model-card-monitoring-coverage` 监测井覆盖模型卡：整体凸包与几何范围制图 | [model-cards.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/model-cards.v1.json) | knowledge / groundwater.monitoring-coverage / 3.1.0 | 3 / 0 | `sha256:b05fd072edb4ba8d522f5fcd532bed317049b065887dde1932458669a0cbe56c` |
| `gw-model-card-transfer-learning-analysis` 迁移学习模型卡：十九项输入与单步留出回测 | [model-cards.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/model-cards.v1.json) | knowledge / groundwater.transfer-learning-analysis / 3.1.0 | 3 / 0 | `sha256:2612c1e7c3e56f31aa9781631c2a123ed11888345ee268d3d1a50d6ccea45a08` |
| `gw-model-card-render-map` 静态制图模型卡：单文件 GeoJSON 或栅格转 PNG | [model-cards.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/model-cards.v1.json) | knowledge / groundwater.render-map / 3.1.0 | 3 / 0 | `sha256:2265be5e41072ce249c0e5a8cbb397158c703c1a8f110b4a157bb0e8025e8817` |
| `gw-model-card-source-sustainability` 外部水源地综合评价模型卡：版本约束、完整性与凭据 | [model-cards.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/model-cards.v1.json) | knowledge / groundwater.source-sustainability / 1.1.0 | 3 / 0 | `sha256:75139ff91d669efb700059947601da070a2ab847cd31a8b878fa22d3acc9739d` |
| `gw-model-card-kriging` 克里金插值模型卡：变异函数、方差与留一法诊断 | [model-cards.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/model-cards.v1.json) | knowledge / groundwater.kriging / 1.0.0 | 3 / 0 | `sha256:b45638dc847d6818e38c92ddbe07048c8ccba096f50328ff5ee45152626c3538` |
| `gw-model-card-rule-classification` 规则分类模型卡：区间、来源、核验状态与未评价原因 | [model-cards.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/model-cards.v1.json) | knowledge / groundwater.rule-classification / 1.0.0 | 3 / 0 | `sha256:fb808d4fd46ff10dcac9e93faa8ff6b62892276757179291b67989cf9907ec03` |
| `gw-model-card-drastic` DRASTIC 模型卡：七因子规则、权重、网格与 NoData | [model-cards.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/model-cards.v1.json) | knowledge / groundwater.drastic / 1.0.0 | 3 / 0 | `sha256:1996a4d333eecc55e7f8656a4e496e6a0bd8aeee609e96134d2fdd9d68222e20` |
| `gw-model-card-porous-simulation` 孔隙型流与运移仿真 | [porous-model-cards.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/porous-model-cards.v1.json) | knowledge / groundwater.porous-simulation / 1.0.0 | 1 / 0 | `sha256:106e1d7585f38cf952212fabd9298d5b74df7b0e1579c6900c7222b44ca5898a` |
| `gw-model-card-porous-calibration` 孔隙型 PEST 水头率定 | [porous-model-cards.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/porous-model-cards.v1.json) | knowledge / groundwater.porous-calibration / 1.0.0 | 1 / 0 | `sha256:68a790d586a78b07031b3c90c47a48f3ce8224f36ad433949a2b6ec1e3ef251c` |
| `gw-model-card-porous-scenario-comparison` 孔隙型情景对比 | [porous-model-cards.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/porous-model-cards.v1.json) | knowledge / groundwater.porous-scenario-comparison / 1.0.0 | 1 / 0 | `sha256:f0f28f0945ed232e2b2b76cf9205cb34125f5bb749920d44fe00305c40ea13f8` |
| `gw-model-card-porous-map` 孔隙型栅格地图准备 | [porous-model-cards.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/porous-model-cards.v1.json) | knowledge / groundwater.porous-map / 1.0.0 | 1 / 0 | `sha256:f69b1aa203fd53202e355fa354b34dd5dcbaca14ad879336058d3d5c105a2405` |
| `gw-model-card-porous-inversion-training` 孔隙型反演训练 | [porous-model-cards.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/porous-model-cards.v1.json) | knowledge / groundwater.porous-inversion-training / 1.0.0 | 1 / 0 | `sha256:bf3eb7130121576bf7aa84f477d54d21baffc5e84684913636438320465bcc6c` |
| `gw-model-card-porous-inversion-sampling` 孔隙型反演采样 | [porous-model-cards.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/porous-model-cards.v1.json) | knowledge / groundwater.porous-inversion-sampling / 1.0.0 | 1 / 0 | `sha256:96f2a98a940678810a5491334836f5c2f0915da7b7cfcfa590ee269b18423176` |
| `gw-model-card-porous-agricultural-inversion` 孔隙型农业抽水反演 | [porous-model-cards.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/porous-model-cards.v1.json) | knowledge / groundwater.porous-agricultural-inversion / 1.0.0 | 1 / 0 | `sha256:f613337264b582dad110bdb329c85e53eb944a8547c9e18125edc76b4eb69c67` |
| `gw-model-card-sustainability-input-preparation` 水源地评价输入准备 | [porous-model-cards.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/porous-model-cards.v1.json) | knowledge / groundwater.sustainability-input-preparation / 1.0.0 | 1 / 0 | `sha256:bf1e25922ed0140600fa3246e9beeae856f33ea60709ed9f71e1d5ab88da13c8` |
| `gw-method-sen-slope` Sen 斜率：地下水监测序列的稳健变化率 | [groundwater-statistical-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/groundwater-statistical-methods.v1.json) | method | 1 / 0 | `sha256:bbcb9b3944e51b48a272cb17bd53d13ebd819f906eb79f2fa35a71a5c9ebbc10` |
| `gw-method-pettitt` Pettitt 单变点检验：地下水序列的候选位置变化 | [groundwater-statistical-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/groundwater-statistical-methods.v1.json) | method | 1 / 0 | `sha256:501429bd069addb74199ee0bb6ad34afbb81447f76b37ad7762e054d96a38df0` |
| `gw-aquifer-parameter-basis` 含水层导水与储水参数口径 | [groundwater-statistical-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/groundwater-statistical-methods.v1.json) | definition | 1 / 0 | `sha256:a9d829562b9d1f1f8697f206c82e9928675f3d25e3442d73c9dfddafba5829b3` |
| `gw-method-kriging-variogram` 地下水克里金的变异函数与验证 | [groundwater-statistical-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/groundwater-statistical-methods.v1.json) | method | 1 / 0 | `sha256:1a2bf63ddc4ec44dca5b9f3fed25fd617314d688a8b68b6a3d18a322bfa02057` |
| `gw-method-drastic-index` DRASTIC 地下水相对脆弱性指数 | [groundwater-statistical-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/groundwater-statistical-methods.v1.json) | method | 1 / 0 | `sha256:b88f156706b3de29ff259103f810cccbc4cc07c13e831e434369c6a06e95498d` |
| `gw-method-modflow-six` MODFLOW 6 地下水流动模型设计与结果诊断 | [groundwater-statistical-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/groundwater-statistical-methods.v1.json) | method | 1 / 0 | `sha256:4dc0040032a683fccc616356fa01cd0e9f41bb906724c4ad9a9136b8c874b5c3` |
| `gw-method-flopy-reproducibility` FloPy 与 MODFLOW 6 的可复现运行 | [groundwater-statistical-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/groundwater-statistical-methods.v1.json) | method | 1 / 0 | `sha256:d3322b448c8793c002d91001970f105e1482af05db74bbd64694f3fab73d21c6` |
| `gw-method-pest-and-inversion` PEST 率定、参数反演与可辨识性 | [groundwater-statistical-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/groundwater-statistical-methods.v1.json) | method | 1 / 0 | `sha256:dfccace3298a474a436acea86fe61987bc80ffa1222e6c61095d26dea2dd09b9` |
| `gw-method-pastas-response` Pastas 地下水时间序列应力响应分析 | [groundwater-statistical-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/groundwater-statistical-methods.v1.json) | method | 1 / 0 | `sha256:ce3cfc77e846f917df7ed6dde654a03699ef9fee5bff9ff2dcf1eba8769eb3f5` |
| `gw-method-groundwater-uncertainty` 地下水预测不确定性的量化与报告 | [groundwater-statistical-methods.v1.json](/home/mtd/liweigis/workflow/groundwater-v6/source/domains/groundwater/6.2.0/knowledge/groundwater-statistical-methods.v1.json) | method | 1 / 0 | `sha256:720f5e0faa0b790b415b09d7b3fc5de6eec87603dfb2edbb47172553f16ec3e3` |

## 全部单元与 locator

本表的待核状态用于内容审阅。正式检索不能直接把这张清单当作已审配置。

| 文档/单元 | 建议审阅方向 | sourceRefs | locator | 单元 checksum |
| --- | --- | --- | --- | --- |
| `gw-project-overview-template/chunk-0` | DEMO 待审核 | groundwater-project-templates | 缺失，待定位 | `sha256:464758de225fb7dea4bbadffc8c2eca590dbb96b896b30eed4b727f206ffdcb8` |
| `gw-data-dictionary-template/chunk-0` | DEMO 待审核 | groundwater-project-templates | 缺失，待定位 | `sha256:a77cbe8a9af25f7cbcbc85db36e3e98e685de1c0dd525d38ab7222a06fcbeacd` |
| `gw-model-evidence-template/chunk-0` | DEMO 待审核 | groundwater-project-templates | 缺失，待定位 | `sha256:640c7a10cfaafc682c9bb8b1e8ab964925689998be02358fd676fa12143ff433` |
| `gw-vertical-hydraulic-gradient/chunk-0` | 分类待审核 | groundwater-domain-definitions, usgs-circular-1224 | 缺失，待定位 | `sha256:1158e72ce617cabd1e7d5e6ab887ba215f01c7f2a19ec898ddcf21eb11a4ae23` |
| `gw-vertical-hydraulic-gradient/chunk-1` | 分类待审核 | groundwater-domain-definitions, usgs-sir-2006-5294 | 缺失，待定位 | `sha256:41732c620dabc29337dc8788acf154b8670397a5739ceb37bd5100d6f5c2e113` |
| `gw-vertical-hydraulic-gradient/chunk-2` | 分类待审核 | groundwater-domain-definitions, usgs-circular-1224, usgs-sir-2006-5294 | 缺失，待定位 | `sha256:c6f46586764035380bee339313fdb13ea37db723265096547788899cc5429964` |
| `gw-nested-well-comparison/chunk-0` | 分类待审核 | groundwater-domain-definitions, usgs-circular-1224 | 缺失，待定位 | `sha256:ab31b4c83d60c6a9c003df0c83436145627fb7669ada5a989ada4b5f1074b518` |
| `gw-nested-well-comparison/chunk-1` | 分类待审核 | groundwater-domain-definitions, usgs-circular-1217-essential | 缺失，待定位 | `sha256:18fd95a72e2423c8781ca71ada14cb587a0572f66e20f8c691103e9dad6b1181` |
| `gw-nested-well-comparison/chunk-2` | 分类待审核 | groundwater-domain-definitions, usgs-circular-1217-essential, usgs-circular-1224 | 缺失，待定位 | `sha256:22650bbdc8a94d40159b88ff6666ada03f67bcd8a2f5f5c8e74dc1e647b0e266` |
| `gw-surface-water-groundwater-interaction/chunk-0` | 分类待审核 | groundwater-domain-definitions, usgs-circular-1139 | 缺失，待定位 | `sha256:e59dedad376ac3de03a982e1703658e8792800529357310683ac8a9f9dfd0b5d` |
| `gw-surface-water-groundwater-interaction/chunk-1` | 分类待审核 | groundwater-domain-definitions, usgs-circular-1139 | 缺失，待定位 | `sha256:34bf5e889557c50a7203777a3d1d792787985389dafdf894ba0a16e1219ca4e6` |
| `gw-surface-water-groundwater-interaction/chunk-2` | 分类待审核 | groundwater-domain-definitions, usgs-circular-1139 | 缺失，待定位 | `sha256:388c17449dd99256f0885d26e56991d14f1d7ec59a0d7458850545de48c48612` |
| `gw-recharge-response-lag/chunk-0` | 分类待审核 | groundwater-domain-definitions, usgs-circular-1224 | 缺失，待定位 | `sha256:8e56c5bb9a336bfca50ce3a0ffb2ea5232df163cd36eb13e3739e887ad2fae45` |
| `gw-recharge-response-lag/chunk-1` | 分类待审核 | groundwater-domain-definitions, usgs-circular-1224 | 缺失，待定位 | `sha256:c3117ccc654ff1f23205e0f5609b6eb369462fca2755be746d7f4ebeb6b128af` |
| `gw-recharge-response-lag/chunk-2` | 分类待审核 | groundwater-domain-definitions, usgs-circular-1224 | 缺失，待定位 | `sha256:b2aee255b18551cb1ac06f5ee08edfd81a9d2efe84b92a370f46013733c52fb0` |
| `gw-hydrogeologic-boundaries/chunk-0` | 分类待审核 | groundwater-domain-definitions, usgs-circular-1224 | 缺失，待定位 | `sha256:1e0001f59cc5b0f450636088769d1168c12fd6e6db12d5e76dc3a0d25c0cf44c` |
| `gw-hydrogeologic-boundaries/chunk-1` | 分类待审核 | groundwater-domain-definitions, usgs-circular-1224 | 缺失，待定位 | `sha256:addc551b0e69b98a6f25c23f106c1dfc73dd97fb7796e94059229188c0e0c26f` |
| `gw-hydrogeologic-boundaries/chunk-2` | 分类待审核 | groundwater-domain-definitions, usgs-circular-1224 | 缺失，待定位 | `sha256:63fa79e216e755ca06479ba5fbd30fc4038813a6554bcf6583b4b3cc9a63f509` |
| `gw-source-sink-conceptualization/chunk-0` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:ac1ee2c90109ff495a61ca15995b77e3703833c6a861bc8e7aa2bbe182329ce8` |
| `gw-source-sink-conceptualization/chunk-1` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:2e71f8b821945edcc6b8254efafe0fc5b4512427e167ad50d5d058ba1468bb68` |
| `gw-source-sink-conceptualization/chunk-2` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:459ec8270ba29ff98813e42c01a822c7733f9c82f11165f15d1ac8098ddc7e01` |
| `gw-anisotropy-heterogeneity-scale/chunk-0` | 分类待审核 | groundwater-domain-definitions, usgs-twri-3-b2 | 缺失，待定位 | `sha256:219395ecc3e36c0c5d9ff136ff238bbe28fd644a0c07c89b58a95cfdead6698d` |
| `gw-anisotropy-heterogeneity-scale/chunk-1` | 分类待审核 | groundwater-domain-definitions, usgs-twri-3-b2 | 缺失，待定位 | `sha256:b1bcca5e9f8dde1d5616c3cf869a0110dab2a28fb2e1c8dd58b78a947c042236` |
| `gw-anisotropy-heterogeneity-scale/chunk-2` | 分类待审核 | groundwater-domain-definitions, usgs-twri-3-b2 | 缺失，待定位 | `sha256:9d210ad792d0102609221c0af504cbe802cc68a47a99b3138b129cc705b88850` |
| `gw-density-temperature-limitations/chunk-0` | 分类待审核 | groundwater-domain-definitions, usgs-twri-3-b2 | 缺失，待定位 | `sha256:4cdfbf23c95945a2bf2f87cfd787687a17ae67ece8a4f992146e8ce7d0179eed` |
| `gw-density-temperature-limitations/chunk-1` | 分类待审核 | groundwater-domain-definitions, usgs-twri-3-b2 | 缺失，待定位 | `sha256:e3d8e76fb94180f51b701e8879c37109f5adccf71191823c9b328a394bb6bdd0` |
| `gw-density-temperature-limitations/chunk-2` | 分类待审核 | groundwater-domain-definitions, usgs-twri-3-b2 | 缺失，待定位 | `sha256:1164b19a668d788a7ad86c924726a527e29c221fe55f13f8e3347d743b78a577` |
| `gw-confined-pressure-response/chunk-0` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:06e6ca62e1eb776a926ca4009fcb032478f0dd7220e8739492783eae4530fd90` |
| `gw-confined-pressure-response/chunk-1` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:315bfa6bcf30e54d0d4863ad86740a4a458a3b4c76f6174ee0413e0939436db6` |
| `gw-confined-pressure-response/chunk-2` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:5b9421586fe3ac3dbe2c363856d34d316e00832cf7dbf011660d09379856ce06` |
| `gw-contours-gradient-flow/chunk-0` | 分类待审核 | groundwater-domain-definitions, usgs-circular-1217-box-a | 缺失，待定位 | `sha256:457b3ba334f4b846bea9cd0ccf0a496bce6775759b5fcf1136cd1aea3335d64c` |
| `gw-contours-gradient-flow/chunk-1` | 分类待审核 | groundwater-domain-definitions, usgs-circular-1217-box-a | 缺失，待定位 | `sha256:1a29774d57218b48629c2ee2ef22acecc4778c6179d6f4d714fa0375e2440ba6` |
| `gw-contours-gradient-flow/chunk-2` | 分类待审核 | groundwater-domain-definitions, usgs-circular-1217-box-a | 缺失，待定位 | `sha256:7c983ab3d3d2126192753fc760a336c0be09d4170021ad038b2270e645ed9faf` |
| `gw-multi-snapshot-comparison/chunk-0` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:028fc52edec275e73254bafbc9ea39826534c8c583447037a0c6d8a05d05fd6a` |
| `gw-multi-snapshot-comparison/chunk-1` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:908c2f4b2c6729f640fea19790faa6fa12743349e9fd3ad721eafff3a83641ba` |
| `gw-multi-snapshot-comparison/chunk-2` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:462ac7798020d1a420afa5e71e0fe3b151dd43d1c5c4e126961f83ad29448308` |
| `gw-spatial-validation-boundaries/chunk-0` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:6e2890c0030739ac0fa142b5569aeb0cc5d336f9c32d4d5f8d0c294f35e4dcbb` |
| `gw-spatial-validation-boundaries/chunk-1` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:a534be1ee72a098460c2267bad4c88150df44007c1b3801e33d81e7df135f032` |
| `gw-spatial-validation-boundaries/chunk-2` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:b95e0d9f8943acd0dd2dd1bef76ac3cf87e4ffab70bad5ed1a3d7b86c3a23aae` |
| `gw-conflicting-evidence-resolution/chunk-0` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:f7944a43c881173a91a014c0c5fdacbd17d68ef585ab6b92bf868a4ceedeb124` |
| `gw-conflicting-evidence-resolution/chunk-1` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:f2d68eb0d8e28718ae966abfd533fc0a03506882735a232e7d56d941ecc38c49` |
| `gw-conflicting-evidence-resolution/chunk-2` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:c7257107314752f0144dddd0116335bf85c6f3b89fff346d23a70bcf11fbfd88` |
| `gw-source-freshness-citation/chunk-0` | 分类待审核 | gbt-51040-2023-release, groundwater-domain-definitions | 缺失，待定位 | `sha256:25bf6d94ab6f55b565e096312aa24701f72c0507b6edd0b9e54175a56190ba2f` |
| `gw-source-freshness-citation/chunk-1` | 分类待审核 | groundwater-domain-definitions, usgs-tm-1a1 | 缺失，待定位 | `sha256:4d349442cefd421d638902f834f1a766ea2e47bf22b14e3b970fdfb0db0f50f9` |
| `gw-source-freshness-citation/chunk-2` | 分类待审核 | gbt-51040-2023-release, groundwater-domain-definitions, usgs-tm-1a1 | 缺失，待定位 | `sha256:8b690a5589c83eb5a3834870143f5663d8ad16a93ce45e8be1aa3529072fb5d3` |
| `gw-untrusted-knowledge-safety/chunk-0` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:46df1c051316b60724468ab0500f888c27f4ce24f4c3e14f1f21dc4579338c88` |
| `gw-untrusted-knowledge-safety/chunk-1` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:02fd88174869c4b8b28167f5a9beab366976adf6e26a8ca412984fb770c2344f` |
| `gw-untrusted-knowledge-safety/chunk-2` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:11f99fb9e9f84ad804a57c19e8fd26d29ca379eb9db89197122d037c68357c08` |
| `gw-time-normalization/chunk-0` | METHOD 待审核 | epa-groundwater-statistics-guide, groundwater-domain-definitions | 缺失，待定位 | `sha256:dce43a36d1c1286b88f18bbeb80df38e65cf226cefde79c2801c8928a7c832d0` |
| `gw-time-normalization/chunk-1` | METHOD 待审核 | usgs-tm-1a1 | 缺失，待定位 | `sha256:1c0ae52e251891ae7f7bf73f1a1c4424bd38c00149d1694cafd5a6244cdaaaf2` |
| `gw-time-normalization/chunk-2` | METHOD 待审核 | epa-groundwater-statistics-guide, usgs-tm-1a1 | 缺失，待定位 | `sha256:57837ee894d52f2a161758fa57122b27947124f4e321a4732745b434c209444b` |
| `gw-sampling-frequency-bias/chunk-0` | METHOD 待审核 | usgs-tm-1a1 | 缺失，待定位 | `sha256:4608938518be07a9347d7db316fa644ed534131fac9c6702b3cab19657fbbdc2` |
| `gw-sampling-frequency-bias/chunk-1` | METHOD 待审核 | usgs-tm-1a1 | 缺失，待定位 | `sha256:f28c2e81a7ec219ba6c605b00ad067f5020c8c53b8bb85f85480bb1778bff133` |
| `gw-sampling-frequency-bias/chunk-2` | METHOD 待审核 | groundwater-domain-definitions, usgs-tm-1a1 | 缺失，待定位 | `sha256:e79d331d9b16f240108b89b97b9e57c0cc03a038604e2acac2573714fbed1b8a` |
| `gw-aggregation-choice/chunk-0` | METHOD 待审核 | epa-groundwater-statistics-guide, groundwater-domain-definitions | 缺失，待定位 | `sha256:5a353e63e815965b686fa709b3e04943f0e79dd4ca3f747e8e2b701d4d77349e` |
| `gw-aggregation-choice/chunk-1` | METHOD 待审核 | epa-groundwater-statistics-guide | 缺失，待定位 | `sha256:578b98b467644f5112d42a0979fadb1e69a6eb31fd5d75f87b484c19d05b04b0` |
| `gw-aggregation-choice/chunk-2` | METHOD 待审核 | epa-groundwater-statistics-guide | 缺失，待定位 | `sha256:be9dc2644683c84869bf0153165a3019ebe363e2fd408bb0313d72009f368198` |
| `gw-completeness-design/chunk-0` | METHOD 待审核 | gbt-51040-2023-release, usgs-tm-1a1 | 缺失，待定位 | `sha256:b5ed828b280eea96a29999e904a4e28be8b8201f5bbda847471866ac83040cd9` |
| `gw-completeness-design/chunk-1` | METHOD 待审核 | usgs-tm-1a1 | 缺失，待定位 | `sha256:014679de5d3cf1ae1079ead0fe28869e194d2011550f812e991c057fcac9d02a` |
| `gw-completeness-design/chunk-2` | DEMO 待审核 | gbt-51040-2023-release, groundwater-demo-assumptions | 缺失，待定位 | `sha256:9efdf625915043657e65a83ebef8456456a149786af1aa4c0563bf71edeb27f8` |
| `gw-linear-trend-diagnostics/chunk-0` | METHOD 待审核 | epa-groundwater-statistics-guide | 缺失，待定位 | `sha256:5b927e906a62456da17b2e9919562d8c4e712f43464e0e7148d9e91dbe050abe` |
| `gw-linear-trend-diagnostics/chunk-1` | METHOD 待审核 | epa-groundwater-statistics-guide | 缺失，待定位 | `sha256:47edda58c88d882d0a3e13fdbae693c69ffda5b41fd573ebf3687b1a3258ac97` |
| `gw-linear-trend-diagnostics/chunk-2` | METHOD 待审核 | epa-groundwater-statistics-guide, groundwater-domain-definitions | 缺失，待定位 | `sha256:9c972f690aff9569a864d59f172cbde53e0d5481fbff1896d7e386703848f259` |
| `gw-mann-kendall-cautions/chunk-0` | METHOD 待审核 | epa-groundwater-statistics-guide | 缺失，待定位 | `sha256:96c160bb9d7d8a7aa7194be1e69c0e9383d468da751381aa438fce49831baf7d` |
| `gw-mann-kendall-cautions/chunk-1` | METHOD 待审核 | epa-groundwater-statistics-guide | 缺失，待定位 | `sha256:ac8de202e3b4b122685d48247056d8cd52603bdc0016769ee04542d6e686765b` |
| `gw-mann-kendall-cautions/chunk-2` | METHOD 待审核 | epa-groundwater-statistics-guide, groundwater-domain-definitions | 缺失，待定位 | `sha256:0dbd54016d7aaf3744bfd5d323799d41d592746764e5ebd343e058eec0333c32` |
| `gw-mad-outlier-review/chunk-0` | METHOD 待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:6595d297e724c7238338a68c76ab8ca2a92eaa4fa286c16273f4bc16d35f4861` |
| `gw-mad-outlier-review/chunk-1` | METHOD 待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:8251c34439ccf7591cdd48ab1a689f0491c6a0c126c1ee48ac1125c34a64641a` |
| `gw-mad-outlier-review/chunk-2` | METHOD 待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:b5c1fbdddb8b855072e584bd29a2cf0a1240a22fea306604172ecde53041589d` |
| `gw-period-comparison-design/chunk-0` | METHOD 待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:4749c0c9c5101a2d889d798df0bccebd3ecd2551e8c4d535588cd6bcf198b5b3` |
| `gw-period-comparison-design/chunk-1` | METHOD 待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:a189e49cac5f66e72eee06987d720034adb70a76085e948b0382b139547a5f2d` |
| `gw-period-comparison-design/chunk-2` | METHOD 待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:a596c330052e6c1a5cc4dc67b480d8dd09dc05cfb4431203945f8fd49213ce6e` |
| `gw-indicator-direction/chunk-0` | METHOD 待审核 | usgs-circular-1217-box-a | 缺失，待定位 | `sha256:95b944d057a43fec8a798688b4c3be88905c3f70e29ae743caad4c926c11f639` |
| `gw-indicator-direction/chunk-1` | METHOD 待审核 | groundwater-domain-definitions, usgs-circular-1217-box-a | 缺失，待定位 | `sha256:f9e6d71464f46d795bdbb1567e76cfd3ce90d1b38a8fcbd96b6de88e8ad0dc56` |
| `gw-indicator-direction/chunk-2` | METHOD 待审核 | usgs-circular-1217-box-a | 缺失，待定位 | `sha256:e928b5b9b4afec5cd5a136a89e243748d1d85efa723723c2752607c333167650` |
| `gw-snapshot-selection-policy/chunk-0` | METHOD 待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:efc0c1cc5d7f98f1dd705f5995b933a48116978e125159883ca9e47e770d4636` |
| `gw-snapshot-selection-policy/chunk-1` | METHOD 待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:53e472a4f2e7988267781b0d86b532334a3379796f06be6ca1da812187f88266` |
| `gw-snapshot-selection-policy/chunk-2` | METHOD 待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:8d2288c5b5c7b1d4436c6a7a6bc1b9deca64b7410f33c6fbfbf59c62446f4831` |
| `gw-idw-validation/chunk-0` | METHOD 待审核 | groundwater-domain-definitions, qgis-interpolation-3-40 | 缺失，待定位 | `sha256:6984812d6cb666955dcc9c4ebcfb19236193de932ad7a723c635138a964bdf8a` |
| `gw-idw-validation/chunk-1` | METHOD 待审核 | groundwater-domain-definitions, qgis-interpolation-3-40 | 缺失，待定位 | `sha256:1d662df86e61e51592d765c8e9ffdd0df1551a7564e979ae2f4d3a3b8d5d9249` |
| `gw-idw-validation/chunk-2` | 产品断言待核 | qgis-interpolation-3-40 | 缺失，待定位 | `sha256:5e92f64a78b1ceda0cd0ebe78c55ad2dd8b9553c556951b8f823b52a4cce5fc6` |
| `gw-raster-resolution/chunk-0` | METHOD 待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:46a9583ebb979f4b2947d8df8f0b355b656f84d724a842b872063baab1238221` |
| `gw-raster-resolution/chunk-1` | METHOD 待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:14d574d9a9983cb9daddc85194bb3b40a813e85a0f7cae2f458d00c357c1fb61` |
| `gw-raster-resolution/chunk-2` | METHOD 待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:caf141213ef6d959bfa27d67fa73e151186cbb501b651acdfd7d2c6c4dc9f8e5` |
| `gw-boundary-effects/chunk-0` | METHOD 待审核 | usgs-circular-1224 | 缺失，待定位 | `sha256:2167e727fc7c75f903e64f30b7e9dd7a3ba5d1effd7a3b7e88c31233e0954fa0` |
| `gw-boundary-effects/chunk-1` | METHOD 待审核 | usgs-circular-1224 | 缺失，待定位 | `sha256:e2ba735b05512803b4abae0767c60e245391b67c1511001c6aca0102b63f63fd` |
| `gw-boundary-effects/chunk-2` | METHOD 待审核 | usgs-circular-1224 | 缺失，待定位 | `sha256:d649532a72ee6585a30bcfaa06d3760d3822be99f73fd9d493403ba2e16cb4c7` |
| `gw-darcy-sensitivity/chunk-0` | METHOD 待审核 | usgs-circular-1224 | 缺失，待定位 | `sha256:323a550e6ba1c5044c8490b0dbfc24b6d8c5ab080c58640ea3cbf9f6af33c5e3` |
| `gw-darcy-sensitivity/chunk-1` | METHOD 待审核 | usgs-circular-1224 | 缺失，待定位 | `sha256:9f6df3040574509854a6deacb2aeeefca05eedb9731d82ead291bf85ffa65212` |
| `gw-darcy-sensitivity/chunk-2` | DEMO 待审核 | groundwater-demo-assumptions, groundwater-domain-definitions, usgs-circular-1224 | 缺失，待定位 | `sha256:ef021f1657379b2c58d385aaf89dc495dd18299d19397590799eca87c1c7a7ca` |
| `gw-evidence-provenance/chunk-0` | METHOD 待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:05364e8416ae57b17b78dcd63f3d8726f8e6cfddd589df05da156d77e224f3e9` |
| `gw-evidence-provenance/chunk-1` | METHOD 待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:4ca2aa086aa6b681edac744618a098889ce58c350985be721676291a91d32488` |
| `gw-evidence-provenance/chunk-2` | METHOD 待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:4b566fd6473a31e5b7241cec29b2a58e80d30567ab824c4e47582b312bb7fe05` |
| `gw-pressure-transducer-records/chunk-0` | 分类待审核 | usgs-tm-1a1 | 缺失，待定位 | `sha256:974f82cdebd12a83bd9693a4fbadfe18f822445735fd43c3db422fd8c1539b0e` |
| `gw-pressure-transducer-records/chunk-1` | 分类待审核 | usgs-groundwater-levels-time-series | 缺失，待定位 | `sha256:84a67a5391783617c947a13747023a8ff7249b80760aa29aeb2ac6861b66a056` |
| `gw-pressure-transducer-records/chunk-2` | 分类待审核 | usgs-data-revisions-guidance | 缺失，待定位 | `sha256:f823288c97ee98fea41dfb6ade22e1ba210278205968a34276dee787f435ccb1` |
| `gw-barometric-temperature-effects/chunk-0` | 分类待审核 | usgs-tm-1a1 | 缺失，待定位 | `sha256:aa8e7126df8418aeb5f0e3d247f2eb0dc8d8a22c11d856a95b86102faba10217` |
| `gw-barometric-temperature-effects/chunk-1` | 分类待审核 | usgs-ofr-97-11-gwqap | 缺失，待定位 | `sha256:76e45e877fff3260108e097c524e27b3e4fa41173d01be62effba826f67b5daa` |
| `gw-barometric-temperature-effects/chunk-2` | 分类待审核 | usgs-circular-1217-essential | 缺失，待定位 | `sha256:b01681b3b0134ebf3ffa1d089ac02265ee08fa95ac41c386a34d9752ea94e8ba` |
| `gw-manual-calibration-checks/chunk-0` | 分类待审核 | usgs-tm-1a1 | 缺失，待定位 | `sha256:49ca43bd1c8ea40930757c6aa235963ec350f88bf85e8bfd43e2940ad15fbba1` |
| `gw-manual-calibration-checks/chunk-1` | 分类待审核 | usgs-ofr-97-11-gwqap, usgs-tm-1a1 | 缺失，待定位 | `sha256:ee8c7b033d1d60000dc9cb08f4be2c2964477c7047b0d9807c39a9642d9f11f3` |
| `gw-manual-calibration-checks/chunk-2` | 分类待审核 | usgs-ofr-97-11-gwqap | 缺失，待定位 | `sha256:f6e93a32234004e87c4447d142fb43c12c339a7de13ee79430a1e69aa4bb45ce` |
| `gw-dry-flowing-well-states/chunk-0` | 分类待审核 | usgs-tm-1a1 | 缺失，待定位 | `sha256:8699c4a61f162553b500a473f7c764293834e49195169138cf718183edebb022` |
| `gw-dry-flowing-well-states/chunk-1` | 分类待审核 | usgs-tm-1a1 | 缺失，待定位 | `sha256:a1b2d1926d7a9cf0adfdbe280712a280ac13d6fc91efa1e71659cdaed1bfb958` |
| `gw-dry-flowing-well-states/chunk-2` | 分类待审核 | usgs-tm-1a1 | 缺失，待定位 | `sha256:00523102bd8c68835e3af180fbf3fe9391c0119cb6fad86df28856788d1f9018` |
| `gw-static-pumping-recovery-state/chunk-0` | 分类待审核 | usgs-tm-1a1 | 缺失，待定位 | `sha256:c8d377781eee08099e2f7e9cf78705af9b45f9c620c5ce4138624967a968868d` |
| `gw-static-pumping-recovery-state/chunk-1` | 分类待审核 | usgs-tm-1a1 | 缺失，待定位 | `sha256:74b1c0259d04431f71a354a8857768d3b424303f4f6ef57814de6a22131a22a5` |
| `gw-static-pumping-recovery-state/chunk-2` | 分类待审核 | usgs-circular-1217-essential | 缺失，待定位 | `sha256:08e910f04afe01a64c3d695c8781a4f5ecf75051f6ef6ee86dd927d3a681eb19` |
| `gw-well-integrity-obstruction/chunk-0` | 分类待审核 | usgs-tm-1a1 | 缺失，待定位 | `sha256:a11964470014d69851ee7533d9fc9d9f0cec9701b25dae99fbfc2d84acd86216` |
| `gw-well-integrity-obstruction/chunk-1` | 分类待审核 | usgs-circular-1217-essential | 缺失，待定位 | `sha256:4c7813ebf60d982811d4324a1b1bbe0bef7a496cc173b25a76df149a5330ebb9` |
| `gw-well-integrity-obstruction/chunk-2` | 分类待审核 | usgs-furnished-records-policy | 缺失，待定位 | `sha256:d60d3b52e57f18ce3aba0404c2b3bd24021ad6bb5beac76b969ee9f717490c38` |
| `gw-telemetry-clock-transfer/chunk-0` | 分类待审核 | usgs-groundwater-levels-time-series | 缺失，待定位 | `sha256:84fb1387ed35070c9b5f9c4d4011b0bfc00730f06f38cdd757ce7ffcf41eff18` |
| `gw-telemetry-clock-transfer/chunk-1` | 分类待审核 | usgs-groundwater-levels-time-series, usgs-ofr-97-11-gwqap | 缺失，待定位 | `sha256:73196bad0c4fe8e417820aa0e8741bd63fe95a03c181bbd82c3dcf1c4a9955b2` |
| `gw-telemetry-clock-transfer/chunk-2` | 分类待审核 | usgs-ofr-97-11-gwqap | 缺失，待定位 | `sha256:d19b4820b6fe841d6e6fcab795bb11cd18614967a61d5c8f21938300930fb6e3` |
| `gw-sensor-range-resolution/chunk-0` | 分类待审核 | usgs-ofr-97-11-gwqap | 缺失，待定位 | `sha256:a5f9675707a84aaa2e4feb76475d99ac330df1d22d87f16aa991081556759b0a` |
| `gw-sensor-range-resolution/chunk-1` | 分类待审核 | usgs-tm-1a1 | 缺失，待定位 | `sha256:36a570dc50ecf89b41cade0c4345cf67fd1ad7146dc506bd559acdda2ff33375` |
| `gw-sensor-range-resolution/chunk-2` | 分类待审核 | usgs-circular-1217-essential | 缺失，待定位 | `sha256:2d987ea7842639417a56036382ef117a4f583c7505f01749cba1a2a29445ee8e` |
| `gw-quality-flags-qualifiers/chunk-0` | 分类待审核 | usgs-groundwater-levels-time-series | 缺失，待定位 | `sha256:0c4adcead4019a9bbbf02a42d7cdf25e8514c0d1fb770c1aecee17ba6658529a` |
| `gw-quality-flags-qualifiers/chunk-1` | 分类待审核 | usgs-groundwater-levels-time-series, usgs-water-memo-2049 | 缺失，待定位 | `sha256:7c8d9c6fd53b8796e192b2f97fc06da449384ee7a184d4c886043f0f09acb7fb` |
| `gw-quality-flags-qualifiers/chunk-2` | 分类待审核 | usgs-water-memo-2049 | 缺失，待定位 | `sha256:78ce8dead872d5ed97438908e75807af0624a78a14b655caf1fca79b77473b7b` |
| `gw-record-revision-audit/chunk-0` | 分类待审核 | usgs-water-memo-2049 | 缺失，待定位 | `sha256:3f4b1b1e3ebd276d13bb9afed39863397042566a19053f46e8b195d0acb1fa35` |
| `gw-record-revision-audit/chunk-1` | 分类待审核 | usgs-data-revisions-guidance | 缺失，待定位 | `sha256:f6360547b340fcdd5d1a22b05ee8f22891b50a25e6bb61d78fb30a0dc546e08c` |
| `gw-record-revision-audit/chunk-2` | 分类待审核 | usgs-ofr-97-11-gwqap | 缺失，待定位 | `sha256:22cfe06afacc2dfc3c6cbfdf0b0e696d66fd1ee147543157cad0d54f05c06f0a` |
| `gw-elevation-survey-datum/chunk-0` | 分类待审核 | usgs-circular-1217-essential | 缺失，待定位 | `sha256:291b10260feab45c6685d62bf874417cc3b0c0065c2e09e94c66f471ad90db72` |
| `gw-elevation-survey-datum/chunk-1` | 分类待审核 | usgs-circular-1217-essential, usgs-tm-1a1 | 缺失，待定位 | `sha256:aa81531d0ec90170212d41b29b31cdf25b28a383c6513f81959aa2c6e3f16d05` |
| `gw-elevation-survey-datum/chunk-2` | 分类待审核 | usgs-tm-1a1 | 缺失，待定位 | `sha256:c3c35b321b9bbecdb76d21e30cdf18f8594be928612c4059268f585184ba1693` |
| `gw-screen-construction-metadata/chunk-0` | 分类待审核 | usgs-tm-1a1 | 缺失，待定位 | `sha256:ae19dbf984f7d46be5d09d8d74ab808b1596592a970a3b32fb92bf425cc67a8f` |
| `gw-screen-construction-metadata/chunk-1` | 分类待审核 | usgs-circular-1224 | 缺失，待定位 | `sha256:aa9f6cbce3c7f33dc11664b62f5a07e06826c4e2027ae92e9b339e9cf24fbd27` |
| `gw-screen-construction-metadata/chunk-2` | 分类待审核 | usgs-circular-1224, usgs-tm-1a1 | 缺失，待定位 | `sha256:0ed109e5993dc2d76697455dc85b358159bd6d156c12c1a97bbd753f4b53035e` |
| `gw-well-replacement-continuity/chunk-0` | 分类待审核 | usgs-tm-1a1 | 缺失，待定位 | `sha256:04208f474452adde3d6deac4322565bb634298a4d57a6810ee97c5127a086a0e` |
| `gw-well-replacement-continuity/chunk-1` | 分类待审核 | usgs-tm-1a1 | 缺失，待定位 | `sha256:4b8034924a164d04f28548ded00de4577e9348fcb52420e6fa7e909341fb291d` |
| `gw-well-replacement-continuity/chunk-2` | 分类待审核 | usgs-tm-1a1 | 缺失，待定位 | `sha256:20164b6a8bd3fb1ba25815be9e6f2eb8e12e9501aff1ea69e9d12c72affaccf0` |
| `gw-network-change-control/chunk-0` | 分类待审核 | usgs-circular-1217-essential | 缺失，待定位 | `sha256:6ffe22c7a8115f999e15b5a5873da717b1c56946845e86c19666302994eb5c03` |
| `gw-network-change-control/chunk-1` | 分类待审核 | usgs-circular-1217-essential | 缺失，待定位 | `sha256:78dd750f602b1ac567d4e572482880dfd8035faf5a81f3b19bd93092821a3e80` |
| `gw-network-change-control/chunk-2` | 分类待审核 | usgs-circular-1217-essential | 缺失，待定位 | `sha256:a20b1039a303cdecce25e0416840520cbfb0ad16d49d9fdd6890abfc24ecf570` |
| `gw-source-acceptance-provenance/chunk-0` | 分类待审核 | usgs-furnished-records-policy | 缺失，待定位 | `sha256:68337c2fc0231895c03cb5741c34d56ef111a9ae03abc8d24164beebdb7a3b16` |
| `gw-source-acceptance-provenance/chunk-1` | 分类待审核 | usgs-furnished-records-policy | 缺失，待定位 | `sha256:9373d9ed2d02094537383478ba0f925dbfb068175ccfe11c7dde9c0f21c6e703` |
| `gw-source-acceptance-provenance/chunk-2` | 分类待审核 | usgs-furnished-records-policy | 缺失，待定位 | `sha256:c1737a0d8c0ff23ac46b7a311baa9ea700343b34048fad285b25f7c5c4fa7001` |
| `gw-scenario-seasonal-decline/chunk-0` | DEMO 待审核 | epa-groundwater-statistics-guide, groundwater-demo-assumptions | 缺失，待定位 | `sha256:208ddea609d6ba4f89fecbb72aa33d7f2f06b86eb9177b12a6cf067c79e2cb91` |
| `gw-scenario-seasonal-decline/chunk-1` | DEMO 待审核 | epa-groundwater-statistics-guide, groundwater-demo-assumptions, groundwater-domain-definitions | 缺失，待定位 | `sha256:41ee85889a842caf29184010284f92ef7890dd100fd5f732c69e2f983b9292a5` |
| `gw-scenario-seasonal-decline/chunk-2` | DEMO 待审核 | epa-groundwater-statistics-guide, groundwater-demo-assumptions, groundwater-domain-definitions | 缺失，待定位 | `sha256:98ef3606eb9aef91b9b4b9e9ee5ba327108bb791e43c53d8fada1ea900d5c96b` |
| `gw-scenario-recovery-recharge/chunk-0` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:77f085885ace560bfdb4d8c6f4732172832f95c21f41f2a6c7f43b6653d74126` |
| `gw-scenario-recovery-recharge/chunk-1` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:17796947e3cca5eb9f3f6547982998b5b5a3fc7d5f49491eff6245f63f963494` |
| `gw-scenario-recovery-recharge/chunk-2` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:3c74c482c8b58ecdd0e625435460a7cc761021431d980ae268738faf9f01f680` |
| `gw-scenario-pumping-disturbance/chunk-0` | DEMO 待审核 | groundwater-demo-assumptions, usgs-tm-1a1 | 缺失，待定位 | `sha256:d61fbc08a6fa0c3a1754f8970c62d9ff77ae6b9e30954a51d13a01dbd7bb8d2c` |
| `gw-scenario-pumping-disturbance/chunk-1` | DEMO 待审核 | groundwater-demo-assumptions, usgs-tm-1a1 | 缺失，待定位 | `sha256:2bae765347bf571fb32ca652c5cf5bb7e8ed7371260fd45748f133c3a20ca80f` |
| `gw-scenario-pumping-disturbance/chunk-2` | DEMO 待审核 | groundwater-demo-assumptions, groundwater-domain-definitions, usgs-tm-1a1 | 缺失，待定位 | `sha256:839f9de1cecd0e0a351d04d0bac42bedbb775dc9efec2c3b8925aeec7b4c36a7` |
| `gw-scenario-sensor-drift/chunk-0` | DEMO 待审核 | groundwater-demo-assumptions, usgs-tm-1a1 | 缺失，待定位 | `sha256:7d59f5202e8d23250c1c7cd05a59ae27dec0e7b193db2e9611982e47bb6e402e` |
| `gw-scenario-sensor-drift/chunk-1` | DEMO 待审核 | groundwater-demo-assumptions, usgs-tm-1a1 | 缺失，待定位 | `sha256:7953c8ea8348b03335f19bf3d11fd5e3e8d249bc88e861f7e0f3ea16125ebc1a` |
| `gw-scenario-sensor-drift/chunk-2` | DEMO 待审核 | groundwater-demo-assumptions, usgs-tm-1a1 | 缺失，待定位 | `sha256:4f4c357cdc752a1bf4f4a13f4a4109c73a8866ad81582095ffded0b95e1ddbee` |
| `gw-scenario-datum-change/chunk-0` | DEMO 待审核 | groundwater-demo-assumptions, usgs-tm-1a1 | 缺失，待定位 | `sha256:653d5f889d6286fdcaa711c0f2b3ffa0465d1e860bded370df9160e70f5af180` |
| `gw-scenario-datum-change/chunk-1` | DEMO 待审核 | groundwater-demo-assumptions, usgs-tm-1a1 | 缺失，待定位 | `sha256:ea85918e0ce7a299ed5b49ca4044877eda5cfb7429d6c548648ce14280c74533` |
| `gw-scenario-datum-change/chunk-2` | DEMO 待审核 | groundwater-demo-assumptions, usgs-tm-1a1 | 缺失，待定位 | `sha256:f1fb62689ff1bbe0f61a7515d3137d984027b6d38d8bd47ba888c63bdc485716` |
| `gw-scenario-missing-months/chunk-0` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:512b8fea598ef501f8384d10f6ba9e28711c62b1ebfed80e1a9b1e0d541afc1f` |
| `gw-scenario-missing-months/chunk-1` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:d2189aaeebb5ac0580299a6b285e1e2479092d3e8bd4cb3f20e36cd835bf120f` |
| `gw-scenario-missing-months/chunk-2` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:a40634a25af4290e8add6a4dccb38b30aecf4bf438ceafe249ce7f38b9d5194f` |
| `gw-scenario-duplicate-records/chunk-0` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:84819abf6e5d28f5ae2ef54d78a3b628b1d8cf639b6efc0d03263556b7524ad0` |
| `gw-scenario-duplicate-records/chunk-1` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:dc0b6b2cbc2b9d09e7a9bd765c6e4e0a4e05a9c13b28ed2fc757822ab9772316` |
| `gw-scenario-duplicate-records/chunk-2` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:71f5287f570f0136729d3dccf55e01d1fb7b7450cecf95dcca7020572a493eba` |
| `gw-scenario-small-sample/chunk-0` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:26c3e30ddb4bf293c42ca5ce8f9cd13ae51a8a55dc646f2740bd47dea5519679` |
| `gw-scenario-small-sample/chunk-1` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:adf3021d7e49e7194228a9cf8487e86451a34956a8ffb2547036c9a72c8d84ea` |
| `gw-scenario-small-sample/chunk-2` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:fc0c52a06b3bcd0a642ae76bc3d90c723e4f4c8b4b856eaea729062a1659f1a6` |
| `gw-scenario-stale-snapshot/chunk-0` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:6a8f13c200943804d7deece145857de2ff804e5766cf62c08c9c888a5aceaa10` |
| `gw-scenario-stale-snapshot/chunk-1` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:e5836a1257fc542557dd90d5cbbea17b86dd1743c0cd319309fdaf17cd837ecf` |
| `gw-scenario-stale-snapshot/chunk-2` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:b7fd8a48cf24e4f5c092d0095a532e6b74a8b228f61c7616c4d3c29f35d23b3c` |
| `gw-scenario-crs-mismatch/chunk-0` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:ed573e4bcc3d0e3ef53027a7413030588cfc3f486edfe32a3dd47962dc9800f7` |
| `gw-scenario-crs-mismatch/chunk-1` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:c7d6f739aed619da598541957fd56caa7667d99074098997acb24fd0ddea3787` |
| `gw-scenario-crs-mismatch/chunk-2` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:bd3a371a88ebf9adb4b1a4ac05645c4ce85863fcc962b498304c36036127d4f4` |
| `gw-scenario-sparse-edge/chunk-0` | DEMO 待审核 | groundwater-demo-assumptions, qgis-interpolation-3-40 | 缺失，待定位 | `sha256:62b5376ecf40c82e519af7a3e2fcad9fd4cfa3dfbfb77318b8e98018f823b9df` |
| `gw-scenario-sparse-edge/chunk-1` | DEMO 待审核 | groundwater-demo-assumptions, groundwater-domain-definitions, qgis-interpolation-3-40 | 缺失，待定位 | `sha256:b6da974373606f15bc15c714a735ec8ffdb839a9d6392d9ae1525390bf6b0a26` |
| `gw-scenario-sparse-edge/chunk-2` | DEMO 待审核 | groundwater-demo-assumptions, qgis-interpolation-3-40 | 缺失，待定位 | `sha256:7cd8ccf24a791ab150508e97e5bc156750504f836e081f464ba9913fd5395839` |
| `gw-scenario-idw-bullseye/chunk-0` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:54d3f9f4b18c57aa47012a3cea89290e03f9d29c5b56ea1825ff9ea76bf213e5` |
| `gw-scenario-idw-bullseye/chunk-1` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:67f750e727125c654d9e0909861607869bce1cad08f64388ce80eb47209e12e4` |
| `gw-scenario-idw-bullseye/chunk-2` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:e1165a51743063a91378506466bef45f1ee94ae088744bf76577c4d8cfbf0b83` |
| `gw-scenario-parameter-range/chunk-0` | DEMO 待审核 | groundwater-demo-assumptions, usgs-circular-1224 | 缺失，待定位 | `sha256:98c732676a5fe45cfbb378dd51f8e59a640a1e18a409184cee4daa8642b4a396` |
| `gw-scenario-parameter-range/chunk-1` | DEMO 待审核 | groundwater-demo-assumptions, usgs-circular-1224 | 缺失，待定位 | `sha256:12629fc0207166cca01a3c9fbda349f126cac476f05ed40da3b239b30071139b` |
| `gw-scenario-parameter-range/chunk-2` | DEMO 待审核 | groundwater-demo-assumptions, usgs-circular-1224 | 缺失，待定位 | `sha256:05394d924d7cc82f72909360d9ea15aec1acc46804ad2d4d600a9c99f6d70272` |
| `gw-scenario-conflicting-results/chunk-0` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:33e5382401bb9e37dde000ce0db6aceea2778c4e22ced234cea54bffe06369de` |
| `gw-scenario-conflicting-results/chunk-1` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:657a1b65056ae3c03e5a39a8dc0d65e74ecb37d8ad0eda1ac9ccebeb96ec02c8` |
| `gw-scenario-conflicting-results/chunk-2` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:446567fe8ea4a8f05a704aeb543dc3379dbdcd56e01227039cf1128098a8046d` |
| `gw-scenario-flow-direction-shift/chunk-0` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:e001b6c7b5317560a2e430274426869270bfec8e464a7f314fbef83f240cc069` |
| `gw-scenario-flow-direction-shift/chunk-1` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:b4494778a8daa5ea10116f8b1a8ce4f47978fab7e4e6bb85a52049bf58214bea` |
| `gw-scenario-flow-direction-shift/chunk-2` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:aff5176183887c77a6e955dc4f28b4a2926517369ec639c151dc0e6719cdf058` |
| `gw-task-routing/chunk-0` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:5b2fc07408330a3425ee2e5c82aeed337de10cc085529eb3ece5099cc5f0112b` |
| `gw-task-routing/chunk-1` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:e848e61ebcd3c705dda0fbab977feb2379f30cd4405f3bd8bc09e210f1693d7f` |
| `gw-task-routing/chunk-2` | 产品断言待核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:c9f1eebc235849e5d57b75e514904c0122fe404b55edaf67828720aace04a2ba` |
| `gw-water-level-head/chunk-0` | 分类待审核 | usgs-gwpd | 缺失，待定位 | `sha256:97117407b3459b0272b343eb8bf5dd381374c64021c603057ddca574388f3fbb` |
| `gw-water-level-head/chunk-1` | 分类待审核 | usgs-circular-1217-box-a | 缺失，待定位 | `sha256:e2e92aea16bb0a63559bd1990d7e634b6ba0821a93cc61fc23eb881fad6dc8d3` |
| `gw-water-level-head/chunk-2` | 分类待审核 | gbt-51040-2023-release, usgs-circular-1217-box-a | 缺失，待定位 | `sha256:163fabaf26481e440db6f0aac755c8e5413a80ebb25c26f16155faeae17b2f06` |
| `gw-monitoring-data-contract/chunk-0` | 分类待审核 | gbt-51040-2023-release, groundwater-domain-definitions | 缺失，待定位 | `sha256:cb1677d8630fe138e7d92199ad6262a5548fdb8c806c18b476b71e824991aeb8` |
| `gw-monitoring-data-contract/chunk-1` | 分类待审核 | usgs-ofr-97-11-gwqap | 缺失，待定位 | `sha256:6ac7f69734bcc7dca769b57962114ca48ea0b541070e4777dcc837df56f149d0` |
| `gw-monitoring-data-contract/chunk-2` | 分类待审核 | usgs-gwpd, usgs-ofr-97-11-gwqap | 缺失，待定位 | `sha256:d65da76ee34fd90a6d4b13516247df0dd99e6ddfd5648454dc690a0ddb132edd` |
| `gw-trend-preparation/chunk-0` | 分类待审核 | groundwater-domain-definitions, usgs-california-groundwater-levels | 缺失，待定位 | `sha256:2cf0d68219cd85f3c596147eed2e0b200c9c22bf7d32a36509568b80d4af500c` |
| `gw-trend-preparation/chunk-1` | 分类待审核 | usgs-groundwater-trends-2020 | 缺失，待定位 | `sha256:c0490607f715e70077e377fa16fb3d114ebc833ca704543712cefe500bd748e3` |
| `gw-trend-preparation/chunk-2` | 分类待审核 | usgs-california-groundwater-levels | 缺失，待定位 | `sha256:52bfb0badeb4004a0f55aa3d061892afba6b4a805860fcfb419c9d1d1aab728c` |
| `gw-trend-methods/chunk-0` | METHOD 待审核 | groundwater-domain-definitions, usgs-groundwater-trends-2020 | 缺失，待定位 | `sha256:69399672a7268c52a5659e41115d218190b5bcfddb6b6775d4ed6dcdee57c2b2` |
| `gw-trend-methods/chunk-1` | METHOD 待审核 | epa-groundwater-statistics-guide, groundwater-domain-definitions | 缺失，待定位 | `sha256:793f068244a58b30f44b6e666254eb7c530c1903557e8f7c73db26e0862694e4` |
| `gw-trend-methods/chunk-2` | METHOD 待审核 | epa-groundwater-statistics-guide, groundwater-domain-definitions, usgs-groundwater-trends-2020 | 缺失，待定位 | `sha256:adf1f93359739ec0195a365f864b4a0eba557a72376dfabf3d436415972b7a31` |
| `gw-trend-interpretation/chunk-0` | 分类待审核 | usgs-circular-1217-box-a, usgs-gwpd | 缺失，待定位 | `sha256:85b81379b3944deebb3f12fd42a2e782105383a1e1fa9a961a3f8d5135741ba2` |
| `gw-trend-interpretation/chunk-1` | 分类待审核 | epa-groundwater-statistics-guide, usgs-groundwater-trends-2020 | 缺失，待定位 | `sha256:1d6e6c004303d4a929842497233050a003ac26c6bf0171bb807329c4d9fe2a24` |
| `gw-trend-interpretation/chunk-2` | 分类待审核 | usgs-groundwater-streamflow-trends | 缺失，待定位 | `sha256:1c07b30b86cb0d9a446740197bf47e8a747f17fcd48b9e30c6bd719f67f5fcfe` |
| `gw-flow-input-and-snapshot/chunk-0` | 分类待审核 | groundwater-domain-definitions, usgs-circular-1217-box-a | 缺失，待定位 | `sha256:95cfaaaeadfc06fb196428031836ea23fadb5fcf7ffc2a5ea19b27ca4c3af87b` |
| `gw-flow-input-and-snapshot/chunk-1` | 分类待审核 | groundwater-domain-definitions, qgis-interpolation-3-10, usgs-circular-1217-box-a | 缺失，待定位 | `sha256:2dd50bd413d93896fd821464fd13659943beafb03341a0300e684c4e1c09fbeb` |
| `gw-flow-input-and-snapshot/chunk-2` | 分类待审核 | groundwater-domain-definitions, qgis-interpolation-3-10 | 缺失，待定位 | `sha256:00b8dc3048157aa133f3c499ad3734dcbbe7363d9cf5e74a2aa31f25445a8b2b` |
| `gw-idw-and-gradient/chunk-0` | METHOD 待审核 | groundwater-domain-definitions, qgis-interpolation-3-10 | 缺失，待定位 | `sha256:ca0801c1eea31dfb3ae97494f4045608853440dd929a4e1d321194f7f2742117` |
| `gw-idw-and-gradient/chunk-1` | METHOD 待审核 | qgis-interpolation-3-10 | 缺失，待定位 | `sha256:4ac994b5e1a9394fd3fd17cf56d424b51034d87a3fc366940bb8a50143679490` |
| `gw-idw-and-gradient/chunk-2` | METHOD 待审核 | groundwater-domain-definitions, usgs-circular-1217-box-a | 缺失，待定位 | `sha256:28b752613405fa4c06c3f0cb9fa893ddc24cb747b98f8cfac5a8eb2726bdfc75` |
| `gw-darcy-velocity/chunk-0` | METHOD 待审核 | groundwater-domain-definitions, usgs-sir-2016-5021 | 缺失，待定位 | `sha256:9b1490c3848d9dec469695d665d9659d26f2a8b87b92a8c17aa5f1a0c3fcada2` |
| `gw-darcy-velocity/chunk-1` | METHOD 待审核 | usgs-wri-95-4015 | 缺失，待定位 | `sha256:1a91b9771cbbb1f0b60e417244692f289b6bdbe79a3f970895d7a97aa62c7c1d` |
| `gw-darcy-velocity/chunk-2` | METHOD 待审核 | groundwater-domain-definitions, usgs-sir-2016-5021 | 缺失，待定位 | `sha256:960c19fdbde75ee8d7519ac572bd38311e248e77174f25811fa88451ece37952` |
| `gw-demo-playbook/chunk-0` | DEMO 待审核 | groundwater-demo-assumptions, groundwater-domain-definitions | 缺失，待定位 | `sha256:3ce5ba4ba13dc42f82a077400619f8b91ea50f83d0b6dcb7afbf322ef29dc05a` |
| `gw-demo-playbook/chunk-1` | DEMO 待审核 | groundwater-demo-assumptions, groundwater-domain-definitions | 缺失，待定位 | `sha256:9ad98c4d1883761abe0fde55031625eff9051ced02a10973b19c3d5eb984db44` |
| `gw-demo-playbook/chunk-2` | DEMO 待审核 | groundwater-demo-assumptions | 缺失，待定位 | `sha256:edce65ee8848c95a867f5596b2472d4838af4605189f722ec4c49cf30137b64c` |
| `gw-aquifer-framework/chunk-0` | 分类待审核 | usgs-aquifers-groundwater | 缺失，待定位 | `sha256:9997d82624a86be13aaa9c2b386be6e05488ef185e266d0f0af206ed13753360` |
| `gw-aquifer-framework/chunk-1` | 分类待审核 | usgs-groundwater-discharge-cycle | 缺失，待定位 | `sha256:7232e563b6406a1405442c31af0d7d8767d565d070d8023b18ca158e4ec0568e` |
| `gw-aquifer-framework/chunk-2` | 分类待审核 | usgs-twri-3-b2 | 缺失，待定位 | `sha256:f70e4e4dfb3c72606b8dd3f2eaaed7dac69edc029048997be08644caeb37a4a9` |
| `gw-recharge-discharge-context/chunk-0` | 分类待审核 | usgs-groundwater-discharge-cycle | 缺失，待定位 | `sha256:091fcb3668998ba7777dff9108826abe3b42dbad7063b820a60d9a45d118a71f` |
| `gw-recharge-discharge-context/chunk-1` | 分类待审核 | usgs-rivers-groundwater | 缺失，待定位 | `sha256:54fbba1d6f22ddbcf88b604fd3675fb3043b3dc0236d5209a9ab5bb192b9fa40` |
| `gw-recharge-discharge-context/chunk-2` | 分类待审核 | usgs-aquifers-groundwater | 缺失，待定位 | `sha256:ebd3f35f0d5cb13972ec02c6be8045c97092248cb62ea63a62620db1644d41c8` |
| `gw-well-representativeness/chunk-0` | 分类待审核 | usgs-ofr-95-398 | 缺失，待定位 | `sha256:5815010c1db1017cd1b8320d46e098b95520510ac46cc75c4792b0ea502ff584` |
| `gw-well-representativeness/chunk-1` | 分类待审核 | usgs-circular-1217-essential | 缺失，待定位 | `sha256:6638353ffbaefefd0f8e9782d3de26ff87458fdaaec7a01df5f377554ab9d0fa` |
| `gw-well-representativeness/chunk-2` | 分类待审核 | gbt-51040-2023-release | 缺失，待定位 | `sha256:3a15a778dbb0a0172b2b5913a1f2b0fc0c3de69074fb9694d30fe3ae90c956bb` |
| `gw-measurement-quality-control/chunk-0` | 分类待审核 | usgs-gwpd | 缺失，待定位 | `sha256:a60720826df524d05a341cd0270b23d92110f1985b8d2560e5be7025ed465a77` |
| `gw-measurement-quality-control/chunk-1` | 分类待审核 | usgs-furnished-records-policy | 缺失，待定位 | `sha256:62a7718d337e67e0379d97b53587512fd3f55c089c4c7bd9880a0b583848fc4d` |
| `gw-measurement-quality-control/chunk-2` | 分类待审核 | usgs-data-revisions-guidance | 缺失，待定位 | `sha256:7a610a1b9e5277a4ce66ede8984bc90f0e7457f0093079b3ececed045b58ea1a` |
| `gw-time-series-seasonality/chunk-0` | 分类待审核 | usgs-california-groundwater-levels | 缺失，待定位 | `sha256:15bfcb31eb12757ec3b929e63de427ef3c095c3d94c04fadafafb85b09e507bd` |
| `gw-time-series-seasonality/chunk-1` | 分类待审核 | epa-unified-guidance, groundwater-domain-definitions | 缺失，待定位 | `sha256:40c315cf43a155dc11987d8ce19148a4f4319e53598f6d090d443ba6ffe74b1f` |
| `gw-time-series-seasonality/chunk-2` | 分类待审核 | usgs-ofr-97-11-gwqap | 缺失，待定位 | `sha256:6ef66c25c10162abb0f9f8fdf1a6218c9dc7e4cd961e35884380aa1dc5cd3fdc` |
| `gw-missing-duplicate-review/chunk-0` | 分类待审核 | groundwater-domain-definitions, usgs-time-series-guidance | 缺失，待定位 | `sha256:09bb4ec57f64c8d4d0b6d747fe83139c9826ceaa108502c74e7ac66d90549006` |
| `gw-missing-duplicate-review/chunk-1` | 分类待审核 | usgs-time-series-guidance | 缺失，待定位 | `sha256:988d2d3175a904c37d92b07f5f0535768ffc661198e9922d5377509b3d69b581` |
| `gw-missing-duplicate-review/chunk-2` | 分类待审核 | usgs-ofr-97-11-gwqap | 缺失，待定位 | `sha256:24de310341389193548ba4e722797127ce733a3040767b40750a7e62c0d32c28` |
| `gw-trend-period-design/chunk-0` | 分类待审核 | usgs-groundwater-trends-2020 | 缺失，待定位 | `sha256:9578bf9716a4c2cf9a5c97cb5dc6186df4f61af8cb7bf39bd506c9f5973aad7b` |
| `gw-trend-period-design/chunk-1` | 分类待审核 | usgs-groundwater-trends-2020 | 缺失，待定位 | `sha256:4a7950a55a28c17d37ee3898847508304ac54d399346f0a0f7365fcab9f4a0b5` |
| `gw-trend-period-design/chunk-2` | 分类待审核 | usgs-groundwater-trends-2020 | 缺失，待定位 | `sha256:47c1f220e9dee877f0aeb9fa123b21ffa7597ea0bba8ee7c83150c912f81f855` |
| `gw-cross-well-comparison/chunk-0` | 分类待审核 | usgs-circular-1217-essential | 缺失，待定位 | `sha256:42975429bd8f14d093c6fa8f437a02731b034f257ecb44dd9cdc884e7c26d5d1` |
| `gw-cross-well-comparison/chunk-1` | 分类待审核 | gbt-51040-2023-release, groundwater-domain-definitions | 缺失，待定位 | `sha256:f6bf4bec356d63cdf6e74210ec580614edd6697e825c64e604f52fbec3e88492` |
| `gw-cross-well-comparison/chunk-2` | 分类待审核 | usgs-groundwater-streamflow-trends | 缺失，待定位 | `sha256:e8d2eb9ae1c6df72c4f0b5d71acf3431f17aa530a8bcc9410ad3f0e2a583a12f` |
| `gw-snapshot-consistency/chunk-0` | 分类待审核 | groundwater-domain-definitions, usgs-ofr-97-11-gwqap | 缺失，待定位 | `sha256:f2ad69f65d88e5cf735c0291b8614d8add76ee5c163f04e00d6f60ba3ce10bfc` |
| `gw-snapshot-consistency/chunk-1` | 分类待审核 | groundwater-domain-definitions, usgs-ofr-97-11-gwqap | 缺失，待定位 | `sha256:06e124ab44c9eb0ec38689cd108b2b35e992221196ff377e59f29bfdcaab8194` |
| `gw-snapshot-consistency/chunk-2` | 分类待审核 | groundwater-domain-definitions, usgs-ofr-97-11-gwqap | 缺失，待定位 | `sha256:c2f7deed5b685ec80b2179fc3271cf835105701865ea629289cb4ffaf74b7d32` |
| `gw-coordinate-systems/chunk-0` | 分类待审核 | qgis-interpolation-3-10 | 缺失，待定位 | `sha256:16844d38ed06af8270bdb5f454caab142c954b41a0b41062467486c0d3e90934` |
| `gw-coordinate-systems/chunk-1` | 分类待审核 | groundwater-domain-definitions, qgis-interpolation-3-10, usgs-gwpd | 缺失，待定位 | `sha256:c56d08eba7102ec1d90576df2bbdec40f3c427273ce0903855dbc328b2e87795` |
| `gw-coordinate-systems/chunk-2` | 分类待审核 | usgs-gwpd | 缺失，待定位 | `sha256:a9ac0c956b0bb0af91f2ca9c38292b225aae9665ef428ee450942fc0cb8778f1` |
| `gw-spatial-sampling-coverage/chunk-0` | 分类待审核 | groundwater-domain-definitions, qgis-interpolation-3-10 | 缺失，待定位 | `sha256:b28a02c6a510c0a61531eb4630d02b3565c9b003d6bcb172b1010b49f5207d45` |
| `gw-spatial-sampling-coverage/chunk-1` | 分类待审核 | qgis-interpolation-3-10 | 缺失，待定位 | `sha256:8fef3ef4f9acb46f8d29e78d7c7940ccfacdca894a9d8175b9acfc4f98c45a78` |
| `gw-spatial-sampling-coverage/chunk-2` | 分类待审核 | usgs-circular-1217-essential | 缺失，待定位 | `sha256:efa38a93125e02241dcfdc224a9c30f1ab47d6147ba0dec76ab8d8f914403881` |
| `gw-idw-sensitivity/chunk-0` | 分类待审核 | qgis-interpolation-3-10 | 缺失，待定位 | `sha256:bb74f59d7ff6483b0ed2eabb9415742c5eb69f3f660d79962e086f272f65382c` |
| `gw-idw-sensitivity/chunk-1` | 分类待审核 | groundwater-domain-definitions, qgis-interpolation-3-10 | 缺失，待定位 | `sha256:10d5fe2cd1392ba76c73c815cd7b86f8fa1c28adc993b9c82094583ea58fe5ad` |
| `gw-idw-sensitivity/chunk-2` | 分类待审核 | qgis-interpolation-3-10 | 缺失，待定位 | `sha256:5f7d90fa2a9dba9056f58fd3b9a24db40d4b0d8b067d23b391aeae96d77b9dea` |
| `gw-gradient-quality/chunk-0` | 分类待审核 | usgs-circular-1217-box-a | 缺失，待定位 | `sha256:77ab596d3c72e9e1e6af4c7cd665a4ee3132505fb7bc39b9d090dd34ffcb0796` |
| `gw-gradient-quality/chunk-1` | 分类待审核 | groundwater-domain-definitions, qgis-interpolation-3-10 | 缺失，待定位 | `sha256:990c9a71b34babcc0ad8019306691bdb8871c32868343906b8f914befc974119` |
| `gw-gradient-quality/chunk-2` | 分类待审核 | usgs-groundwater-discharge-cycle | 缺失，待定位 | `sha256:c95cbd8c4087428f15ba6b823c31bc4fb7da567fe892da94deb042c7519bce42` |
| `gw-hydraulic-conductivity/chunk-0` | 分类待审核 | usgs-sir-2016-5021 | 缺失，待定位 | `sha256:1bd35b9a3e43ab561b682cae7c46084207990d1c169c9a31b212caba8d698b4b` |
| `gw-hydraulic-conductivity/chunk-1` | 分类待审核 | usgs-gwpd | 缺失，待定位 | `sha256:020dae70864bf6900f56ae82c435aa278cb4053f9a0c62c71936dd6054ab3db1` |
| `gw-hydraulic-conductivity/chunk-2` | 分类待审核 | usgs-twri-3-b2 | 缺失，待定位 | `sha256:ca4ee1f1914d3b1bbcd618564a7d6d37e9a68c97e147575a4ed3c2e9b2a50cc1` |
| `gw-effective-porosity/chunk-0` | 分类待审核 | usgs-wri-97-4225 | 缺失，待定位 | `sha256:47fa5d2f7f6b9fae92a003d32bc58847fbff414ba06e589bf29207b3efb1d8a8` |
| `gw-effective-porosity/chunk-1` | 分类待审核 | usgs-wri-95-4015 | 缺失，待定位 | `sha256:a40a6fd04ea3af9c9ffb14c9d03ea09b2d9949b747be7ab189fb3d779ba34d56` |
| `gw-effective-porosity/chunk-2` | 分类待审核 | usgs-wri-97-4225 | 缺失，待定位 | `sha256:534460ba5ad7705fa86fca2b104dc4fbdd17cce81e49e2b2e29963e05245c6eb` |
| `gw-flow-result-interpretation/chunk-0` | 分类待审核 | usgs-circular-1217-box-a, usgs-sir-2016-5021, usgs-wri-95-4015 | 缺失，待定位 | `sha256:7cd33e339675275fcdb7117265ea7f4f465e84cdbb41d5e6d927c5d9f2cf6d0f` |
| `gw-flow-result-interpretation/chunk-1` | 分类待审核 | groundwater-domain-definitions, usgs-circular-1217-box-a | 缺失，待定位 | `sha256:8e2bb7fbb134b5441909842a5b26c19e52b93866a278e1b3433e20a1cf6bb9e5` |
| `gw-flow-result-interpretation/chunk-2` | 分类待审核 | usgs-sir-2016-5021, usgs-wri-95-4015 | 缺失，待定位 | `sha256:d631805a6fe9fc3a7ca01977be57cf88f2c8eb2e4b328d3992870df53398f01b` |
| `gw-uncertainty-communication/chunk-0` | 分类待审核 | usgs-ofr-97-11-gwqap | 缺失，待定位 | `sha256:53469c670485d8779418838bd3a0e4894fe7ce558f05055c723c213b6fa890b2` |
| `gw-uncertainty-communication/chunk-1` | 分类待审核 | epa-groundwater-statistics-guide | 缺失，待定位 | `sha256:24c4f9f9d2f2fe219b26e6a85431e852f58a8ee57eb807c55f63e205793dd164` |
| `gw-uncertainty-communication/chunk-2` | 分类待审核 | groundwater-domain-definitions, qgis-interpolation-3-10 | 缺失，待定位 | `sha256:10b53008bc31321178821a78d80b5657fff6fdd3fc1056358f1a03b058d6a2f2` |
| `gw-analysis-checklists/chunk-0` | 分类待审核 | groundwater-domain-definitions, usgs-gwpd | 缺失，待定位 | `sha256:478932204c3d6c461ed64a055c5d794685170038b1ef520729602e47f8f39cae` |
| `gw-analysis-checklists/chunk-1` | 分类待审核 | groundwater-domain-definitions, usgs-gwpd | 缺失，待定位 | `sha256:97024d28e741af2cfdd33cc8b8eb22e46cbbbea2aef45e2051ca52d009a3fb25` |
| `gw-analysis-checklists/chunk-2` | 分类待审核 | groundwater-domain-definitions, usgs-gwpd | 缺失，待定位 | `sha256:614f44c23da215cc2d039266222f97dfce7085f8ee9ad4906089a0638fa2bb79` |
| `gw-question-patterns/chunk-0` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:3d50063f22b9f620892dcd12ba306d113a477c01ab74d4cb8a1991334f963941` |
| `gw-question-patterns/chunk-1` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:88220b21b4ddc5f202be63cef518b6f40eda5deddedeeabed8a65d262925f2a1` |
| `gw-question-patterns/chunk-2` | 分类待审核 | groundwater-domain-definitions | 缺失，待定位 | `sha256:fc9610a2cd82aecdff0218644b3f4852a31de7bee4efd8d799792e10a4f093c9` |
| `gw-domain-glossary/chunk-0` | 分类待审核 | usgs-aquifers-groundwater | 缺失，待定位 | `sha256:419ce98b82ac7b76e83595ba7c709e0981613064d3e9670b84f881565e5d5ac3` |
| `gw-domain-glossary/chunk-1` | 分类待审核 | epa-groundwater-statistics-guide | 缺失，待定位 | `sha256:db3b63aa92e3ab10572f3668dc22e053b3daebde9771cac4f10872f8b9a8c95a` |
| `gw-domain-glossary/chunk-2` | 分类待审核 | groundwater-domain-definitions, usgs-sir-2016-5021 | 缺失，待定位 | `sha256:e30f831f3366bd101a3b2519dc1021523ab9bedd1142400303eac04fc821ae09` |
| `gw-model-card-summary/chunk-0` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "模型与适用任务"} | `sha256:ad2717b5adb755c21d270abd21e8684580583ff48c58e6600583a6b7b828ca93` |
| `gw-model-card-summary/chunk-1` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "输入与待补信息"} | `sha256:f4e79fb14d4341301d92511eecf9cb843d06435aac33e5dcd3e398b5fa5c9ea4` |
| `gw-model-card-summary/chunk-2` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "输出、成本与边界"} | `sha256:c8b85169f15a98d2b25d2d2fcc6c2f41b44fce9cddb41d98f072f6b33ac93b64` |
| `gw-model-card-monitoring-trend/chunk-0` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "模型与适用任务"} | `sha256:519e195e9fa81142819d29f758add01403f97dbdd95bf50829ee8a9bb4256d7c` |
| `gw-model-card-monitoring-trend/chunk-1` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "输入与待补信息"} | `sha256:1ff9440a2062b0b1f25ef6add13a8e0be1583d6041fc1c382306fd786fd80789` |
| `gw-model-card-monitoring-trend/chunk-2` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "输出、成本与后续"} | `sha256:e77de3b1806c0ada1a6d3d6da4db5e9a3d8e01ca8ee8ecbf874958b5b5ac883e` |
| `gw-model-card-flow-field/chunk-0` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "模型与适用任务"} | `sha256:4dcc35d87ae1bfd43e39a73c681b7cc85221e5f2a1f65176e7eae17838e43061` |
| `gw-model-card-flow-field/chunk-1` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "输入与待补信息"} | `sha256:31cb57b81fcbf5ede04d1064ffc58673ba43633181feb5840ce8cdd5eed7bdb9` |
| `gw-model-card-flow-field/chunk-2` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "输出、成本与后续"} | `sha256:a2159fe556a04f9f53e499d1f54dbb3d6bc5ffc874d5cebfbb684d639bf33017` |
| `gw-model-card-monitoring-coverage/chunk-0` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "模型与适用任务"} | `sha256:1d510642e702b8352b3dfc1bdd60be7fff6007f937d1e0c3e0333b01a52ec262` |
| `gw-model-card-monitoring-coverage/chunk-1` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "输入与待补信息"} | `sha256:4329afc075cfb2d4fcda2c03c448d7e5b3431b64424882e2d31fe363bd192dc5` |
| `gw-model-card-monitoring-coverage/chunk-2` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "输出、成本与后续"} | `sha256:6e2f39b626c0217d1e4fcc3a73aa349e100280bd1fdb273b5c48a56a255a9eb6` |
| `gw-model-card-transfer-learning-analysis/chunk-0` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "模型与适用任务"} | `sha256:7fc27dd78ef765f52337baba9448dd3b5bf1f7fd80260211fdf45b7d87182c7e` |
| `gw-model-card-transfer-learning-analysis/chunk-1` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "输入与待补信息"} | `sha256:b32f331ddf4aa59105494985cc6e0c114c13329cc0a7e0a5543e76fe2d70347f` |
| `gw-model-card-transfer-learning-analysis/chunk-2` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "输出、成本与后续"} | `sha256:2cb75775da36b02b65622b5806cb587227625fe999d87b5af9464d49bab9f071` |
| `gw-model-card-render-map/chunk-0` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "模型与适用任务"} | `sha256:ae6f85fea9cb510d7cd8cd4df0bf3ba6bacb2b1cd7c9f43876b9347f17279bba` |
| `gw-model-card-render-map/chunk-1` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "输入与待补信息"} | `sha256:56b241edc924d79cf4e801317f807f602cd56ed5644501d3b1144f58834302ff` |
| `gw-model-card-render-map/chunk-2` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "输出、成本与连续步骤"} | `sha256:50f557f2f049963d600002d5fb347d5347eba56c78bfaf5c147e2dc5fb44008a` |
| `gw-model-card-source-sustainability/chunk-0` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "模型与依赖"} | `sha256:6488f530d6611c75951246a32f3ae302779e3bdf88e74f53a6b327a26dfc6fa1` |
| `gw-model-card-source-sustainability/chunk-1` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "输入、失败与重试"} | `sha256:bdb019225c5996ab184e760ab8ae063e731bae5254b5c8dae99e020ea43b7a26` |
| `gw-model-card-source-sustainability/chunk-2` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "结果、provenance 与边界"} | `sha256:55c01224664db1915bdfc5b1f43c4e8f5f59aebbba533127790d3a4a22b14e28` |
| `gw-model-card-kriging/chunk-0` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "模型与适用任务"} | `sha256:83ae10b4066e9d5b455a417caaf559da6417c50e3e4d514a724001f7e973d9c3` |
| `gw-model-card-kriging/chunk-1` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "参数与诊断"} | `sha256:f75b1eb1dc5f0aca0592b451275a93518780a94ef587b88a766b6d8641c7c287` |
| `gw-model-card-kriging/chunk-2` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "输出与科学边界"} | `sha256:8a69e099e9aaf411f61ffe07627b22464814d08943ea33080911332b1e596743` |
| `gw-model-card-rule-classification/chunk-0` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "模型与适用任务"} | `sha256:28d5d553089304fc19eb627d79f1bcc0f5c015c02614be83f0c3c8dc00464548` |
| `gw-model-card-rule-classification/chunk-1` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "输入与规则边界"} | `sha256:8dbb22c3308ac49b359c8b3c9a9262a3f83dfa46b132dfcb2febde7a2b4d9fca` |
| `gw-model-card-rule-classification/chunk-2` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "输出与未评价语义"} | `sha256:3d9a49c6eb70d373099e01b3af6394f41eb6314bce4da4d84b7b32222b55a99a` |
| `gw-model-card-drastic/chunk-0` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "模型与适用任务"} | `sha256:ba96763cbf7d466ff92ca520595a480264beff5d1280bb4757fa9cfe0c9f6480` |
| `gw-model-card-drastic/chunk-1` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "配置、网格与缺失"} | `sha256:c9ff7d750efca3a35f9b79695a6bf29e382e03cbce511874f9aa082b7b27749e` |
| `gw-model-card-drastic/chunk-2` | MODEL_CARD 待审核 | groundwater-6-2-model-cards | {"section": "计算、输出与科学边界"} | `sha256:75a83d857444627abf15fa3cb24d6da070d8389dee69c6263e5a25188319cca2` |
| `gw-model-card-porous-simulation/chunk-0` | MODEL_CARD 待审核 | groundwater-6-2-porous-model-cards | {"section": "模型适用范围"} | `sha256:13326a441a2090b476e9ea2722671aeadebf8b2560551d121c692c93d4a1f780` |
| `gw-model-card-porous-calibration/chunk-0` | MODEL_CARD 待审核 | groundwater-6-2-porous-model-cards | {"section": "模型适用范围"} | `sha256:239f00dcfb8412a3a11fbd7653bd0c150fdb032d9308957262c81fdb4346b1f2` |
| `gw-model-card-porous-scenario-comparison/chunk-0` | MODEL_CARD 待审核 | groundwater-6-2-porous-model-cards | {"section": "模型适用范围"} | `sha256:3a1b83a54d53ae6db7e268f11deb1f5ce37b3dbc83ca51163d2145b929eb8f57` |
| `gw-model-card-porous-map/chunk-0` | MODEL_CARD 待审核 | groundwater-6-2-porous-model-cards | {"section": "模型适用范围"} | `sha256:09b5c11853ca6180c3f946016904a4b7c5071b81e390ee611241bd98b03b7cb8` |
| `gw-model-card-porous-inversion-training/chunk-0` | MODEL_CARD 待审核 | groundwater-6-2-porous-model-cards | {"section": "模型适用范围"} | `sha256:730b8a99ea479266f19d753ee5e90674529700da8549208e5d5b3a5ee2835e4f` |
| `gw-model-card-porous-inversion-sampling/chunk-0` | MODEL_CARD 待审核 | groundwater-6-2-porous-model-cards | {"section": "模型适用范围"} | `sha256:e2a896d64c94b82b37ecf7ed2405ff22479dcb470c4a8bd9eb27d5a9f0f36a23` |
| `gw-model-card-porous-agricultural-inversion/chunk-0` | MODEL_CARD 待审核 | groundwater-6-2-porous-model-cards | {"section": "模型适用范围"} | `sha256:5c7803b0f805a2e16d00de74c24c0850cb4f80312f092d8ecf8e87683b496597` |
| `gw-model-card-sustainability-input-preparation/chunk-0` | MODEL_CARD 待审核 | groundwater-6-2-porous-model-cards | {"section": "模型适用范围"} | `sha256:e95cca4a8a3ccdb6c649ad2a5dd2366db163461fec06c2c4db32feacf4d2684c` |
| `gw-method-sen-slope/chunk-0` | METHOD 待审核 | sen-1968 | {"reference": "Sen (1968), pp. 1379–1389", "implementation": "statistics.mann_kendall@1.0.0"} | `sha256:c2226fec64957ea5e72218329cb0d8b7fa03295efe7deb3cbe202c52237d5e83` |
| `gw-method-pettitt/chunk-0` | METHOD 待审核 | pettitt-1979 | {"reference": "Pettitt (1979), pp. 126–135", "implementation": "statistics.pettitt_change_point@1.0.0"} | `sha256:ba1c46c80e13fb39b7ea6bca4d4385161736661349746b68a8cc8452f05f2bf6` |
| `gw-aquifer-parameter-basis/chunk-0` | 分类待审核 | usgs-transmissivity-interpretation, usgs-specific-yield-wtf | {"reference": "USGS pumping tests and water-table fluctuation method"} | `sha256:529ba8830d7d9d423fb7397a3fbf570e90c7d23b52c819a7a9e00f84d2869410` |
| `gw-method-kriging-variogram/chunk-0` | METHOD 待审核 | usgs-kriging-sir20145038 | {"reference": "USGS SIR 2014-5038, spatial interpolation"} | `sha256:804821e56fb52abd6478db98d7d6529e940b28a2f64a78e4683e5d7e1f99b339` |
| `gw-method-drastic-index/chunk-0` | METHOD 待审核 | epa-drastic-1987 | {"reference": "EPA/600/2-87/035, DRASTIC factors and rating"} | `sha256:416c31d2b5e63021b83087ec3b8dc4da8e9a5053e5fce7d95ff18eb755e9638e` |
| `gw-method-modflow-six/chunk-0` | METHOD 待审核 | usgs-modflow6-tm6a55 | {"reference": "USGS TM 6-A55, Groundwater Flow model"} | `sha256:1ce2f193ce602e678314436cdad0fc8669511111229bda35f7f9011972971ef4` |
| `gw-method-flopy-reproducibility/chunk-0` | METHOD 待审核 | usgs-flopy-workflows | {"reference": "USGS FloPy workflows, reproducible model construction"} | `sha256:a96190292a10faaeaa774a09e0d71034b41c8b5d4338bea6134c1bb69a5bc22f` |
| `gw-method-pest-and-inversion/chunk-0` | METHOD 待审核 | usgs-pest-inversion | {"reference": "USGS SIR 2010-5211, highly parameterized inversion"} | `sha256:80d437581ff7fea4794bdc18094f8bd42dc67eb2250905a3e5acebed635182af` |
| `gw-method-pastas-response/chunk-0` | METHOD 待审核 | pastas-2019 | {"reference": "Collenteur et al. (2019), model structure and examples"} | `sha256:0b0ceef1905dd176dc944ddbb157dc8b29a3f352c635464cedee89dda93b1902` |
| `gw-method-groundwater-uncertainty/chunk-0` | METHOD 待审核 | usgs-pest-inversion | {"reference": "USGS SIR 2010-5211, predictive uncertainty"} | `sha256:a83d48c8a633d5dd8a0bbaf2675f9bc4305f524a2e5b5ea2b83ad863f080652d` |
