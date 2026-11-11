# 腰站堡水源地综合评价模拟案例

**端到端验收通过。** 以假设的 2026 全年为评价范围，独立构建水量、监测和管理三类证据。全部输入标记为 `SYNTHETIC_TEST_ONLY`，评价结果保留 `synthetic_example=true`。结果适用于模拟案例。实地取证记录保存在本机。

| 输入 | 模拟数据与取值口径 |
| --- | --- |
| 水量与供需 | 12 个月台账。全年补给 2,520 万 m³、流出 420 万 m³、开采 2,060 万 m³、供水 1,980 万 m³、需求 2,100 万 m³。年末蓄变量为 40 万 m³ |
| 抽水与恢复 | SIM-P01 单井涌水量 4,800 m³/d。停泵时降深 12.5 m，24 小时后残余降深 0.6 m，恢复率 95.2%。另有渗透系数和介质分类假设 |
| 水质 | 40 个虚构点位，35 个达到或优于Ⅲ类，3 个标记特征污染指标超标。水质等级和超标标记是预设的模拟检测结论 |
| 管理 | 假设物理可开采量 2,400 万 m³/a、控制上限 2,200 万 m³/a。模拟开采量低于控制上限，另两项限制性情形在本场景均设为否 |

取值可从原始 CSV 和管理假设 JSON 汇总到三个证据文件。每个字段记录单位、依据和来源文件。清单固定记录输入 SHA-256。重复生成得到相同字节，篡改任一已声明输入后加载检查拒绝。

## 实际验收

在隔离的 6.1.0 产品中发布三份 JSON 证据，经独立确认后执行准备。评价期错配测试产生 `SCOPE_MISMATCH` 和空候选。正确输入得到 `REVIEW_REQUIRED` 候选，来源摘要与上传证据一致。

以候选的精确数据版本独立确认综合评价，由实际 HTTPS 服务 0.7.0 执行。模型 `sustainability-evaluation.v1.1` 得到 **88.37075 分、Ⅱ级**，区间 **83.327–93.4145**。这是系统采用区间中点和默认权重计算的模拟结果，与独立加权预期相符。

发布的结构化结果、结果 JSON 和运行凭据均可下载。两个结果对象的 JSON 内容一致，成果对象和凭据中的输入及输出摘要已核对。40 项准备及验收基础回归通过。隔离 Compose 退出码为 0。 [端到端结果与凭据索引](/home/mtd/.local/state/groundwater-v6/validation/yaozhanbao-synthetic-20261111/run/sustainability/result.json) [命令记录](/home/mtd/.local/state/groundwater-v6/validation/yaozhanbao-synthetic-20261111/compose-command.json)

## 使用与复现

解压后，`case/` 包含原始模拟表、规范化证据及 `case.json`，`results/` 包含候选、字段来源、评价结果及运行凭据。可将三份 `*-evidence.json` 分别发布为数据版本，先用 `prepare_source_sustainability` 生成候选，再绑定候选版本运行 `run_source_sustainability`。

从仓库根目录可运行 `python3 source/products/groundwater/development/yaozhanbao_synthetic.py --output-dir <新的输出目录>` 重新构建输入。隔离测试使用 `check_compose_e2e --sustainability-only --sustainability-case <case.json>`。 [完整案例包](/home/mtd/.local/state/groundwater-v6/validation/yaozhanbao-synthetic-20261111/yaozhanbao-synthetic-case.zip)

## 本地桌面验收

当前本地研究工作区的 Chromium 1440 × 1000 全流程通过，Playwright 耗时 54.2 秒。使用该实例配置的 DeepSeek Agent、本地 Native Worker 和实际 HTTPS 评价服务。8 份文件经桌面上传，全部保存了精确版本并核对原始文件的 SHA-256。复跑复用这批版本。数据视图显示月度 CSV 表格，并可搜索监测 JSON 的 `class_iii_count_nd`。 [案例会话](http://127.0.0.1:28186/#/sessions/743e6f79-7888-4a93-b0b9-f0e22548ca51) [测试记录与版本清单](/home/mtd/.local/state/groundwater-v6/validation/yaozhanbao-desktop-20261111/attempt-3/artifacts/v12-yaozhanbao-sustainabil-ff699-hetic-Yaozhanbao-assessment/case-verification.json)

两轮分别核对模型、冻结参数和精确输入，确认前均无当前轮执行。点击确认后，准备和评价两项任务都在界面依次显示等待、执行、发布和完成。通过“作为下一步输入”将候选版本绑定到评价。任务面板保留两条记录，刷新后能恢复已发布成果。 [任务面板](/home/mtd/.local/state/groundwater-v6/validation/yaozhanbao-desktop-20261111/attempt-3/artifacts/v12-yaozhanbao-sustainabil-ff699-hetic-Yaozhanbao-assessment/task-history.png) [评价视图](/home/mtd/.local/state/groundwater-v6/validation/yaozhanbao-desktop-20261111/attempt-3/artifacts/v12-yaozhanbao-sustainabil-ff699-hetic-Yaozhanbao-assessment/evaluation-result.png)

在桌面下载结构化评价（6,721 B）、远端结果文件（9,363 B）和执行凭据（1,095 B），全部匹配发布对象的长度和 SHA-256。两个结果 JSON 解析值一致，凭据绑定了候选摘要。桌面结果仍为 **88.37075 分、Ⅱ级**，带模拟标记。 [评价 JSON](/home/mtd/.local/state/groundwater-v6/validation/yaozhanbao-desktop-20261111/attempt-3/artifacts/v12-yaozhanbao-sustainabil-ff699-hetic-Yaozhanbao-assessment/sustainability-result.json) [执行凭据](/home/mtd/.local/state/groundwater-v6/validation/yaozhanbao-desktop-20261111/attempt-3/artifacts/v12-yaozhanbao-sustainabil-ff699-hetic-Yaozhanbao-assessment/sustainability-run-receipt.json)

### 并行测试及修复

| 分工 | 证据 |
| --- | --- |
| 数据导入与预览 | 修正数据目录的 `query` 和 `cursor` 请求参数。旧版在目标不在首屏时无法定位；两条合同回归先失败，修复后相关 41 项通过。重新构建并只更新 Gateway 容器。独立真实浏览器从 30 项追加至 60 项，按精确标题唯一命中管理证据并打开 JSON。 [回归](/home/mtd/.local/state/groundwater-v6/validation/yaozhanbao-evidence-20261111/data-view/README.md) [真实分页和搜索](/home/mtd/.local/state/groundwater-v6/validation/yaozhanbao-evidence-20261111/data-view-live/result.json) |
| 确认与进度 | 63 项前端回归和 1 项使用受控 API 与 SSE 的桌面回归通过。实际流程脚本改按提交回执的轮次判断状态，避免将上一轮完成误认作当前轮终态。 [独立测试记录](/home/mtd/.local/state/groundwater-v6/validation/yaozhanbao-synthetic-20261111/desktop-execution-independent/REPORT.md) |
| 评价结果与下载 | 49 项前端回归和 2 项使用受控 API 的桌面回归通过。独立复算模拟得分、区间和等级，并核对隔离验收归档。部分套件与前两组重叠。 [独立复算](/home/mtd/.local/state/groundwater-v6/validation/yaozhanbao-desktop-20261111/result-review/archived-result-integrity.json) |

容器身份复核显示只替换了 Gateway，其他 11 个研究实例容器保持原 ID，健康检查恢复为 `UP`。 [Gateway 更新记录](/home/mtd/.local/state/groundwater-v6/validation/yaozhanbao-desktop-20261111/gateway-redeploy.json)
