# 交互式增量开发模式

该模式把“源码变化—最小运行时更新—受影响检查—可观测反馈”组成一个本机闭环，同时保持默认系统运行时不变。

## 启动与停止

```bash
../bin/bootstrap --sync
../bin/gw deps materialize --scope python --scope npm --scope system
../bin/dev interactive --product groundwater --dry-run
../bin/dev interactive --product groundwater
```

启动后按 `Ctrl-C` 停止文件监视器、控制台和由它创建的 Vite 进程。Compose 服务会保留，便于继续诊断；需要停止服务时显式执行：

```bash
../bin/dev down --product groundwater
```

`--once --no-ui --no-validate` 只完成装配、开发覆盖校验、服务启动和状态采集，适合启动验收。`--no-ui` 不启动 Vite，`--no-validate` 不运行按变化范围选择的检查。`interactive-status` 读取最近一次原子状态文件：

```bash
../bin/dev interactive-status --product groundwater
```

## 与系统代码的隔离

| 层 | 位置 | 约束 |
| --- | --- | --- |
| 开发控制面 | `scripts/development/interactive/` | 文件监视、进程监管、Compose 开发覆盖、Vite 配置和本机可观测端点只在这里实现 |
| 默认系统运行时 | `kernel/src`、`services/*/src`、`packages/*/src` | 不导入开发控制面；默认命令、镜像入口和产品合同不含开发控制逻辑 |
| 生成状态 | `$XDG_STATE_HOME/groundwater-v6/research-dev/<product>/interactive/` | `status.json`、`events.jsonl`、日志和 `compose.interactive.json`；不写入源码树或装配 bundle |
| 源码挂载 | `/workspace` | 容器内只读；Python 不写 `pyc` |

默认装配仍只把 Gateway 发布到 loopback `28186`。交互模式最后叠加 XDG 中的 Compose 覆盖文件，并只在 loopback 临时发布开发 API；覆盖文件不会复制进装配、镜像或发布产物。架构测试会阻止系统运行时反向导入开发控制面。

## 增量策略

| 变化 | 反馈方式 | 是否重建系统装配 |
| --- | --- | --- |
| `web/`、Data UI、Knowledge UI | 宿主 Vite HMR | 否 |
| Kernel、Data API、Knowledge API、Executor Control Python | 容器内 Uvicorn stat reload；显式监视 `/workspace/.../src` | 否 |
| Data、Knowledge、Executor worker | 只重启受影响的 Compose worker | 否 |
| Product、Domain、Assembler、合同、锁文件、Dockerfile、环境定义 | 重新编译装配并调和开发 Compose | 是 |
| 控制器自身 | 记录 `controller.restart-required` | 需人工重启控制器 |

变更批次经过 debounce，连续批次会合并；应用和验证严格串行，避免多个重启或测试同时修改共享状态。验证失败不会杀死控制器，而会记录稳定事件和 XDG 日志。

## 本机端口与可观测性

| 端口 | 用途 |
| ---: | --- |
| `28185` | 开发控制台、状态、事件和控制器指标 |
| `28186` | 默认 Gateway |
| `5173` / `5174` / `5175` | 主界面 / Data UI / Knowledge UI Vite HMR |

控制台只允许绑定 `127.0.0.1`。可用端点：

- `/status`：控制器、增量 supervisor、Compose readiness 和 Vite 进程状态；
- `/events`、`/events/stream`：有界事件快照和 SSE；
- `/metrics`：开发控制器 OpenMetrics；
- `/metrics/services/kernel|data|knowledge|executor`：通过项目 Compose 边界读取声明过的内部服务指标。

Kernel、Data、Knowledge 和 Executor Control 仍只存在于 Compose 内部网络，不额外占用宿主端口；三个 Vite 入口统一通过 Gateway `28186` 访问 API。这避免与其他本地实例冲突，也保持与生产路由一致。事件会把源码绝对路径转换为仓库相对路径，并按 key 屏蔽 token、password、secret、credential 和 API key。指标代理不接受任意服务名或 URL。

## 配置与故障定位

交互模式复用 `profiles/research.env` 和 `secrets/research/`，不会创建第二套系统配置。产品声明的远程服务即使不被调用，也必须让 Compose 能解析地址和 secret 挂载；本地默认应使用 `.invalid` 保留域并提供不可用占位挂载，使误调用失效关闭。

常用诊断：

```bash
../bin/gw config status --profile research
../bin/dev interactive-status --product groundwater
curl --fail http://127.0.0.1:28185/status
curl --fail http://127.0.0.1:28185/metrics
```

Vite 和增量验证日志位于交互模式 XDG 状态目录的 `logs/`。控制器退出后，状态文件保留最终 `STOPPED` 或 `FAILED` phase，便于事后分析。

## 全量验收报告

增量检查用于快速反馈，不替代全量门禁。实现冻结后执行：

```bash
../bin/dev check --scope all
python -m scripts.operations.test_full_project --product groundwater
```

完整项目 runner 在 XDG validation state 中生成 `full-project-test-report.json`，并同步生成同名 Markdown 中文报告。JSON 保存阶段、命令结果、测试计数、隔离身份和证据 checksum；Markdown 只汇总结论，不复制命令 payload 或敏感日志。工程通过不能推断科学有效性、真实 Provider 表现或第三方服务可用性。
