# 外部一手来源抽查

核查日期：2026-11-14。目标是核对代表性知识来源及方法边界，不是全44个sourceId的文献综述或监管合规认证。所有未逐项核实的来源、正文逐主张支持度和适用性均为UNKNOWN；本轮未修改知识资产。

| 来源 | 本轮读取范围 | 可支持的结论 | 限制 |
|---|---|---|---|
| [USGS Circular 1139官方题录](https://pubs.usgs.gov/circ/circ1139/index.html) | 官方域名搜索结果返回题名、作者、摘要和目录；直接open返回403 | 正式题名为Ground Water and Surface Water A Single Resource；本地“Ground Water Atlas reference”题名不符 | 未逐段核对所有本地引用；题录错误不证明专业正文全部错误 |
| [USGS Techniques and Methods 6-A55](https://pubs.usgs.gov/publication/tm6A55) | 官方题录/摘要搜索结果；直接open返回403 | MODFLOW6地下水流动模型有离散、边界/应力与求解定义；单一演示不能自动覆盖完整建模能力 | 没有以该文认证本仓Porous变体或FloPy spike；未据网页当前下载版本升级本仓 |
| [EPA地下水监测统计Unified Guidance](https://archive.epa.gov/epawaste/hazard/web/pdf/unified-guid.pdf) | 可读取889页PDF的文本；定向读取封面、适用性说明和executive summary（PDF第5、7页），没有全889页逐页审查 | 方法评估应考虑自相关/季节性、采样独立性和多重比较；支持A03要求显著性叙述附适用条件 | 是特定监测背景的指南；本轮不判定任何中国项目的法律适用性，也不把其直接变成产品自动规则 |

正文未大段转录。USGS两个来源只用于核题录与概念边界，EPA用于方法审查依据。其余本地sourceRefs的URL可见KNOWLEDGE_INVENTORY.md，**列出URL不代表已打开、全文支持或仍适用**。此前USGS部分PDF直接抓取403、另一EPA附件超时，均未据抓取失败断言来源失效。

本报告的架构、数量、调用关系和测试结论以本地源码/日志为主；外部资料不能替代实现验证。后续A07应记录具体主张、文献定位、适用限制、审阅者及内容版本，而不是只补一个“官方来源”标签。
