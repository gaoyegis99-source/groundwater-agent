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

## 快照与归档

```bash
../bin/gw snapshot create --instance ID
../bin/gw snapshot verify --snapshot PATH
../bin/gw snapshot restore --snapshot PATH --instance ID
../bin/gw archive create --instance ID --run RUN_ID --level experiment
../bin/gw archive verify --archive PATH
```

恢复、删除和清理采用 plan/apply 与摘要确认。归档不包含 secret；benchmark 归档要求科学与工程
状态都为 PASS，experiment 归档保留成功或失败的可复现运行事实。

## 离线升级

```bash
../bin/gw migrate plan --instance ID --identity-map /absolute/mapping.json
../bin/gw migrate apply --plan PLAN --confirm DIGEST
```

升级先创建并验证快照，再在影子存储逐版本迁移。记录数、外键、对象 checksum、owner 映射和
lineage 全部通过后才切换实例指针；失败时保留旧存储。
