# V12 参数登记

本表登记当前可配置参数的唯一解析所有者。它只记录名称、用途和敏感性，绝不记录 Token、密钥或本机值。
环境变量仍是兼容输入；业务模块消费经过组合/装配后得到的值对象或冻结配置，不自行读取相同参数。

| 参数组 | 所有者与解析点 | 消费者 | 默认/约束 | 发布或敏感性 |
| --- | --- | --- | --- | --- |
| `PLATFORM_IDENTITY_MODE` | Kernel/Gateway 启动边界 | 本地会话、API Token 和项目角色 | 固定为 `local` | 密码和 Token 明文不进入配置或日志 |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | `platform_protocol_contracts.tracing` | Kernel/Data/Knowledge/Executor/Worker | 可选、无凭据 HTTP(S) Collector；有界队列、失败开放 | 不记录正文、提示词、路径/查询值或凭据 |
| `OPENLINEAGE_URL`、`DATA_QUALITY_WEBHOOK_URL`、`MLFLOW_ADAPTER_URL` | Data `Settings` 与 outbox dispatcher | Data Worker 外部投影 | 可选、无凭据 HTTP(S) URL；稳定幂等键、租约、退避 | 外部失败不回滚业务事务；认证由同机 sidecar/私网入口承担 |
| `EXECUTOR_NATIVE_*`、`EXECUTOR_QGIS_*`、`EXECUTOR_REMOTE_HTTP_*` | `ExecutorOperationalProfile`，仅 assemble/candidate 边界 | `executor-config.v8`、Compose worker、assembly lock、候选 metadata、heartbeat 校验 | slots 1–64；memory 256 MiB–1 TiB | 发布档案的一部分；v6/v7 仅兼容读取 |
| `GROUNDWATER_SUSTAINABILITY_BASE_URL`、`GROUNDWATER_SUSTAINABILITY_ALLOWED_HOST` | Product v8 `RemoteServiceBinding` + Assembler | egress-only `remote_http` Worker | HTTPS；URL host 必须与允许主机精确相同 | 仅地址，不含凭据；Bearer 与 CA 由 secret 文件挂载 |
| `PLATFORM_AGENT_*` | `KernelSettings` + `ModelEndpointSettings` | Agent Provider、readiness、Kernel runtime | 协议、URL、token/window/timeout 均严格校验 | API key 仅从受限文件读取，不写入报告 |
| `PLATFORM_INTENT_ROUTER_*` | `IntentRouterSettings` + `ModelEndpointSettings` | Intent Router、readiness | 与 Agent 使用同一字段验证；保留历史缺协议错误码 | API key 仅从受限文件读取 |
| `PLATFORM_AGENT_CONCURRENCY`、`PLATFORM_AGENT_QUEUE_CAPACITY`、`PLATFORM_AGENT_SHUTDOWN_GRACE_SECONDS` | `KernelSettings` | `KernelTaskSupervisor` 和 metrics | 默认并发 8、等待 100；宽限期 1–300 秒 | 项目配额；队列满返回 429 |
| `PLATFORM_WORKFLOW_DISPATCH_CONCURRENCY` | `KernelSettings` | Workflow Execution Owner 与 metrics | 默认 8，范围 1–64；同时受共享 `PLATFORM_AGENT_CONCURRENCY` 容量限制 | 运行参数；不改变 Executor 自身容量 |
| `DATA_WORKER_SLOTS` | Data 启动/候选边界 | Data Worker | 1–8 | 运行容量；不属于 Executor profile |

验证入口：`python -m scripts.checks.check_configuration_boundaries`。该门禁不读取或打印环境值；它只检查
源码是否仍遵守上述解析边界。
