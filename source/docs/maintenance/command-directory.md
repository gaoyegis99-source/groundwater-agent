# 命令目录

`../bin/gw` 是统一入口，`../bin/bootstrap` 和 `../bin/dev` 是无项目虚拟环境的稳定兼容入口。所有命令都固定本项目 XDG 路径；Docker 命令只能通过 `../bin/docker` 连接 `$XDG_RUNTIME_DIR/groundwater-v6/docker.sock`。

## 无副作用检查与自举

```bash
../bin/gw doctor
../bin/bootstrap --status
../bin/bootstrap --sync
../bin/gw deps status
../bin/gw config status --profile research
../bin/gw dev compile --product groundwater
../bin/gw dev check --scope all
```

`doctor` 不联系 daemon；`bootstrap --status` 只核对摘要绑定工具链的 Python 版本、binary checksum、
marker 和重定位状态。`bootstrap --sync` 是显式联网/安装动作。`dev` 只以验证通过的锁定 Python
重新执行，工具链未就绪时返回 `PROJECT_TOOLCHAIN_NOT_READY`，不会静默使用宿主解释器。
普通状态命令不会因为缺工具而自行联网。M0-01 的当前 checkout 实测结果见
[Vibecoding 本地快速参考](../development/vibecoding-quick-reference.md)。

## 锁定依赖缓存

```bash
../bin/gw deps status
../bin/gw deps verify --scope python
../bin/gw deps verify --scope npm
../bin/gw deps verify --scope system
../bin/gw deps materialize --scope python --scope npm
```

`status` 和 `verify` 不从 registry 下载；`materialize` 是显式联网动作。三个有效 scope 为
`python`、`npm`、`system`，输出写入 `$XDG_CACHE_HOME/groundwater-v6/deps/sha256-<digest>`。

## 单算子快速运行

```bash
../bin/dev operator-smoke \
  --operator-id timeseries.gap_runs \
  --operator-version 1.0.0 \
  --fixture observations=monitoring.timeseries \
  --parameter 'frequency="month"'
```

命令复用 catalog 中的 smoke 和生产 `moperators.runtime.task`。fixture 与 JSON 参数覆盖均可重复；
成功结果原子写入 `$XDG_CACHE_HOME/groundwater-v6/operator-runs/<run-id>`，包含可回放 request、
runtime result、摘要及输入/输出 SHA-256。显式 `--output-dir` 也只能位于该目录下。

Native 执行使用锁定 uv、CPython 与已验证的 hash-pinned wheelhouse，在 XDG build 中创建一次性离线
环境；不会安装或修改默认 root/Native/QGIS runtime。公共 `dev` 入口仍要求摘要绑定 root 工具链先
通过验证；缺失时稳定返回 `PROJECT_TOOLCHAIN_NOT_READY`。当前 checkout 的实测正例、负例与边界见
[Vibecoding 本地快速参考](../development/vibecoding-quick-reference.md)。

## Workflow golden

```bash
../bin/dev workflow-golden --case monitoring-trend
../bin/dev workflow-golden \
  --case flow-field \
  --runtime-image source-groundwater-executor-native-worker:latest
```

命令复用现有 Workflow Compiler 和生产 task 子进程，按签入的 case 执行 `monitoring-trend`、
`render-map` 或 `flow-field`，并与 `products/<product>/development/workflow-goldens/` 中的 snapshot 比较。
summary 记录 workflow/catalog/fixture/output identity、关键值和 JSON Pointer 级差异；结果原子写入
`$XDG_CACHE_HOME/groundwater-v6/workflow-golden-runs/<run-id>`。

Native Python 仍来自一次性离线锁定环境。`--runtime-image` 只为需要 GDAL 的节点提供系统工具；它
必须是项目 rootless daemon 中已有、架构和锁定包版本均匹配的本地 image。运行时禁网、只读、
不 pull、不挂载 Docker socket，也不修改默认 root/Native/QGIS runtime。公共 `dev` 入口仍先要求
锁定 root 工具链就绪。当前三个 case 的重复 PASS、image identity 与边界见
[Vibecoding 本地快速参考](../development/vibecoding-quick-reference.md)。

## Agent 离线回放

```bash
../bin/dev agent-replay --case all
../bin/dev agent-replay --case fan-out-draft --repetitions 3
```

命令复用 Kernel 的真实 Agent/Confirmation/Preparation/Workflow Draft/WorkflowRun 状态机，并以
`ScriptedModel` 和进程内 `MockTransport` 固定 Provider 与下游响应。签入的六个 case 默认各重复三轮，
逐项核对不变量、Confirmation/WorkflowRun 数量和 snapshot identity；它不会访问真实 Provider，也不
代表真实模型准确率。结果原子写入
`$XDG_CACHE_HOME/groundwater-v6/agent-replay-runs/<run-id>`，显式 `--output-dir` 只能位于该目录下。
公共入口仍要求锁定 root 工具链先通过验证；当前实测结果和边界见
[Vibecoding 本地快速参考](../development/vibecoding-quick-reference.md)。

## 当前功能清单

```bash
../bin/dev capability-inventory
../bin/dev capability-inventory --verify
```

默认命令从产品选择的领域包、domain manifest、Operator Catalog index 和 Product UI 声明确定性生成
[当前功能清单](../development/current-capability-inventory.md)。`--verify` 只读重算并检查签入表是否漂移；
模型、workflow、算子、runtime、UI 示例和 viewer 数量均来自已验证源声明，不维护平行的手写计数。
清单只描述当前声明和 workflow 使用关系，不把独立 Pastas/MF6 spike 或 `remote_http` 声明解释为默认
部署能力。公共入口仍要求锁定 root 工具链先通过验证。

## 日常运行

```bash
../bin/gw dev up --product groundwater
../bin/gw dev status --product groundwater
../bin/gw dev down --product groundwater
```

日常运行使用项目专属 rootless Docker，允许本地 build、缺失依赖 pull 和源码只读挂载。统一 Gateway
默认只监听 loopback；数据库、Knowledge 和 Executor 不发布宿主机端口。`dev status` 同时核对容器、
服务 readiness、Executor runtime 能力、镜像身份与当前源码摘要。

## 交互式增量开发

```bash
../bin/dev interactive --product groundwater --dry-run
../bin/dev interactive --product groundwater
../bin/dev interactive-status --product groundwater
```

交互模式把 Vite HMR、Uvicorn 源码 reload、worker 定点重启、装配调和和受影响检查放在独立开发控制面中。实现只位于 `scripts/development/interactive/`；系统运行时不导入它，生成的 Compose 覆盖、状态、事件和日志只写入 XDG state。控制台和所有额外服务端口只监听 loopback。完整端口、增量分类、停止语义和故障定位见[交互式增量开发](../development/interactive-incremental-mode.md)。

## 身份、快照与归档

```bash
../bin/gw auth bootstrap-admin
../bin/gw snapshot create --instance instance-a
../bin/gw snapshot verify --snapshot /absolute/snapshot.tar.gz
../bin/gw snapshot restore --snapshot /absolute/snapshot.tar.gz --instance instance-a
../bin/gw archive create --instance instance-a --run run-id --level experiment
../bin/gw archive verify --archive /absolute/archive.tar.gz
```

快照包含一致性存储副本、对象 checksum、实例配置和 schema 版本。异盘、加密、恢复演练与
RPO/RTO 可按项目需要增加，但不是默认运行门禁。

## 离线升级

```bash
../bin/gw migrate plan --instance instance-a --identity-map /absolute/mapping.json
../bin/gw migrate apply --plan /absolute/plan.json --confirm sha256:<64-hex>
```

迁移先创建并验证快照，再在影子存储逐版本升级；校验 schema、记录数、外键、对象 checksum、
owner 映射和 lineage 后才切换。失败时保持旧存储与快照可恢复。

## 破坏操作

以下命令第一次不带 `--confirm` 只输出 plan。审阅路径和对象 ID 后，把返回的完整 `confirmationDigest` 原样传给第二次调用；apply 会重新生成并比较计划，成功后在 XDG state 写入 receipt。

```bash
../bin/gw clean cache
../bin/gw clean cache --confirm sha256:<64-hex>

../bin/gw engine reset
../bin/gw engine reset --confirm sha256:<64-hex>

../bin/gw instance destroy --instance dev-a
../bin/gw instance destroy --instance dev-a --confirm sha256:<64-hex>

../bin/gw snapshot restore --snapshot /absolute/snapshot.tar.gz --instance dev-a
../bin/gw snapshot restore --snapshot /absolute/snapshot.tar.gz --instance dev-a --confirm sha256:<64-hex>
```

创建和启动不要求 confirmation digest；删除、恢复、清理与升级继续使用 digest 绑定的 plan/apply。
