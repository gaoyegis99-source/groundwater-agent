# 运行、快照与升级操作

## 日常运行

```bash
../bin/gw doctor
../bin/gw dev up --product groundwater
../bin/gw dev status --product groundwater
../bin/gw dev down --product groundwater
```

日常运行使用项目 rootless Docker，允许本地 build、拉取缺失的 digest 固定基础镜像和源码只读
挂载。Gateway 只监听 loopback；数据库和 worker 不发布主机端口。

`dev status` 的 `runningAssemblyDigest` 来自容器的配置挂载，`compiledAssemblyDigest`
来自当前源码生成的装配。状态检查按运行装配核对服务清单和 Knowledge 初始化回执。
配置挂载无法识别或指向不同装配时，初始化状态显示 `UNKNOWN`。

`dev clean --dry-run` 列出可清理项。清理保留当前装配和本地容器仍引用的装配，
包括可以重新启动的容器。无法核验 Docker 挂载时，保留旧装配。

## 快照与归档

```bash
../bin/gw snapshot create --instance ID
../bin/gw snapshot verify --snapshot PATH
../bin/gw snapshot restore --snapshot PATH --instance ID
../bin/gw archive create --instance ID --run RUN_ID --level experiment
../bin/gw archive verify --archive PATH
```

归档活动实例时，从该实例的 Kernel 容器读取运行证据，并核对挂载对应的实例数据目录。停机快照在临时副本中核对 SQLite 版本。维护查询活动 WAL 数据库时也应在容器内执行；宿主机上的 `mode=ro` 仍可能创建辅助文件。

恢复、删除和清理采用 plan/apply 与摘要确认。归档不包含 secret；benchmark 归档要求科学与工程
状态都为 PASS，experiment 归档保留成功或失败的可复现运行事实。

## 离线升级

```bash
../bin/gw migrate plan --instance ID --identity-map /absolute/mapping.json
../bin/gw migrate apply --plan PLAN --confirm DIGEST
```

升级先创建并验证快照，再在影子存储逐版本迁移。记录数、外键、对象 checksum、owner 映射和
lineage 全部通过后才切换实例指针；失败时保留旧存储。
