# 地下水领域分析系统

正式名称为**地下水领域分析系统（Groundwater Domain Analysis System）**，内部工程代号为 `V6`。它是面向地下水业务的单机
Docker + Compose 科研产品，当前公共合同为 API v12。`V6` 仅是本产品内部代号，不表示与其他
产品共同组成版本序列；“企业科研工作流治理平台”（内部代号 `V7`）不是本系统的升级版。

源码树只保存源码、声明、锁文件、Dockerfile、Compose 模板和测试；配置、数据、状态、构建输出、Docker data-root 与瞬时 socket 均位于 XDG 生命周期目录。仓库根不再创建 `runtime/`，源码内也不再把 `.generated/` 作为输出位置。

架构总入口是[依赖与运行态架构](docs/architecture/dependency-runtime-architecture.md)。产品/API 行为仍由[架构契约](docs/architecture/architecture-contract.md)、[产品清单](products/groundwater/product-manifest.json)和[HTTP API](docs/capabilities/http-api.md)定义，本次重构不改变这些公开合同。

同一仓库还提供独立的[轻量 Agent 工作流包](packages/lightweight-agent-core/README.md)：它可嵌入 Python 程序，不属于 Groundwater Compose 服务，也不以 Data、Knowledge 或容器为基础依赖。两条交付面的状态与迁移边界见[产品线边界](docs/architecture/product-line-boundary.md)。

## 从空运行态开始

以下入口不依赖项目虚拟环境：

```bash
../bin/gw doctor
../bin/gw dev up --product groundwater
../bin/gw auth bootstrap-admin
```

`doctor` 只检查科研运行必需项。统一 Gateway 默认仅监听 loopback 的 `28186`，数据库、Data、Knowledge 与 Executor 不发布宿主机端口。

日常研究使用项目专属 rootless Docker，可本地构建、拉取依赖并只读挂载源码：

```text
../bin/gw dev up --product groundwater
../bin/gw dev status
../bin/gw dev down
```

研究实例使用显式数据目录；Docker/BuildKit cache 可以重置而不触碰权威数据。

## 目录职责

| 位置 | 职责 |
| --- | --- |
| `kernel/` | Session、Agent、确认、任务与 Workflow 编排 |
| `services/` | Data、Knowledge、Executor 与统一 Gateway |
| `packages/` | wire contract、客户端、Workflow 编译、共享 UI 边界与独立轻量 Agent 包 |
| `domains/`、`products/`、`platform/assembler/` | 领域声明、产品清单与静态装配 |
| `environments/`、根锁文件 | Python 兼容域、Node、工具链与系统依赖锁 |
| `scripts/maintenance/` | `gw` 控制面、XDG 生命周期、快照、归档与迁移 |
| `schemas/operations/` | layout、instance 与科研操作的严格 JSON Schema |
| `tests/` | 当前可执行验证；运行报告写入 XDG state，不回写源码树 |

## 当前就绪性

代码使用 layout v4 与单一 `research` profile，提供科研开发运行、本地身份、校验快照、研究归档和逐版本影子迁移。签名、加密、企业身份代理和异盘备份均不是科研就绪前置条件。

常用操作见[命令目录](docs/maintenance/command-directory.md)，部署顺序见[运行维护](docs/operations/deployment/operations.md)，开发验证见[执行手册](docs/operations/execution-playbook.md)。
