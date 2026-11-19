# 08 去重决策矩阵

分类：KEEP保留；MERGE合并重复职责；SHRINK收缩范围；DEPRECATE逐步弃用；REMOVE_CANDIDATE进入删除评估；ADVANCE补强；EXPERIMENTAL独立实验；UNKNOWN证据不足。主建议一项，附条件动作；均为提案，未实施。

| ID / 模块或职责 | 决策 | 产品价值 / 成本 | 证据与根因 | 下一步/依赖/风险 | 合同影响 / 任务 |
|---|---|---|---|---|---|
| D01 Kernel交互编排 | KEEP | 高/高 | Session/Task/授权/恢复是核心；E30–36 | 修准备缺口，避免另建研究编排层 | 行为修订；A01/A02 |
| D02 Gateway | KEEP | 高/低 | 无业务状态；E03 | 保留可信头与唯一入口 | 无；A18文档 |
| D03 Data版本/对象/语义 | KEEP | 高/中 | 冻结输入/成果复现；E10/E16 | 补专业profile | 新领域合同；A02/A04 |
| D04 Data ML experiments/registry/aliases | SHRINK | 默认路径低/中高 | 公开API有实现，未见主执行消费者；E14 | 先使用审计与导出，再停止新增默认入口 | 退出API需版本策略；A11 |
| D05 Data Query / Preview | KEEP | 高/中 | 沙箱和只读预览不同目的；E15 | 共用持久队列设施，减少重复查询 | 不改变语义；A12 |
| D06 Data质量台账+专业质量 | ADVANCE | 高/中 | caller结果和专业要求未统一执行证明；E13/E32 | 接既有Executor/回执，不另建质量引擎 | 回执/门禁扩展；A04 |
| D07 Knowledge服务 | KEEP | 高/中 | 单PG双路检索/精确Evidence；E21 | 优先内容治理 | 检索scope可扩展；A08 |
| D08 Model card机械参数 | MERGE | 中/中 | 与model/schema/UI/instructions多处重复；E01/E23/E34 | 生成机械段，人工专业限制保留 | 新Domain/知识版本；A09 |
| D09 Method知识 | ADVANCE | 高/中 | 科学依据浅、locator不足；E23 | 现有方法适用性和来源逐项复核 | 新知识版本；A07/A16 |
| D10 Demo/Template检索 | SHRINK | 教学高/默认科研低 | 无绑定进入GENERAL；E20/E21 | 用途分层，不按文件名删整包 | 检索/内容标签合同；A08 |
| D11 Domain/Product | KEEP | 高/中 | 专业定义vs装配选择；E01 | 移除重复手写投影 | 不合并身份；A09/A10 |
| D12 Observation链 | SHRINK | 高/中 | describe/sample/prepare重复读取；E12/E32 | 同Turn快照复用+末端身份复核 | 可选批量读合同；A12 |
| D13 Router/Agent双请求 | UNKNOWN | 权限边界高/体验成本未知 | Router无工具；尚无请求延迟实测；E30 | 先量化，再评估规则旁路/合并语义判读 | 路由/授权不可削弱；A13 |
| D14 AgentPlan/Task文字 | SHRINK | 中/中 | 同目标多轮重述但plan_revision被action引用；E35 | 减展示和写入，保留冻结理由 | Plan删除需迁移；A14 |
| D15 Task/DAG状态 | KEEP | 高/中 | 不同粒度所有权；E35/E50 | 仅结果引用关联 | 无；A14 |
| D16 前端selector/后端状态 | KEEP | 高/中 | 快照权威、未知状态保守映射；E51 | 复用selector，不新增状态机 | 无；A15 |
| D17 三套UI目录/预览/错误恢复 | MERGE | 中/中 | 共同交互职责，不是逐字复制；E51 | 抽共用组件/读hook，保留角色与service入口 | UI行为回归；A15 |
| D18 Operator/Workflow | KEEP | 高/中高 | 计算单元vsDAG；E40/E54 | 场景目录分层，不删未引用项 | 默认选择新版本；A10 |
| D19 有限Workflow组合 | SHRINK | 有用/中高 | 已10白名单而非无界引擎；E54 | 只维持科研准备组合，稳定后再考虑更多 | 编译/恢复合同保留；A10 |
| D20 普通时间/空间诊断 | ADVANCE | 高/中 | ACF/空间验证等已有算子未接业务；E41/E43 | 复用算子补适用性与输出解释 | 新Workflow版本；A03/A16 |
| D21 Porous仿真/率定/情景 | KEEP | 专题高/高 | 确有remote实现；外置solver与科学边界；E46 | 作为高级可选包，独立数值验收 | Product部署选择；A10/A17 |
| D22 Porous反演训练/采样/农业 | EXPERIMENTAL | 窄/高 | 固定形状/方法研究；E46 | 隔离工具、依赖、成熟度说明 | 新默认装配；A10/A17 |
| D23 porous-map | MERGE | 有限/中 | 与本地栅格检查/发布职责部分重叠；E11/E46 | 对照输出/CRS/nodata再决定本地替代 | 新算子版本；A19 |
| D24 Transfer Learning | EXPERIMENTAL | 窄/高 | 19输入/58节点含固定案例展示；E45 | 分开最小回测和案例空间展示 | 新模型/Workflow身份；A20 |
| D25 MODFLOW6/Pastas spikes | EXPERIMENTAL | 研究价值/中 | 独立环境与合成基准；E48 | 暂不进入默认产品，按明确需求晋级 | 晋级才新model；A21 |
| D26 hydrology通用DEM辅助 | SHRINK | 按需/中 | 地表汇流不等于地下水；E40 | 隐藏默认目录，保留需求明确项 | 产品能力选择；A10 |
| D27 QGIS worker | KEEP | 当前coverage必需/高 | 工作流确有QGIS节点；E40 | 不能直接删除；本地替代需几何/CRS等价基准 | 新workflow/runtime；A19 |
| D28 lightweight-agent-core | EXPERIMENTAL（对本产品） | 第二交付面/高 | 419测试函数，非Compose依赖；E52 | 明确独立owner/release/check，不迁仓 | 本产品无需改API；A22 |
| D29 旧Domain与双版本算子 | DEPRECATE（新选择） | 历史高/中 | 6.1未默认选择；两个算子有后继 | 查历史/外部消费后再定；保留重放 | 不得原地改身份；A23 |
| D30 旧生成说明/未消费UI候选 | REMOVE_CANDIDATE | 低/低 | 文档漂移已发现，旧UI仍可能被测试使用 | 先逐引用与发布用途审计，未授权删除 | 文档/测试；A18/A23 |
| D31 Research Result/报告 | ADVANCE | 核心高/中 | 查看/下载/归档与完整报告有间隙；E49 | 结果解释+最小版本化报告，复用Data | 新成果/报告合同；A05/A06 |
| D32 多PG进程/物理共置 | UNKNOWN | 运维潜在成本未知 | 逻辑隔离明确；无资源量测；E03/E53 | 按实测考虑同宿主不同database/roles；不合表 | 部署/备份合同；A24 |

没有直接REMOVE结论；缺少运行使用证据时，减少默认可见性比删除实现更易回退。完整算子级决定见OPERATOR_MATRIX，不能只用本表替代逐版本引用审计。
