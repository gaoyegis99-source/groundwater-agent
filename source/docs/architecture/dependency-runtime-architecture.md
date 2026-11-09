# V12 依赖与运行态架构

V12 的日常入口是 `gw dev up/status/down`。Compose 使用项目专属 rootless Docker，允许本地 build、缺失镜像 pull 和源码只读挂载；实例数据位于 XDG 管理目录，secret 只以受限文件注入。

依赖锁和镜像 digest 用于记录环境与重建研究包。Assembler 把锁中的 digest 引用声明为
`docker-image://` BuildKit named context；本地缺失时由 rootless Docker 直接拉取，不维护第二份
OCI layout、临时 registry 或离线 builder。

服务只由统一 Gateway 发布 loopback 端口。数据库、Knowledge、Executor 和 worker 保持 Compose 内网可达。破坏性删除、恢复与迁移使用 plan/apply；普通构建和启动不要求确认摘要。

研究包记录 Git commit、dirty patch、未跟踪源码、依赖锁、装配配置、实际镜像和环境摘要。
