# 科研运行与维护入口

V12 使用项目专属 rootless Docker 和单一 `research` profile。Gateway 默认只监听 loopback；数据库、Knowledge 和 Executor 不发布主机端口。

两套第三方地下水业务模型的隔离仓库、HTTPS 绑定、执行恢复和科学验收门禁见
[第三方地下水模型 Linux 服务接入](third-party-groundwater-models.md)。

```bash
../bin/gw doctor
../bin/gw dev up --product groundwater
../bin/gw dev status
../bin/gw dev down
```

`gw dev status` 同时检查容器、服务 readiness、Executor control/native/QGIS 能力、Knowledge 初始化状态和实际镜像/源码摘要。源码可只读挂载，Compose 可本地 build 和 pull。启动先等待常驻服务，再执行幂等知识目录对账；后者失败时状态为 `DEGRADED`，但 Kernel/Gateway 仍保留诊断能力。

首次使用通过 `gw auth bootstrap-admin` 创建本地管理员。实例升级、恢复、删除和清理仍使用 plan/apply 与摘要确认；普通创建和启动不要求 confirmation digest。

快照和归档：

```bash
gw snapshot create --instance ID
gw snapshot verify --snapshot PATH
gw snapshot restore --snapshot PATH --instance ID
gw archive create --instance ID --run RUN_ID --level experiment
gw archive verify --archive PATH
```

旧部署设计只通过 Git 历史查询，不属于当前运行合同。
