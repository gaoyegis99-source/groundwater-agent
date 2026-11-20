# 阶段 6：Business Capability 扩展与选择质量

## 用户目标
用户按业务问题表达需求，系统识别适用能力、理解资料、询问关键缺失信息，并在明确范围内进入现有专业计算链路。

## 先核实
Product Capability schema/declaration、Workflow Profile、Result Contract、Assembler exact-reference validation、Kernel `select_business_capability`、match/exclude terms、参数解析、输入绑定、准备续接、role/service/model/profile checks、trace 和 Web visibility。

## 原则
- 从真实用户任务盘点能力，不按 Workflow 数量机械生成。
- 一项能力可链接一个或多个经验证 Profile，但不得成为无边界万能入口。
- 纯查询/浏览/状态查询不强制绑定 Workflow。
- 专业计算使用精确 Model/Workflow/Profile，不临时拼接未经治理的执行图。
- CORE 自动入口需有技术证据、角色、服务依赖和限制。
- 技术验证、科学适用性和业务验收分开。
- 多候选或关键参数冲突时澄清；无合适能力时正常回答并说明边界。

## 推荐选择流程
Intent Router 判定咨询/状态/资料/分析/草稿/控制动作 → Agent 提出候选 Capability → Kernel 根据产品配置、角色、服务、精确身份、输入和参数确定性校验 → 唯一且符合政策时准备 → 歧义/缺失时澄清。

## 测试语言
同义词、口语、行业缩写、否定、多目标、多候选、缺参、冲突数值、单位、补答、用户显式指定 Profile、纯咨询包含关键词、自定义 Workflow、Prompt injection、不同角色。

## 每项新增能力必须具备
用户场景、适用/排除条件、输入角色、前置条件、参数来源、结果合同、限制、技术测试、领域验证状态、UI 入口和澄清案例。

## 验收
报告候选召回、错误匹配、澄清率、准备阻塞率、任务成功率和用户纠正率。不得只凭关键词单测宣称自然语言理解完成。
