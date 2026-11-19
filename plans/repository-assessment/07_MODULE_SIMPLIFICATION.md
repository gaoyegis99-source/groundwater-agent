# 07 系统减负：先收默认路径，再改物理部署

**建议保留四个状态所有者（Kernel、Data、Knowledge、Executor），把默认产品路径收敛到监测资料、统计/空间分析和可信交付。** 最低成本不是换框架或并数据库，而是消除同一校验的分叉、缩短工具/信息路径、隔离实验依赖。证据在E01–54及08矩阵。

## 十二组重点边界

| 比较 | 已核事实 | 建议与限制 |
|---|---|---|
| Kernel / Gateway | nginx无业务存储，auth_request向Kernel取身份，Kernel拥有Session/Task | KEEP；没有两个业务后端。可合并打包运维入口，不撤掉边缘头重写/CSRF/静态服务边界 [E02/E03] |
| Data / Knowledge | Data拥有字节/版本/语义，Knowledge拥有Evidence/生命周期；绑定走HTTP | KEEP逻辑独立；共享基础库/客户端代码可评估，不能重复保存原文件或共享表直连 [E10/E22] |
| Domain / Product | Domain专业定义，Product选服务/UI/remote binding | KEEP；描述参数和输出的重复内容MERGE为生成投影，不并成一个无版本配置 [E01/E04] |
| Model Card / Method Knowledge | 卡片重复描述schema和参数；方法含科学原理/限制 | MERGE机械信息来源；method与实现card仍分工，引用而非复制 [E23] |
| Observation /业务Tools | 前者精确读取，后者准备确认后的模型提案；算子不直接给Agent | KEEP权限分离；SHRINK同Turn重复inspection，不能把读工具授权升级为执行 [E31/E32] |
| Operator / Workflow | 函数合同vs组合DAG；54引用/113版本不是全目录使用情况 | KEEP层次；按场景暴露目录；旧版本需历史引用审计 [E40/E54] |
| AgentPlan / Task Steps | Plan绑定本轮action与plan_revision；Task是跨轮交付 | SHRINK Plan文字/重复计划生成；保留冻结执行理由，暂不并表 [E35] |
| Task Steps / Executor DAG | criteria/review进度 vs节点租约/计算依赖 | KEEP；通过workflow_run/output refs关联，不同步两套节点状态 [E35/E50] |
| 前端状态 / 后端状态 | UI selector映射快照；本地存队列/草稿 | KEEP后端权威；MERGE重复label/公共读取组件需合同测试；未发现前端另有权威科学完成状态 [E51] |
| Data Quality / 专业检查 | 文件profile、caller报告QualityRun、task semantic checks分层 | MERGE规则定义/回执链，ADVANCE必需科研前提；不把文件OK当科研READY [E11/E13/E32] |
| Demo / 正式科研 | general检索含未绑定模板和演示；default domain含真实方法 | SHRINK Demo默认召回；保留用于教学/测试的独立用途 [E20/E21] |
| Porous / 核心工作流 | 7条remote能力、私有vendor和求解器；不依赖于普通趋势/流场 | EXPERIMENTAL反演；仿真/率定作为高级可选能力，不要求默认监测路径部署 [E46] |

## 本轮确认的重复与尚不能确认的重复

可以直接进入设计的重复：prepare与proposal-boundary不同校验逻辑；模型参数/输出在JSON、指令、卡、UI中的重复维护；资料目录/预览/轮询在主站与能力管理页中的同类职责；MLRun环境/参数/指标与WorkflowRun/Result provenance的重叠记录风险。

不能直接认定重复：原始文件与规范化Parquet；DataVersionRef与Data对象；immutable semantic revision与Knowledge binding；Kernel WorkflowRun与Executor Job；两版本reproject或PEST；Raster Data preview与scientific render-map。它们分别服务身份、传输、执行、解释或交付，需要保持合同边界。

UI同名polling/recovery文件**并非逐字相同**，因此只能按行为契约抽取公共部分，不能全局替换。MLOps没有找到主执行路径调用，并不证明没有外部API用户；先审计调用与导出需求。

## 减负的优先顺序

1. **统一必需校验**：先处理A01/A02，避免精简放大科研错误。
2. **收默认能力和知识范围**：核心方法优先，Porous/GRU/地表DEM辅助按需进入；真实启用状态与科学成熟度分开。
3. **减少重复维护**：机械卡片生成、精确元数据单次读取、共用UI边界；保留用户能检查的冻结内容。
4. **MLOps范围冻结**：停止以Data为中心扩建训练/注册/上线治理。保留不可变模型artifact及metrics即可满足多数当前研究复现；已有公开接口兼容退出。
5. **物理服务合并留后**：只有真实资源/延迟证据证明值得时，才评估Knowledge/Data数据库宿主共置；当前不建议跨owner合库或重写Kernel。

## 删除候选的必要门槛

REMOVE_CANDIDATE不意味着允许删除。本轮候选主要是确认不再消费的旧生成说明/废弃UI配置及旧版本默认装配引用。每项须有：全仓引用、当前Product和导出SDK、外部API/历史运行使用、归档重放、替代版本、回滚方案。未查活动库和外部客户端，因此多数实际删除决定仍UNKNOWN。

6.1.0领域包、PEST1.0、raster.reproject1.0有历史身份价值；不能在“减负”名义下原地修改或删除。当前lightweight-agent-core是另一交付面，建议独立发布/测试入口，不立即迁仓，不以其规模作为删代码理由。

## 效益如何验收

记录每条代表研究任务的工具数、Provider请求/输入Token、Data读取/查询次数、端到端等待、默认能力卡片数、服务依赖及失败原因。当前实际基线UNKNOWN，任务A12先测再给阈值；本报告不声称“减半成本”或“提升数倍”。更重要的硬验收是：必需前提缺失不产生可批准执行，结果能追到输入/方法/限制，同一研究能复现。
