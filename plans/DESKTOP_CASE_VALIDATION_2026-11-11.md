# 地下水案例桌面验收

在本地研究 Gateway 使用 Chromium 1440 × 1000 并行复核四个案例。每次执行前核对待确认的模型、参数和不可变输入；浏览器保留验证会话及成果。

| 案例 | 桌面结果 | 证据 |
| --- | --- | --- |
| 孔隙流与溶质运移 | 合成矩形算例由实际服务执行，观察排队、执行、发布和完成。打开 100 × 30 PNG，12 项实际下载与发布 SHA-256 一致。连续下载第 11 项超时，重开原会话后补齐两项 | [会话](http://127.0.0.1:28186/#/sessions/6e0f18ea-07d6-4ec7-bab3-020cca2c9980) · [报告](/home/mtd/.local/state/groundwater-v6/validation/desktop-other-cases-20261111/porous-simulation/REPORT.md) |
| USGS MRVA 流场 | 1,147 个公开衍生坐标，执行后发布 8 项。查看表格、图表和水头栅格，下载摘要与独立 Native 基准一致。速度基于 K 与有效孔隙率的情景值 | [会话](http://127.0.0.1:28186/#/sessions/eab6fd01-ca9d-452c-81d4-40066391c914) · [证据](/home/mtd/.local/state/groundwater-v6/validation/yaozhanbao-evidence-20261111/public-flow-desktop/result.json) |
| USGS MRVA 普通克里金 | 1,148 个公开衍生测点，5 项成果实际下载并校验。2,025 个预测像元与独立基准的最大差为 1.91 × 10⁻¹⁰ m。修复后在原会话点击预测及方差栅格，均读到正确像元值 | [会话](http://127.0.0.1:28186/#/sessions/a6bac3c7-73f8-4309-bbce-b34ee45965a3) · [报告](/home/mtd/.local/state/groundwater-v6/validation/desktop-other-cases-20261111/usgs-mrva-kriging/REPORT.md) |
| 腰站堡迁移学习 | 19 份冻结输入，58 个节点完成，发布 25 项。检查训练图、预测表和评价 JSON，9 份浏览器下载匹配发布校验和。固定案例走本地规则，外部模型请求为零 | [会话](http://127.0.0.1:28186/#/sessions/46850e4a-8ad3-41db-9338-bbcdd221014f) · [报告](/home/mtd/.local/state/groundwater-v6/validation/desktop-other-cases-20261111/yaozhanbao-transfer/REPORT.md) |

本地水位观测表已导入并核对 114 口井、9,943 行和水位高程单位。桌面读取了首批 100 行。该表仅用于本地预览，趋势模型本轮未通过默认 Agent 运行，避免公开服务取得样本行。腰站堡迁移学习采用开发快速档。238 条留出预测的 RMSE 为 0.93418 m，持久性基线为 0.57858 m。 [本地表格证据](/home/mtd/.local/state/groundwater-v6/validation/yaozhanbao-evidence-20261111/real-trend-desktop/local-case-result.json) · [迁移学习数值复算](/home/mtd/.local/state/groundwater-v6/validation/desktop-other-cases-20261111/yaozhanbao-transfer/scientific-result-audit.json)

## 复现与修复

- 地图父容器只有最小高度，OpenLayers 视口实际高度为 0。明确容器高度后，两项生产构建浏览器回归通过，当前 Gateway 的原会话预测和方差视图均为 418 × 478。独立的 CSP 样式提示仍可见，本次图层和探针可用。 [地图实测](/home/mtd/.local/state/groundwater-v6/validation/desktop-other-cases-20261111/usgs-mrva-kriging/run-1/map-recheck-after-deployment.json)
- 研究开发与交互开发的源码挂载覆盖了 QGIS 镜像的 Python 搜索路径，迁移学习首次在 `vector.clip` 节点失败。两套配置修复后 27 项回归通过，定向重建 QGIS worker 复用原镜像，实际成功导入 `processing` 并完成迁移学习。 [QGIS 更新记录](/home/mtd/.local/state/groundwater-v6/validation/desktop-other-cases-20261111/yaozhanbao-transfer/qgis-recreate.json)
- Kernel 在等待恢复确认时保留原任务的失败状态。WebUI 现在允许显示当前任务最新、未决且有来源的恢复确认。82 项相关前端回归通过，补充的 17 项边界测试通过，两者有重叠。 [恢复确认回归](/home/mtd/.local/state/groundwater-v6/validation/yaozhanbao-evidence-20261111/recovery-confirmation/README.md)

修复后在原失败会话打开现有恢复提案，核对 19 份输入和冻结参数后，经一次桌面确认恢复成功。刷新后仍显示原失败记录和第二次成功记录，25 份成果校验和与独立快速档一致，9 份实际下载通过。恢复成功回复未重复显示固定案例的数据使用限制，独立成功会话显示了该说明。 [原恢复会话](http://127.0.0.1:28186/#/sessions/312e2280-960c-4b9c-981d-3463ecce846a) · [实际恢复验收](/home/mtd/.local/state/groundwater-v6/validation/desktop-other-cases-20261111/yaozhanbao-transfer/recovery-live/REPORT.md)
