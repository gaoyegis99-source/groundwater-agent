# V12 容器拓扑与最小权限

V6 只通过项目根 `bin/docker` 连接当前用户的 `$XDG_RUNTIME_DIR/groundwater-v6/docker.sock`。包装器拒绝 host/context 覆盖，并在操作前验证 daemon 的 Docker Root Dir 是 `$XDG_CACHE_HOME/groundwater-v6/docker`。

## 装配网络

| 网络 | 成员 | 目的 |
| --- | --- | --- |
| `v6-ingress` | 统一 Gateway | 唯一的 loopback 北向入口 |
| `v6-service` | Gateway、Kernel、Data、Knowledge、Executor Control | 内部 HTTP |
| `v6-data-store` | Data PostgreSQL、迁移、API/Worker | Data 数据库 |
| `v6-knowledge-store` | Knowledge pgvector、迁移、API/Worker | Knowledge 数据库 |
| `v6-executor` | Executor Control、Native/QGIS Worker | 受控执行 |
| `v6-egress` | Kernel、Knowledge | 明确的 Provider/Embedding 出站 |

research profile 只把统一 Gateway 的 `28186` 发布到 loopback。数据库、内部 API 和 worker 不对主机发布端口。

## 数据与秘密

每个持久挂载都解析到当前 instance data root 的普通目录。Compose 顶层不声明持久命名卷，不挂载 Docker socket；科研开发可将源码只读挂载到服务容器。秘密由顶层 file secret 声明，并只授予实际消费者；容器内路径为 `/run/secrets/<name>`。

普通服务使用非 root 用户、只读根文件系统、受限 tmpfs、`cap_drop: ALL` 和 `no-new-privileges`。PostgreSQL/migration 和 QGIS 临时态的必要写权限必须在 Compose 中显式声明。Native/QGIS worker 不拥有 daemon socket。

容器和网络标签包含 instance ID 与 Compose project。科研开发允许本地 build 和缺失镜像 pull；`gw dev status` 报告实际镜像与源码摘要。
