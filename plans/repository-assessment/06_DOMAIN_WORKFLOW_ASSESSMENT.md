# 06 Domain / Workflow / Operator

**Domain是产品的专业核心；应收敛默认能力而保留经审核的高级研究扩展。** 当前不是“18个独立科学模型”：其中包括描述统计、制图、数据准备、几何操作、固定学习链和远程物理求解。完整逐项输入输出合同、runtime、实现入口和引用在 evidence/MODEL_WORKFLOW_MATRIX.md 与 evidence/OPERATOR_MATRIX.md。

逐项源码分派与smoke/测试导航补充见 [OPERATOR_TEST_INDEX](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/OPERATOR_TEST_INDEX.md)；模型→Workflow→modelRef知识卡→测试连接见 [MODEL_EVIDENCE_DETAIL](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/MODEL_EVIDENCE_DETAIL.md)。字面测试引用只是导航，不等于覆盖或本轮通过。

## 当前18项能力逐项判断

“定向通过”仅指TEST_EVIDENCE所列合同/算子范围；真实服务运行、现场科学验证本轮均未执行。

| 模型 | Workflow节点/依赖 | 产品分类/处置 | 测试证据与科研边界 |
|---|---|---|---|
| summary | 1；statistics.describe/native | 核心 KEEP | 数值/非有限值/分组语义测试；描述样本，不代表总体 [E40] |
| monitoring-trend | 10；native统计/时间序列 | 核心 ADVANCE | 定向合同/扩展算子通过；补自相关/采样/多重检验门禁 [E41] |
| flow-field | 5；head/snapshot/IDW/gradient/Darcy | 核心 ADVANCE | 定向合同/算子通过；简化二维空间推导，不是MODFLOW [E42] |
| kriging | 1；自有native实现 | 核心中的高级方法 KEEP | 定向算子通过；变异函数、LOO、条件方差，缺外部真值认证 [E43] |
| monitoring-coverage | 3；QGIS dissolve/hull + native convert | 核心辅助 KEEP | 专门几何测试存在；本轮未QGIS真实跑；凸包非水文影响范围 |
| render-map | 1；native PNG | 核心交付 KEEP | 定向算子通过；单文件静态图，非完整报告地图 [E49] |
| rule-classification | 1；table.classify | 专题 KEEP | 定向规则边界测试通过；有来源规则才可评价；不默认宣称标准合规 [E44] |
| drastic | 1；drastic.evaluate | 专题 KEEP | 定向算子通过；七因子相对脆弱性，不是风险概率 [E44] |
| source-sustainability | 1；remote service | 专题 SHRINK默认呈现/ADVANCE依据 | remote测试和评分测试存在；需要第三方规则来源与适用性复核 [E47] |
| sustainability-input-preparation | 1；native汇总/核对 | 专题 KEEP | scope/模拟监测误用测试存在；输出候选而非审批结论 |
| transfer-learning-analysis | 58；native/GRU与空间链 | EXPERIMENTAL | 19输入，训练段scaler和单步回测审计测试通过；真实训练、跨井/跨区外验证未跑 [E45] |
| porous-simulation | 4；compile/simulate/analyze/render remote | 高级研究 KEEP，退出核心默认 | 服务/求解器外置；现场等价UNKNOWN；保留数值诊断 [E46] |
| porous-calibration | 3；compile/PEST1.1/simulate remote | 高级研究 KEEP | 仅声明范围内的全局参数倍率和水头率定，不是一般反演平台 |
| porous-scenario-comparison | 1；remote | 高级研究 KEEP | 明确基准/情景包；结果比较≠概率不确定性 |
| porous-map | 1；remote GeoTIFF整理 | SHRINK / MERGE候选职责 | 与Data raster metadata、制图读取重叠；仍有远程产物合同，先对照再迁移 |
| porous-inversion-training | 1；remote Torch | EXPERIMENTAL | 固定形状同批perm/disp/head训练；物理泛化UNKNOWN |
| porous-inversion-sampling | 1；remote Torch | EXPERIMENTAL | 条件采样/权重版本；后验科学校准UNKNOWN |
| porous-agricultural-inversion | 1；remote MODFLOW响应/有界岭回归 | EXPERIMENTAL | 明确WEL组与观测切分，不能推广为通用农业取水反演 |

## Workflow与合同

领域模型使用 `executable-model.v6`：业务参数schema、默认值、inputBindings、fieldRequirements、semanticRequirements、参数绑定、结果发布描述。Workflow v6拥有计算节点和端口连线。Operator Manifest定义最小执行单元，静态实现按 `(id,version)`选择；发布身份不可原地覆盖。[E01/E40]

Product另选择10个表格/栅格算子，`ArtifactComposition.compile`校验并下沉受限workflow.v7；Kernel与Executor共享compiler并分别验证信任边界。两次验证不是可直接去掉的重复运算。应减少规则实现的重复，保留边界复核。[E54]

当前模型层、Workflow层、Operator层存在名称/输出描述重复；输入绑定与输出端口相互约束仍必要。推荐从合同生成model card机械段、UI参数说明与能力列表；不能为了少JSON把用户参数、DAG调度与函数执行合成一个不可审核文件。

## 全部算子分类

113个版本条目逐行见OPERATOR_MATRIX；分类按主要产品角色，个别共用算子同时服务高级研究。分类不是删除命令。

| 分类 | 数量 | 原则 |
|---|---:|---|
| 核心算子 | 20 | 直接服务当前默认统计、流场、概览、覆盖和制图链 |
| 共用基础算子 | 61 | 表格、时间预处理、矢量/栅格等，按需暴露 |
| 高级专业算子 | 24 | 包含Kriging、DRASTIC、远程专业模型及地表水文辅助；明确适用范围 |
| 实验性算子 | 6 | 3个GRU训练/微调/预测+3个Porous反演相关 |
| 弃用候选 | 2 | raster.reproject1.0、pest_calibrate1.0存在后继版本，先查历史运行/显式调用 |
| 已证实重复算子 | 0 | 同名不同版本/相似名字不是充分重复证据 |
| 未知用途 | 0 | 都有合同描述与静态入口；真实使用频率仍UNKNOWN |

有59个版本条目未被当前18条Workflow引用；其中还可能被Product组合白名单、SDK、后续科研任务使用，不能凭引用数删。`hydrology.*`主要为DEM地表汇流辅助，不等于地下水流动求解，建议SHRINK默认目录，保留明确研究用途的实现。

每条Manifest目录有smoke描述/夹具，静态`IMPLEMENTATIONS`登记与runtime对应。smoke可证明最小接口行为，不能证明科学正确性；本轮只跑8个专业测试文件的45项，未跑所有smoke和QGIS，见TEST_EVIDENCE。

## Porous专项

当前产品已装配7条Porous Workflow及10种remote operation；catalog有11个Porous算子版本（PEST两版）。这些已超出纯设计阶段，不能一概标为“尚未实现”。然而关键vendor原包、NWT/MT3D/PEST二进制及构建产物不在仓库跟踪源码内；本轮未读取私有载荷或跑完整模型，因此不提供科学等价保证。[E46]

`numerics.py`默认选择本地 `conservative-afc.v1` 求解器变体；README明确适用单组分、无反应、TVD、正储存项隐式运移，拒绝未验证组合，并保留negative concentration、守恒/残差等审计。它不是简单裁剪，也不是已获官方认可的通用算法。应保留这些拒绝与诊断，独立验证数值扩散、网格/时间步收敛、边界质量和实测场地，再决定是否进入标准研究路径。

最小减负：Porous作为可选部署与专业能力集；不让普通监测统计承担其构建、GPU/Torch、远程凭据、求解器和反演卡片的认知成本。不要删除与当前业务有价值的仿真/率定，仅把高风险泛化的实验入口与核心流程隔开。

## Transfer Learning专项

58节点不仅是GRU，还包括XLSX提取/展开、时序切分、多个scaler/windows、矢量背景、影像裁窗和绘图准备。数据科学部分和特定案例交付部分耦合，造成19输入门槛。已有训练段标准化、persistence baseline、R²为空原因及空间时间选择审计，值得保留。[E45]

建议先拆“数据/回测最小合同”和“腰站堡空间展示模板”的产品呈现，保持旧模型3.1身份可重放。只有获得不同区域、独立井与前向预测验证后再晋级默认预测能力。不能把用真实观测历史的单步留出回测包装为未来预报；也不应以“高级”名义优先增加更多网络模型。

## 真正缺少的专业能力与取舍

优先补监测语义/井层位一致性、统计适用诊断、空间外推与参数敏感性标记、标准/规则精确依据、计算后数值解释和最小可复现报告。抽水试验参数估计、完整水量平衡、水化学QA/QC等没有默认端到端实现；可列为有需求后的专业任务，不在本轮建议无条件新增所有模型。
