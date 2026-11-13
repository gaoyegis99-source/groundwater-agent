# 项目布局契约（schema 4）

仓库根 `project-layout.json` 是逻辑布局合同。它只允许 `source`、可选 linked worktree 映射、XDG 生命周期角色、隔离 Docker 参数、端口与账户；不得写入机器绝对路径或活动实例状态。

| 角色 | 实际默认路径 |
| --- | --- |
| 源码 | `<仓库根>/source` |
| 配置 | `$XDG_CONFIG_HOME/groundwater-v6` |
| 实例数据 | `$XDG_DATA_HOME/groundwater-v6/instances` |
| 状态与审计 | `$XDG_STATE_HOME/groundwater-v6/{instances,audit,logs}` |
| 工具链与构建缓存 | `$XDG_CACHE_HOME/groundwater-v6/{toolchains,deps,build,docker}` |
| socket/锁/PID | `$XDG_RUNTIME_DIR/groundwater-v6` |

`runtime/` 是退休布局，存在即由 `gw doctor` 报告 `LEGACY_RUNTIME_PRESENT`。源码 `.env` 同样被拒绝；profile 与 secrets 必须在 XDG config 中。

所有持久数据库、对象和任务数据都是 `instances/<id>/` 下的显式 bind mount。Docker data-root 只含镜像、容器元数据与 BuildKit cache，因此 `gw engine reset` 不会删除实例数据。

检查命令：

```bash
../bin/gw doctor
../bin/dev check --scope all
python -m scripts.checks.check_worktree_hygiene
python -m scripts.maintenance.check_internal_layout --scope source
```

设计理由和信任边界见[依赖与运行态架构](../architecture/dependency-runtime-architecture.md)。
旧目录迁移记录只保存在 Git 历史中，不是当前运行合同。
