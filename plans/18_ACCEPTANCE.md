# 18 功能验收与科学计算验收

## 1. 四层测试
- Unit：纯函数/组件/参数校验
- Subsystem：Kernel/Data/Knowledge/Executor/operator
- Workflow：真实 DSL compile + runner/Executor 执行
- UX/E2E：用户从输入到结果的完整路径

## 2. Agent 不变量
- 普通咨询不创建 Confirmation/WorkflowRun/Job。
- 执行提案必须先准备数据、记录计划、用户确认。
- 确认后使用冻结 Data version。
- 引用只来自本轮完整读取且 OK 的 evidence。
- workflow draft 不产生执行授权。
- fan-out 不被误判为线性链。
- MiniMax 普通 run 不带 synthetic case disclaimer。

## 3. 前端验收
每个重要组件覆盖 loading / empty / success / error / retry / stale / unmount。
对话、确认、草稿、地图、栅格探针、图表和结果卡都要有真实数据 fixture。

## 4. 空间数据验收
GeoTIFF：CRS、transform、resolution、nodata、band、physical value。
Vector：CRS、geometry type、feature count、bbox、geometry validity。
Map：layer count、visibility、opacity、order、legend、unit、CRS。
禁止用归一化渲染值作为原始数据验收。

## 5. 数值算法验收
每个新算法必须提供独立 oracle：
- 解析解
- 独立 NumPy 手算
- 固定 benchmark
- 统计性质测试

浮点判据：
`abs(actual - expected) <= atol + rtol * abs(expected)`

每项容差需说明单位、算法、来源和适用范围。不要用同一实现生成 expected 与 actual。

## 6. 地下水业务演示
A. 监测趋势：字段核对、完整性、线性趋势、MK、突变、季节趋势。
B. 降深：水头基准期与观测期比较。
C. 流场：高程/埋深、CRS、K 单位和 Darcy 假设。
D. 井点覆盖：凸包几何范围，不称含水层边界。
E. 制图：水头栅格、等值线、井点。
F. 数据质量：缺测、重复、异常值。
G. Kriging vs IDW：预测、variance、LOO metrics。
H. DRASTIC：七层对齐、规则来源、nodata。

## 7. 测试记录
每卡记录真实命令、退出码、passed/failed/skipped、关键数值、未运行原因和源码 revision。
本次 GitHub 静态审计没有执行测试；所有测试状态为 NOT_RUN。
