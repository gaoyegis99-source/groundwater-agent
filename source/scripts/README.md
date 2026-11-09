# 脚本入口

用户稳定入口是 `../bin/gw`。脚本模块按职责分层，直接调用只适合测试或开发：

| 目录 | 职责 |
| --- | --- |
| `maintenance/` | 无依赖控制面：XDG、自举、daemon、依赖、实例、快照、归档、迁移、清理 |
| `development/` | 静态装配、合同生成、按范围选测与 `dev up/status/down` |
| `checks/` | 静态、合同、隔离 Compose 与数据/知识联测 |
| `operations/` | 完整项目验证 |
| `release/` | wheel 内容与源码快照检查 |
| `migration/` | 业务数据导入，不负责运行态目录迁移 |
| `evaluation/` | Agent、路由、容量与压力评估 |

脚本不得自行选择宿主 Docker。需要 daemon 的模块必须使用 `scripts.maintenance.project_environment.docker_executable()`，最终只会调用 `../bin/docker` 和项目 socket。

生成文件默认写入 XDG cache，审计报告默认写入 XDG state；测试夹具使用临时目录。源码 `.generated` 仅是待清理的历史残留，不是受支持接口。

高风险操作只能通过 `gw` 暴露的 plan → apply → receipt 接口。
