# 地下水水源地可持续利用综合评价 Linux 服务

这是以原 Linux 服务 `0.6.4` 为基线的 `0.7.0` 安全兼容版本。现有同步评价、异步任务、结果与下载路径保持不变；业务接口统一要求 Bearer 密钥，并新增统一第三方任务协议、分片上传、就绪/版本、幂等协调和排队任务取消接口。

模型仍是非正式评价工具；指南中点权重属于实现选择，不应把结果解释为行政审批结论。结果中的 `calculated_at` 是易变元数据，数值回归时应排除。

## 启动

创建仅部署账号可读、至少 32 个字符且末尾无换行的 `secrets/api-key`，由同机反向代理终止 HTTPS，然后执行：

```bash
docker compose up --build -d
```

容器以 UID 10001、只读根文件系统、无 Linux capabilities 运行；SQLite、结果和上传文件只写入独立持久卷。Compose 只把 HTTP 端口绑定到回环地址，平台必须经 HTTPS 反向代理访问。

匿名端点：`GET /healthz`、`GET /readyz`、`GET /version`。其余业务端点使用 `Authorization: Bearer <key>`。

## 兼容与统一协议

- 原 API：`POST /api/v1/sustainability/evaluate`、`POST /api/v1/sustainability/tasks` 以及状态、结果和下载子资源。
- 协调与取消：按 `client_request_id` 查询任务；排队任务可取消。任务已经运行时返回 `cancellationAccepted=false`，不会伪报终止。
- 平台协议：`/api/v1/uploads` 分片上传，`POST /api/v1/operations` 提交固定操作 `sustainability.evaluate`，并通过状态、取消、结果和 receipt 子资源协调。
- `/api/v1/capabilities` 返回上传上限和分片大小；服务重启时未完成任务标记失败，不自动重跑。

发布必须固定镜像摘要；此仓库 Dockerfile 已固定 `linux/amd64` Python 基础镜像摘要，
`uv.lock` 与带下载摘要的 `requirements.lock` 固定全部 Python 传递依赖。
