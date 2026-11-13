# Groundwater V12 文档入口

当前主线是面向可信单机和小型实验室的科研运行平台：Public API v12、Kernel SQLite v34、Data v8、Knowledge v7、实例清单 v4。

按任务读取相关文档：

| 任务 | 当前入口 |
| --- | --- |
| 本地运行与当前能力 | [仓库 README](../README.md)、[执行手册](operations/execution-playbook.md)、[交互式增量开发](development/interactive-incremental-mode.md)、[Vibecoding 快速参考](development/vibecoding-quick-reference.md)、[当前功能清单](development/current-capability-inventory.md) |
| 架构与服务边界 | [架构总览](architecture/architecture-overview.md)、[结构与运行流](architecture/architecture-structure-and-flow.md)、[协议与存储](reference/contracts.md) |
| HTTP、身份和权限 | [HTTP API](capabilities/http-api.md)、[WebUI](capabilities/webui.md) |
| 科研验收与归档 | [科研验收](operations/acceptance-gates.md)、[地下水研究证据链](capabilities/groundwater-research-evidence.md) |
| 部署、依赖和密钥 | [部署维护](operations/deployment/README.md)、[Groundwater 实时监控](operations/live-monitor.md) |
| 代码与文件整理 | [仓库目录与归属](maintenance/repository-layout.md)、[命令目录](maintenance/command-directory.md) |
| 历史工程计划 | [工程计划归档](../../plans/README.md) |

能力服务说明：

- [Data](../services/data/README.md)
- [Knowledge Next](../services/knowledge-next/README.md)
- [Executor](../services/executor/README.md)
- [Kernel](../kernel/README.md)

旧版本设计通过 Git 历史查询，不在当前文档中复制维护。
