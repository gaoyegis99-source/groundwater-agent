# Vibecoding 本地快速参考

从仓库根目录进入 `source/` 后运行下列命令。

```bash
cd source
```

## 稳定入口与前置条件

`../bin/gw` 是统一入口；`../bin/bootstrap` 和 `../bin/dev` 只是转发到同一入口的兼容包装。
入口本身由宿主 `python3` 启动，以便 factory reset 后仍能执行诊断和自举。`dev` 不允许继续使用宿主
解释器：它先验证 `toolchain.lock.json` 对应的完整工具链状态，再以其中的 `V6_PYTHON` 重新执行；
缺失或 checksum、marker、重定位状态不符时稳定返回 `PROJECT_TOOLCHAIN_NOT_READY`。

| 入口 | 前置条件 | 联网或写入 |
|---|---|---|
| `../bin/gw doctor --json` | `project-layout.json`、宿主 `python3` | 不联系 Docker daemon；只读 |
| `../bin/bootstrap --status` | 宿主 `python3` | 不联网；只读锁定工具链状态 |
| `../bin/bootstrap --sync` | `uv`、网络、完整 root workspace locks | 显式联网；写摘要绑定的开发工具链与 uv cache |
| `../bin/gw deps status` | 宿主 `python3`、依赖锁 | 不联网；只读 materialization receipts |
| `../bin/gw deps verify --scope <scope>` | 已物化的 `python`、`npm` 或 `system` scope | 不联网；npm 会在临时目录做 offline `npm ci` |
| `../bin/gw deps materialize --scope <scope>` | 对应下载工具、锁和网络；`system` 还需要项目 Docker | 显式联网；只写 XDG dependency cache |
| `../bin/dev compile --product <id>` | `bootstrap --status` 必须为 `PASS`；有效 product manifest | 不需要 Docker；写 XDG assembly cache |
| `../bin/dev check --scope <scope>` | 锁定工具链，且满足下表 scope 前置条件 | 按 scope 运行；测试/构建输出只写临时目录或 XDG cache |
| `../bin/dev operator-smoke --operator-id <id> --operator-version <version>` | 锁定 root 工具链；已验证且与当前 Python locks 兼容的 wheelhouse；锁定 `uv`/CPython | 离线创建并删除 XDG 临时 Native 环境；只保留 XDG operator run |
| `../bin/dev workflow-golden --case <id> [--runtime-image <local-image>]` | 与单算子相同的锁定 Native 前置条件；需要 GDAL 的 case 还要求项目 rootless daemon 中已有、版本匹配的本地 Linux/amd64 Native image | 不 pull、不联网；临时环境退出即删，只发布 XDG workflow run；可选 image 仅提供只读系统工具 |
| `../bin/dev agent-replay --case <id\|all> [--repetitions 3]` | 锁定 root 工具链；签入的离线 case index 与 Kernel 开发依赖 | `ScriptedModel` 与 `MockTransport` 全离线运行；只发布 XDG replay snapshots，不修改默认 runtime |
| `../bin/dev capability-inventory [--verify]` | 锁定 root 工具链；有效 product/domain/catalog/UI 声明 | 默认确定性更新签入的开发参考表；`--verify` 只读检查是否漂移，不联网、不装配产品 |
| `../bin/dev clean --dry-run` | 锁定工具链 | 只列候选项，不删除 |

`bootstrap --sync` 调用既有 `uv sync --locked --all-packages --all-groups`，预期 CPython
`3.12.8`。它不负责物化 Docker build 所需的 wheel、npm tarball 或系统包；后者由 `gw deps`
单独管理。两者都不写源码目录中的虚拟环境。

`dev check` 的有效 scope 为 `kernel`、`data`、`knowledge`、`executor`、`assembler`、
`contracts`、`web`、`integration` 和 `all`。每个 scope 都需要 Ruff，并在末尾执行 Ruff 与
`git diff --check`；Kernel/全量需要 mypy，Knowledge/全量需要隔离的 `*_test` 数据库，
Contracts/Web/Integration/全量需要 npm 与相应 `node_modules`，Data/Integration/全量在没有外部
测试数据库时需要项目专属 Docker。Executor/全量另走锁定 Native operator/worker-wheel 门禁。

## 单算子快速运行

默认用现有 catalog smoke，并复用 `moperators.runtime.task`，不复制算子 runtime：

```bash
../bin/dev operator-smoke \
  --operator-id timeseries.gap_runs \
  --operator-version 1.0.0 \
  --fixture observations=monitoring.timeseries \
  --parameter 'frequency="month"'
```

`--fixture PORT=FIXTURE_ID` 与 `--parameter NAME=JSON` 可重复。未指定的值来自已签入的
`smoke.json`。`--output-dir` 可以指定新目录，但必须位于
`$XDG_CACHE_HOME/groundwater-v6/operator-runs/` 下；省略时生成 UUID。成功目录包含
`request.json`、`runtime-result.json`、`summary.json`、输入副本和输出 artifact。摘要绑定 catalog、
manifest、smoke、调用参数、fixture、运行时锁和每个输出的 SHA-256；只有合同全部通过后才原子发布。

Native 快速运行不会安装到默认 root/Native/QGIS 环境。它只读核对当前或旧摘要下与所有 Python
lock inputs 仍一致的 materialization receipt，验证每个 wheel、锁定 uv 与 CPython binary SHA-256，
再以 `--require-hashes --offline --no-index` 在 XDG build 临时目录安装 Native lock。临时环境和 uv
cache 在命令结束时删除。

## Workflow golden

Workflow golden 复用现有 Workflow Compiler 与同一生产 `moperators.runtime.task`，不实现第二套
executor。当前签入 `monitoring-trend`、`render-map` 和 `flow-field` 三个 case：

```bash
../bin/dev workflow-golden --case monitoring-trend
../bin/dev workflow-golden \
  --case flow-field \
  --runtime-image source-groundwater-executor-native-worker:latest
```

每次运行都按稳定拓扑执行 workflow，复核 fixture 与 operator output 合同，并把 workflow checksum、
catalog digest、参数、fixture、每个输出的 identity 及选定关键数值写入 snapshot。签入 baseline 是
`products/<product>/development/workflow-goldens/*.json`；不一致时 `summary.json` 中的 `differences` 以 JSON Pointer
精确定位缺失字段、类型、值或具体数值变化。只有差异为空时状态才是 `PASS`。

带 GDAL 的用例需要项目 Docker 中已存在的兼容 Native 镜像。命令使用离线只读容器提供系统工具。

`--output-dir` 必须是 `$XDG_CACHE_HOME/groundwater-v6/workflow-golden-runs/` 下尚不存在的目录。
成功目录包含逐节点 replay request、runtime result、输入/输出、副本 snapshot 与 summary；失败或
golden mismatch 仍发布可诊断证据，并返回非零。

## Agent 离线回放

Agent replay 复用 `kernel/tests` 的 `ScriptedModel`/`ModelStep` 和真实
`PlatformApplication`、Agent runtime、Confirmation、Preparation、Workflow Draft 与 WorkflowRun
状态机。它不实现第二套 Agent，也不把脚本响应描述成真实 Provider 表现：所有下游服务都由
`httpx.MockTransport` 在进程内提供，运行期间不访问网络。

```bash
../bin/dev agent-replay --case all
../bin/dev agent-replay --case fan-out-draft --repetitions 3
```

签入 case index 固定六个边界：普通 DIALOGUE 不执行、明确执行提案停在 Confirmation、缺字段要求
用户修正、fan-out 草稿、MiniMax 非案例执行终态，以及引用只能来自本轮成功读取的 Knowledge unit。
默认重复三轮；每轮生成归一化 snapshot，逐项验证不变量、Confirmation 数和 WorkflowRun 数，并要求
同一 case 的三轮 snapshot SHA-256 完全一致。`all` 成功时，执行提案为 1 次 Confirmation/0 个
WorkflowRun，MiniMax 完整执行为 1/1，其余 case 均为 0/0。

`--output-dir` 必须是 `$XDG_CACHE_HOME/groundwater-v6/agent-replay-runs/` 下尚不存在的目录。
成功目录包含冻结 case index、每个 case 的三份迭代 snapshot 和 `summary.json`；临时 SQLite 与工作目录
在原子发布前删除。真实 Provider、Compose 和浏览器验收分别运行。

## 当前功能清单

功能参考表必须从当前产品选择的 domain package、`domain-manifest.json`、Operator Catalog index 和
Product UI 声明生成，不手写“模型数量”或“算子数量”作为事实来源：

```bash
../bin/dev capability-inventory
../bin/dev capability-inventory --verify
```

默认命令确定性更新签入的
[当前功能清单](current-capability-inventory.md)；`--verify` 重新加载并使用 Assembler 的相同验证路径
核对产品、领域、workflow、catalog 与 UI 声明，然后要求生成内容与签入文件逐字节一致。清单包含每个
模型的说明与适用范围、workflow 节点组成和精确输出/viewer、完整 catalog runtime 分布，以及哪些算子
实际被当前领域 workflow 使用。数量和表格行均由源声明动态派生。

当前计数及 domain package、catalog manifest、Assembler execution catalog 和 Product UI 的校验和
记录在生成清单的身份与统计表中。生成器分别核对 catalog manifest 与 Assembler execution catalog
的摘要。Pastas/MF6 独立 spike 与 `remote_http` 声明的运行可用性需要单独验证。

## 输出位置

| 内容 | 当前解析位置 |
|---|---|
| 锁定开发工具链 | `$XDG_CACHE_HOME/groundwater-v6/toolchains/<toolchain-id>` |
| uv cache | `$XDG_CACHE_HOME/groundwater-v6/uv` |
| 当前依赖 materialization | `$XDG_CACHE_HOME/groundwater-v6/deps/<digest>` |
| 产品 assemblies | `$XDG_CACHE_HOME/groundwater-v6/build/assemblies/.releases/<product>/<digest>` |
| 当前 assembly pointer | `$XDG_CACHE_HOME/groundwater-v6/build/assemblies/<product>.current.json` |
| worktree validation cache | `$XDG_CACHE_HOME/groundwater-v6/build/validation/worktrees/<worktree-id>` |
| 单算子运行 | `$XDG_CACHE_HOME/groundwater-v6/operator-runs/<run-id>` |
| 单算子临时 Native 环境 | `$XDG_CACHE_HOME/groundwater-v6/build/operator-smoke-runtime/native-*`（命令结束后删除） |
| Workflow golden 运行 | `$XDG_CACHE_HOME/groundwater-v6/workflow-golden-runs/<run-id>` |
| Agent 离线回放 | `$XDG_CACHE_HOME/groundwater-v6/agent-replay-runs/<run-id>` |
| 当前功能清单 | `docs/development/current-capability-inventory.md`（签入源码、确定性生成） |
| validation receipts | `$XDG_STATE_HOME/groundwater-v6/audit/validation/worktrees/<worktree-id>` |
| 项目 Docker socket | `$XDG_RUNTIME_DIR/groundwater-v6/docker.sock` |
