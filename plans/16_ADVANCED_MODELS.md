# 16 进阶模型：Pastas 与 MODFLOW 6

## ADV-01 Pastas（P3）
先做独立 spike，不直接加入默认 runtime。
输入：单井水位时间序列、降水/蒸发 stress series。
输出：observed/simulated/residual、参数、calibration/validation metrics。
必须明确：
- 时间频率与缺测处理
- stress lag / response function
- calibration period 与 validation period
- warmup
- 模型收敛/参数可辨识性
与 GRU 比较必须使用相同时间切分和指标。

## ADV-02 MODFLOW 6 + FloPy（P3）
当前 porous remote_http 不等于原生 MF6。新原型从最小稳态规则 DIS 网格开始：
1. 先核实 MF6/FloPy 官方版本与 Python ABI。
2. 用户提供 MF6 binary 路径/版本；不由 Agent 自动下载任意二进制。
3. 构建 homogeneous K、左右定水头的解析案例。
4. 输出 heads、budget、listing、convergence summary。
5. 将 head raster 与现有 contour 工作流串联。
6. 再增加 transient、WEL、RCH、OBS。
7. 后续才做 PRT 与 PEST++/IES。

验收：一维稳态解析水头线性解、Darcy flow、budget balance、固定版本重放。未校准模型不称为真实预测。
