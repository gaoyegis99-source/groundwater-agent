# 第三方地下水模型 Linux 服务接入

当前实现把算法仓库、服务镜像与平台仓库严格分离。平台只保存固定能力算子、Workflow、
Executable Model、`RemoteServiceBinding` 和远程执行证据，不保存第三方算法源码。

## 交付边界

| 服务 | 独立仓库 | 当前状态 | 平台首批能力 |
| --- | --- | --- | --- |
| 水源地综合评价 | `third-party-services/groundwater-sustainability-service` | Linux 服务 0.7.0 已实现；Bearer、分片上传、协调查询和真实取消语义已启用 | `sustainability.evaluate` |
| 孔隙型地下水 | `third-party-services/porous-groundwater-linux-service` | 原生 Linux 服务 1.1.1，模型 v2；完整张量守恒通量修正和字段级接口错误 | 领域包 6.1.0 十项操作 |

孔隙型本机科研构建由固定交付 ZIP 和各求解器源码包的 SHA256 门禁控制。
原生 gfortran 构建生成包含源码、编译器、参数及二进制摘要的 solver manifest。
运行端只接受数据和声明式配置。

## 平台绑定

Product Manifest v8 的 `remoteServices` 是唯一远程地址和凭据绑定层。水源地综合评价使用以下绑定。

- `GROUNDWATER_SUSTAINABILITY_BASE_URL`：固定 HTTPS 基址；
- `GROUNDWATER_SUSTAINABILITY_ALLOWED_HOST`：必须与解析后的 URL host 精确相同；
- `groundwater-sustainability-api-key`：独立 Bearer secret；
- `groundwater-sustainability-ca-bundle`：受信任 CA 文件；
- API、服务、模型版本与操作类型 allowlist；连接和轮询超时。

孔隙型服务使用 `POROUS_GROUNDWATER_BASE_URL`、`POROUS_GROUNDWATER_ALLOWED_HOST`、
`porous-groundwater-api-key` 和 `porous-groundwater-ca-bundle`。服务、API 和模型版本随部署固定。
Operator 的 `remoteBinding` 只包含 `serviceId` 与固定 `operationType`。只有同时匹配
服务绑定和 `allowedOperations` 的算子才会出现在编排器并获准执行。配置加载器和 Assembler 都拒绝算子或 Workflow
内嵌 URL、Bearer 或 CA。`remote_http` Worker 只加入
Executor 内网与 egress 网络，使用只读根文件系统、非 root 镜像、显式 `/work` 挂载和
Docker secrets；Control 不具有外网连接。

## 执行与恢复

固定执行链为：

1. Control 冻结 Data 输入并创建节点；
2. Worker 重新计算本地大小和 SHA256，按服务 capabilities 分片上传；
3. 以 `sha256(jobId + ":" + nodeId)` 作为稳定 `client_request_id`；
4. 提交前先协调查询；提交超时、网络错误或 5xx 后只按该 ID 查询，不盲目重提；
5. 每次状态变化把远程任务号、请求摘要、状态、轮询时间和取消确认写入 SQLite v9；
6. Worker 重启或租约丢失后，新租约从该证据继续协调/轮询；
7. 下载只接受相对路径，并核验 allowlist 版本、清单端口、格式、大小和 SHA256；
8. 平台生成不含密钥的 `run-receipt.json`；
9. Control 最后通过一个 Data publication 原子公开全部结果。

平台取消会立即栅栏结果发布。远程 Worker 可以在租约被栅栏后仅追加取消证据，不能
恢复租约或完成节点；`remoteTerminationConfirmed=false` 必须如实保留。

## 部署

对于已有本机研究栈，从项目根目录执行
`python3 -B source/products/groundwater/scripts/deploy_local_sustainability.py`，
再从 `source/` 执行 `../bin/gw dev up --product groundwater`。
部署脚本使用 V6 私有 egress 网络，将旧配置和密钥备份到
`$XDG_STATE_HOME/groundwater-v6/third-party/groundwater-sustainability`。
回滚时恢复该目录的 `previous-profile.env` 和两个 `previous-groundwater-sustainability-*`
到原配置位置，停止同目录 Compose 文件的 `groundwater-local-sustainability` 项目，再重新启动研究栈。
从 `source/` 运行
`python -m scripts.checks.check_compose_e2e --product groundwater --sustainability-only`
可用隔离 V6 实例验证合成数据的上传、确认、执行和成果发布。该实例的 Worker 连接本机模型服务，
并使用独立的 Kernel 和 Data 存储。

在实例外部密钥目录创建两个独立文件并设为 `0400` 或 `0600`：

```text
groundwater-sustainability-api-key
groundwater-sustainability-ca-bundle
```

服务本身使用独立数据卷、SQLite WAL、单机单副本和反向代理 HTTPS。业务接口要求 Bearer；
`/healthz`、`/readyz` 和最小 `/version` 可匿名读取。平台永远通过 HTTPS 绑定访问，不把密钥
写入环境、URL、receipt、日志或 Activity。

孔隙型启用命令从 `source/` 执行。验收报告由 `check_compose_e2e.py` 的
`--porous-simulation-case`、`--porous-fixtures` 和 `--porous-assessment-bridge` 模式生成。
详细输入及镜像构建步骤见项目根目录的
`third-party-services/porous-groundwater-linux-service/README.md`（独立服务目录）。

```bash
.venv/bin/python -B products/groundwater/scripts/activate_local_porous.py \
  --image groundwater-v6/porous-groundwater:1.1.1 --report "$acceptance_report"
```

启用记录在 `$XDG_STATE_HOME/groundwater-v6/third-party/porous-groundwater/activation.json`。
该目录保存镜像摘要、装配版本、求解器构建清单和隔离验收报告。
从 `source/` 运行
`.venv/bin/python -B products/groundwater/scripts/activate_local_porous.py --rollback`
恢复原装配和配置并停止孔隙型服务。孔隙型数据卷可用于重新启用。

## 评价输入准备

V6 的“水源地评价输入准备”接受三个精确的 Data 版本。模拟输入可使用归一化证据 JSON，
也可直接使用仿真发布的 `porous-calculation-summary`。直接使用时，在管理证据 JSON 增加
`simulationBudget`，为每个评价水量字段声明 `field`、`direction`、预算项 `term` 和认定依据
`basis`。映射将全模型累计量按申报时段换算为 365 日年均量，来源报告保留预算项和换算依据。
恢复试验的降深使用具有同一时段和指标口径的证据。水质计数读取监测统计。

本机命令 `prepare_sustainability_input.py` 也接收 `--simulation`、`--monitoring`、
`--management` 三份归一化 JSON 和一个尚不存在的 `--output-dir`。三份文件采用相同结构，
包含 `scope`（`waterSource`、`startDate`、`endDate`）、布尔值 `synthetic` 和
`fields`。每个字段记录 `value`、`unit`、`basis` 和 `source`。通过时输出候选
`assessment-input.json` 和 `field-sources.json`。缺项、重复来源、范围或单位冲突时只输出
诊断报告。V6 工作流在诊断中标记 `BLOCKED`，评价服务在入队前拒绝无有效输入的阻止标记。
核对候选 JSON 和来源报告后，选择候选的 Data 版本单独发起并确认综合评价。

## 发布门禁

综合评价需保留原 55 项回归，并通过容器、Bearer、`client_request_id` 协调、取消和平台
端到端测试。`calculated_at` 是易变元数据；“非正式评价”和“中点权重属于实现选择”必须保留。

孔隙型本机科研版以十项真实算法操作全部通过本机测试为启用条件。矩形场地和 NWT 对照
随包数值参考，水头和浓度默认使用 `rtol=1e-5`、`atol=1e-7`。MT3D 一维案例
核对时间轴和质量平衡，并用 Ogata–Banks 解析曲线核对浓度形态。水量和质量平衡的误差限
为 0.01 个百分点。PEST 参数和目标值的相对误差限为 `1e-3`。机器学习验收固定种子并核对输出。
完整 Windows 数值对照和脱敏场地数据待补，验收状态标为“现场科学等价待验证”。
[Ogata–Banks 解析解](https://pubs.usgs.gov/publication/pp411A)

发布固定镜像摘要与不可变算子版本；回滚恢复上一镜像摘要并停用新算子版本。运维至少监控
队列深度、耗时、失败率、协调未确认状态、磁盘、求解器退出码、校验失败和 GPU 使用量。


## 输入校验及浓度质量

### 接口封装与使用

仓库名 V6 不等于 Public API 版本：当前对用户的入口为 `/api/v12`，工作流使用 v6，
两个第三方模型都使用 `remote-operation.v1`。用户上传到 Data 后选择精确版本，
由模型工具创建执行提案，确认后才提交 Job。不要把宿主文件路径或模型服务 `artifactId`
当作 Data `versionId` 传给工具；服务的上传 ID 仅由 remote_http Worker 协调。

| 用户入口 / 工具 | Data 输入参数 | 主要成果 |
| --- | --- | --- |
| 综合评价 `run_source_sustainability` | `assessmentVersionId`（JSON） | 结构化评价、下载 JSON、运行凭据 |
| 流与运移 `run_porous_simulation` | `projectPackageVersionId`（项目 ZIP） | 校验、有效参数、模拟摘要、结果 ZIP、分析、PNG、VTU/PVD、凭据 |
| PEST `run_porous_pest_calibration` | `projectPackageVersionId`、`calibrationObservationsVersionId` | 率定参数、残差、可重算模型、重算结果、凭据 |
| 情景 `run_porous_scenario_compare` | `scenarioPackageVersionId`（情景 ZIP） | 逐情景结果、对比表、凭据 |
| 地图 `run_porous_map_prepare` | `geoTiffVersionId` | GeoTIFF、CRS 元数据、凭据 |
| 训练 `run_porous_inversion_train` | `inversionTrainingVersionId`（训练 ZIP） | 权重、指标、报告、凭据 |
| 采样 `run_porous_inversion_sample` | `inversionWeightsVersionId`、`inversionSamplingVersionId` | 后验样本、指标、报告、凭据 |
| 农业反演 `run_porous_agri_inversion` | `agriculturalDataVersionId`（ZIP） | 参数数组、指标、报告、凭据 |

以上工具名和参数以当前 `domains/groundwater/6.1.0/models/*.model.json` 为准。
训练发布的权重版本可以直接作为采样输入；PEST 工作流自动将已率定模型传给仿真。
评价必须先复核候选 JSON，并单独确认。所有入口使用同一条 Data 原子发布链，不直接引用
第三方服务的下载 URL 作为平台成果。

孔隙型 `contracts/remote-operation.v1.json` 从接口类型与固定端口声明生成；
在服务目录执行 `python scripts/export_request_contract.py --check` 可检查漂移。
服务字段错误使用 remote-operation 的 `error.code/details/request_id` 信封，Worker 只转发
字段位置和错误类型；Public API 的 RFC 9457 错误合同保持独立。认证失败返回 401 和
`WWW-Authenticate: Bearer`。运行参数、模型版本及结果哈希继续冻结在运行凭据中。

部署后可从 remote_http Worker 内运行
`products/groundwater/scripts/probe_model_contracts.py --executor-config /config/executor-config.json`，
检查两服务的 HTTPS/CA、健康与版本、Bearer、字段错误、拒绝入队和上传哈希。
完整使用测试使用前述隔离 Compose 模式；HTTP/队列单元测试中的受控调度不冒充真实算法验收。

上传的综合评价 JSON 在入队前进行字段校验。缺少 `demand_qg` 返回 HTTP 422、
`REQUEST_VALIDATION_ERROR`，字段位置为 `body.assessment_input.demand_qg`；不创建任务。
Worker 将字段位置与校验类型转为 `REMOTE_INPUT_VALIDATION_FAILED`，不进入提交超时协调，
也不把输入值或任意远程消息写入失败日志。本机开发启动包含 remote_http Worker 的源码挂载。

孔隙型模型 v2 默认使用 `conservative-afc.v1`，在 MT3D 每个运移步进行成对守恒通量修正，
保留交叉弥散，固定浓度边界修正计入质量预算，不裁剪输出浓度。适用范围为单组分、无反应、
TVD 对流；不支持的配置明确失败。`transport.numerics` 和结果 ZIP 的 `raw/transport.afc.csv`
提供逐步非负性、质量残差、边界交换和舍入转移证据。`transport.quality` 继续检查全部保存时刻。
显式 `transport.mt3d.numerics="legacy"` 可运行原求解器；只有此模式允许诊断用
`transport.mt3d.dsp.noCross=true`，并保留改变方程的警告。

2026-11-11 三情景同输入对照中，新方法共 720 步无负浓度，最大质量平衡误差 0.000388%。
封闭域脉冲和旋转弥散解析解验证通过。旧浓度参考的 W1 超出原 `rtol=1e-5, atol=1e-7`，
最大差 0.000516 mg/L；这项对照保留为不通过，模型版本升级不代表已证明现场科学等价。
完整方法、适用限制和构建步骤见项目根目录
`third-party-services/porous-groundwater-linux-service/README.md`。
