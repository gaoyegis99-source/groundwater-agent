# 命令目录

`../bin/gw` 是统一入口，`../bin/bootstrap` 和 `../bin/dev` 是无项目虚拟环境的稳定兼容入口。所有命令都固定本项目 XDG 路径；Docker 命令只能通过 `../bin/docker` 连接 `$XDG_RUNTIME_DIR/groundwater-v6/docker.sock`。

## 无副作用检查与自举

```bash
../bin/gw doctor
../bin/bootstrap --sync
../bin/gw config status --profile research
../bin/gw dev compile --product groundwater
../bin/gw dev check --scope all
```

`doctor` 不联系 daemon。`bootstrap --sync` 是显式联网/安装动作；普通状态命令不会因为缺工具而自行联网。

## 日常运行

```bash
../bin/gw dev up --product groundwater
../bin/gw dev status --product groundwater
../bin/gw dev down --product groundwater
```

日常运行使用项目专属 rootless Docker，允许本地 build、缺失依赖 pull 和源码只读挂载。统一 Gateway
默认只监听 loopback；数据库、Knowledge 和 Executor 不发布宿主机端口。`dev status` 同时核对容器、
服务 readiness、Executor runtime 能力、镜像身份与当前源码摘要。

## 身份、快照与归档

```bash
../bin/gw auth bootstrap-admin
../bin/gw snapshot create --instance instance-a
../bin/gw snapshot verify --snapshot /absolute/snapshot.tar.gz
../bin/gw snapshot restore --snapshot /absolute/snapshot.tar.gz --instance instance-a
../bin/gw archive create --instance instance-a --run run-id --level experiment
../bin/gw archive verify --archive /absolute/archive.tar.gz
```

快照包含一致性存储副本、对象 checksum、实例配置和 schema 版本。异盘、加密、恢复演练与
RPO/RTO 可按项目需要增加，但不是默认运行门禁。

## 离线升级

```bash
../bin/gw migrate plan --instance instance-a --identity-map /absolute/mapping.json
../bin/gw migrate apply --plan /absolute/plan.json --confirm sha256:<64-hex>
```

迁移先创建并验证快照，再在影子存储逐版本升级；校验 schema、记录数、外键、对象 checksum、
owner 映射和 lineage 后才切换。失败时保持旧存储与快照可恢复。

## 破坏操作

以下命令第一次不带 `--confirm` 只输出 plan。审阅路径和对象 ID 后，把返回的完整 `confirmationDigest` 原样传给第二次调用；apply 会重新生成并比较计划，成功后在 XDG state 写入 receipt。

```bash
../bin/gw clean cache
../bin/gw clean cache --confirm sha256:<64-hex>

../bin/gw engine reset
../bin/gw engine reset --confirm sha256:<64-hex>

../bin/gw instance destroy --instance dev-a
../bin/gw instance destroy --instance dev-a --confirm sha256:<64-hex>

../bin/gw snapshot restore --snapshot /absolute/snapshot.tar.gz --instance dev-a
../bin/gw snapshot restore --snapshot /absolute/snapshot.tar.gz --instance dev-a --confirm sha256:<64-hex>
```

创建和启动不要求 confirmation digest；删除、恢复、清理与升级继续使用 digest 绑定的 plan/apply。
