# 所有权与维护边界

| 领域 | 所有者 | 明确不拥有 |
| --- | --- | --- |
| Public API、Agent、编排 | `kernel/` | 产品硬编码、服务私有数据库 |
| Data/Knowledge/Executor | 各自 `services/<name>/` | 其他服务实现或存储 |
| 跨服务 wire contract | `packages/protocol-contracts/` | 运行编排和部署策略 |
| 产品与领域事实 | `products/`、`domains/` | 通用运行时机制 |
| 静态装配 | `platform/assembler/` | 启停实例和写业务数据 |
| 生命周期控制面 | `scripts/maintenance/` | Public API 与业务规则 |
| 审计与操作回执 | XDG state | 活动配置、源码或业务正文 |

`gw` 控制面拥有路径解析、rootless daemon 证明、实例清单、快照、归档、迁移和破坏操作回执。
它不能绕过 Kernel 身份、项目角色、对象 owner 或业务确认。配置由 `research` profile 所有，
秘密由逐服务文件所有，权威数据由一个 instance ID 所有；Docker 命名卷不拥有业务数据。
