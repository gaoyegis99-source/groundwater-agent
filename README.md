# 地下水领域分析系统

**正式名称：地下水领域分析系统（Groundwater Domain Analysis System）**  
**内部工程代号：V6**

本目录是地下水领域分析系统的完整项目边界，包含源码、第三方服务源码、稳定启动器和维护证据。
项目根目录是唯一的 Git 仓库；`source/` 保存当前版本的主源码。
`V6` 不是跨产品版本序号；本系统与“企业科研工作流治理平台”（内部代号 `V7`）完全隔离，
后者不是本系统的升级版。两者不共享源码、发布、运行态、数据、身份、Docker 或生命周期。

Mutable runtime state no longer lives in a project `runtime/` directory.

The current schema 3 lifecycle is owned by XDG locations:

- configuration: `~/.config/groundwater-v6`
- data/control: `~/.local/share/groundwater-v6`
- audit state: `~/.local/state/groundwater-v6`
- rebuildable dependencies, toolchains, build output, and Docker data: `~/.cache/groundwater-v6`
- sockets and transient state: `/run/user/$UID/groundwater-v6`

Run the control plane from `source/` without activating a repository-local
environment:

```bash
cd source
../bin/gw doctor
../bin/gw deps status
../bin/gw clean cache          # plan only
```

Mutating lifecycle commands require the exact confirmation digest emitted by a
fresh plan. Do not manually delete XDG data, state, Docker, or active dependency
directories. Inspect Git status at the project root before editing source files.
