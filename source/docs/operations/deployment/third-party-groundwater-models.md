# 第三方地下水模型 Linux 服务接入

当前实现把算法仓库、服务镜像与平台仓库严格分离。平台只保存固定能力算子、Workflow、
Executable Model、`RemoteServiceBinding` 和远程执行证据，不保存第三方算法源码。

## 交付边界

| 服务 | 独立仓库 | 当前状态 | 平台首批能力 |
| --- | --- | --- | --- |
| 水源地综合评价 | `third-party-services/groundwater-sustainability-service` | Linux 服务 0.7.0 已实现；Bearer、分片上传、协调查询和真实取消语义已启用 | `sustainability.evaluate` |
| 孔隙型地下水 | `third-party-services/porous-groundwater-linux-service` | 仅有协议、授权门禁、原生构建/清单及黄金结果校验工具；未导入受限源码、未发布镜像 | 授权和科学验收前全部禁用 |

孔隙型服务必须先取得明确书面改造/部署授权。授权文件、批准的源码根和归档 SHA256
由独立仓库中的 fail-closed 校验器核验；不满足时构建必须失败。取得授权后仍需以固定
gfortran 工具链原生构建 MODFLOW-2005、MODFLOW-NWT、MT3D-USGS 和 PEST，并生成包含
源码、编译器、参数及二进制摘要的 solver manifest。不得使用 Wine，也不得接受上传的
Python 脚本或任意可执行路径。

## 平台绑定

Product Manifest v8 的 `remoteServices` 是唯一远程地址/凭据绑定层。Groundwater 当前绑定：

- `GROUNDWATER_SUSTAINABILITY_BASE_URL`：固定 HTTPS 基址；
- `GROUNDWATER_SUSTAINABILITY_ALLOWED_HOST`：必须与解析后的 URL host 精确相同；
- `groundwater-sustainability-api-key`：独立 Bearer secret；
- `groundwater-sustainability-ca-bundle`：受信任 CA 文件；
- API、服务、模型版本与操作类型 allowlist；连接和轮询超时。

Operator 的 `remoteBinding` 只能包含 `serviceId` 与固定 `operationType`。只有同时匹配
服务绑定和 `allowedOperations` 的算子才会出现在编排器并获准执行；孔隙型的十项算子因此
在授权和逐项验收前保持不可见、不可执行。配置加载器和 Assembler 都拒绝算子或 Workflow
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

在实例外部密钥目录创建两个独立文件并设为 `0400` 或 `0600`：

```text
groundwater-sustainability-api-key
groundwater-sustainability-ca-bundle
```

服务本身使用独立数据卷、SQLite WAL、单机单副本和反向代理 HTTPS。业务接口要求 Bearer；
`/healthz`、`/readyz` 和最小 `/version` 可匿名读取。平台永远通过 HTTPS 绑定访问，不把密钥
写入环境、URL、receipt、日志或 Activity。

## 发布门禁

综合评价需保留原 55 项回归，并通过容器、Bearer、`client_request_id` 协调、取消和平台
端到端测试。`calculated_at` 是易变元数据；“非正式评价”和“中点权重属于实现选择”必须保留。

孔隙型能力按仿真、校准、情景、分析制图、CPU/GPU 反演分阶段启用。每项都需要 Windows
原版黄金结果、包内案例和至少一个脱敏真实案例。水头/浓度默认 `rtol=1e-5`、`atol=1e-7`；
水量/质量平衡差不超过 0.01 个百分点；PEST 参数和目标值 `rtol=1e-3`；机器学习使用固定
种子与指标区间。没有真实案例通过记录时只能声明“技术迁移完成”，不能声明生产科学等价。

发布固定镜像摘要与不可变算子版本；回滚恢复上一镜像摘要并停用新算子版本。运维至少监控
队列深度、耗时、失败率、协调未确认状态、磁盘、求解器退出码、校验失败和 GPU 使用量。
