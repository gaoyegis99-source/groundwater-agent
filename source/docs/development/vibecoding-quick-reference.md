# Vibecoding 本地快速参考

本页是 M0-01/M0-02/M0-03/M0-04/M0-05 在 2026-11-10 对 commit
`3354fc2d959219f399ed667b993dcbdf3d0db89e` 及其当前未提交工作树的实测记录。
它只列出本次确实执行过的命令；未执行项统一标为 `NOT_RUN`。开发检查不等于发布验收。

所有命令从仓库的 `source/` 目录运行：

```bash
cd /home/mtd/liweigis/workflow/groundwater-v6/source
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

## 当前实测结果

| 命令 | 结果 | 关键证据 |
|---|---|---|
| `../bin/gw --help` | `PASS` | 显示 `bootstrap`、`deps`、`dev` 等统一子命令 |
| `../bin/gw doctor --json` | `PASS` | 配置、XDG 权限、rootless mapping、Docker/Compose CLI 均通过；`docker_contacted=false` |
| `../bin/bootstrap --help` | `PASS` | 只接受互斥的 `--status` 或 `--sync` |
| `../bin/bootstrap --status` | `MISSING` | 预期 Python 3.12.8；目标 Python、marker 与实际 binary 均缺失 |
| `../bin/gw deps --help` | `PASS` | 显示 `status`、`materialize`、`verify` |
| `../bin/gw deps status` | `NOT_READY` | 当前摘要下 `python`、`npm`、`system` 三个 scope 均为 `MISSING`；system lock 自身为 `PASS` |
| `../bin/gw deps verify --scope python` | `FAILED`（exit 1） | `DEPENDENCY_SCOPE_NOT_MATERIALIZED` |
| `../bin/gw deps verify --scope npm` | `FAILED`（exit 1） | `DEPENDENCY_SCOPE_NOT_MATERIALIZED` |
| `../bin/dev compile --product groundwater` | `BLOCKED`（exit 1） | 最终入口在任何装配前返回 `PROJECT_TOOLCHAIN_NOT_READY` |
| `../bin/dev check --help` | `BLOCKED`（exit 1） | 同一锁定工具链前置条件 |
| `../bin/dev clean --dry-run` | `BLOCKED`（exit 1） | 同一锁定工具链前置条件；没有删除文件 |
| `../bin/dev operator-smoke --operator-id timeseries.gap_runs --operator-version 1.0.0` | `BLOCKED`（exit 1） | 公共入口按 M0-01 合同先返回 `PROJECT_TOOLCHAIN_NOT_READY`；没有静默改用宿主解释器 |
| `../bin/dev workflow-golden --case monitoring-trend` | `BLOCKED`（exit 1） | 公共入口同样先返回 `PROJECT_TOOLCHAIN_NOT_READY`；没有创建运行目录或回退到宿主解释器 |
| `../bin/dev agent-replay --case all` | `BLOCKED`（exit 1） | 公共入口同样先返回 `PROJECT_TOOLCHAIN_NOT_READY`；没有回退到当前 `.venv` |
| `../bin/dev capability-inventory --verify` | `BLOCKED`（exit 1） | 公共入口同样先返回 `PROJECT_TOOLCHAIN_NOT_READY`；没有绕过锁定工具链 |
| `.venv/bin/python scripts/development/dev.py operator-smoke --operator-id timeseries.gap_runs --operator-version 1.0.0` | `PASS`（内部验收） | 子执行环境为锁定 CPython 3.12.8、uv 0.8.14、offline hash-pinned Native wheelhouse；summary SHA-256 为 `bd83c4005b6de5cf90ccffc5f3fa33e5a04e8c2a3f5c1dd551a5f79d3ca837bd` |
| 上一命令增加 `--parameter 'frequency="week"'` | `FAILED`（exit 2） | 稳定 JSON 错误码 `OPERATOR_PARAMETERS_INVALID`；没有发布运行目录 |
| `.venv/bin/python scripts/development/dev.py workflow-golden --case monitoring-trend --runtime-image source-groundwater-executor-native-worker:latest` | `PASS`（内部验收） | 重复运行 `differences=[]`；summary SHA-256 为 `74d77d7c691c71c69e2eafee0dac76593153839fc59d99444c37888202640716` |
| 上一命令使用 `--case render-map` | `PASS`（内部验收） | 重复运行 `differences=[]`；summary SHA-256 为 `b759ffa08771f427ccafb4a58db90b4031c7cd975ada670bf26adb66c0ff01c0` |
| 上一命令使用 `--case flow-field` | `PASS`（内部验收） | 重复运行 `differences=[]`；summary SHA-256 为 `048d98517f40ceb9574cb82e963cc82e833f35e92b85f0d158e59255dff36e4c` |
| `.venv/bin/python scripts/development/dev.py agent-replay --case all --repetitions 3` | `PASS`（内部验收） | 六个 case 各三轮 snapshot identity 一致，所有逐项不变量通过；summary SHA-256 为 `8b6332d11e67bfcbccab96dc8552bcba8788d94ce1f3c70f81025c17cba57b93` |
| `.venv/bin/python -m pytest -q tests/cli/test_dev_agent_replay.py tests/cli/test_agent_replay.py` | `PASS` | 6 passed；覆盖默认三轮、六场景计数/不变量、未知 case 和 repetition 边界 |
| `.venv/bin/python scripts/development/dev.py capability-inventory` | `UPDATED`（内部验收） | 从产品选择的领域包、109 条当前 catalog index 和 Product UI 生成参考表；输出 SHA-256 为 `a8b38fcc34a7133d12eb614ba42e9a60b812055f6343a81a461a016da4c75399` |
| 上一命令增加 `--verify` | `PASS`（内部验收） | 重新读取并经 Assembler 校验全部源声明，生成结果与签入表逐字节一致 |
| `.venv/bin/python -m pytest -q tests/cli/test_dev_operator_smoke.py tests/cli/test_dependency_materializer_compatible_scope.py packages/operator-pack/tests/test_operator_quick_run.py` | `PASS` | 8 passed / 1 Native-domain skip；当前 root `.venv` 缺 `numexpr`，真实执行由上一条锁定临时 Native 运行覆盖 |
| `PYTHONPATH=packages/operator-pack/src python3 -m pytest -q packages/operator-pack/tests` | `PASS`（宿主诊断） | 158 passed；宿主 Python 3.13 回归，不是锁定 Native gate |
| `python3 -m pytest -q tests/cli/test_gw.py` | `PASS` | 8 passed；覆盖 bootstrap、依赖命令、拒绝码和锁定解释器重执行 |

修复前的诊断运行曾让 `../bin/dev compile --product groundwater` 由宿主 Python 3.13 成功写出
`38ee4719fc0a13cc98b833d89f3ee00a5c779a9e1012a3e2ecae3c442a5b3f1e` assembly；同一路径下的
`dev check --scope assembler` 曾完成 118 个 Assembler 测试，随后停在既有、未修改的
`scripts/checks/check_migration_rehearsal.py:10` Ruff `F401`。这些结果暴露了宿主解释器静默回退，
因此不能作为锁定工具链或当前入口的通过证据；当前实现已改为 fail closed。

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
cache 在命令结束时删除。本次成功证据位于
`/home/mtd/.cache/groundwater-v6/operator-runs/2ac6164ebf7e4ee384dc3006575004fc/`；其中
`runtime.defaultRuntimeMutation=NONE`。当前公共入口仍受缺失 root 工具链阻断，上述直接 Python 调用
仅用于本卡内部验收，不是推荐的日常旁路。

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

三个 case 已在同一锁定 CPython 3.12.8 Native 环境和同一只读系统工具 image 下重复通过。image 的
实测 identity 为 `sha256:80dc16ddf62b3e20eb51ad4280e4531e3f28e884c30698d5a9b47fb476105e64`，
平台为 Linux/amd64，精确 `gdal-bin` 版本为 `3.8.4+dfsg-3ubuntu3`。CLI 只接受项目专属 rootless
daemon 中已存在的 image，使用 `--pull never`、`--network none`、read-only rootfs、drop all
capabilities 和 no-new-privileges；它不挂载 Docker socket，也不把 image 中的 Python 或源码当成
执行环境。`monitoring-trend` 与 `render-map` 的宿主和容器 snapshot 逐字节相同；`flow-field` 在没有
GDAL CLI 的宿主上明确拒绝，在该 image 中生成稳定的 GeoTIFF/Parquet identity。三条重复运行分别
验证 12 个连续缺测段/60 个缺失 period、3 个制图 feature，以及 4 个有效流场栅格 cell。

`--output-dir` 必须是 `$XDG_CACHE_HOME/groundwater-v6/workflow-golden-runs/` 下尚不存在的目录。
成功目录包含逐节点 replay request、runtime result、输入/输出、副本 snapshot 与 summary；失败或
golden mismatch 仍发布可诊断证据，但返回非零。当前公共入口仍受缺失 root 工具链阻断，上述直接
Python 结果只用于 M0-03 内部验收，不是完整 Native、Compose 或发布门禁通过。

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
在原子发布前删除。本次内部验收目录为
`/home/mtd/.cache/groundwater-v6/agent-replay-runs/cd1ae7844a4f426692ae64159aed8740/`，
六个 case 均为 `PASS` 且三轮 identity 稳定。公共入口仍因 root 工具链未物化而 fail closed；该离线
回放也不验证真实 DeepSeek/MiniMax 服务质量、网络协议兼容性、Compose 或浏览器体验。

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

本次生成结果为 10 个模型、10 个 workflow、109 个 catalog 算子，其中 43 个唯一算子被当前 workflow
使用；Product UI 有 11 个示例、其中 7 个 featured。参考表 SHA-256 为
`a8b38fcc34a7133d12eb614ba42e9a60b812055f6343a81a461a016da4c75399`。来源身份分别为 domain
package `sha256:e5e8702c14a8ac0298b91f88399faa91154c107ccc7fc283fba34fffbf21eb09`、catalog manifest
`sha256:2ca1c058884bc6d5bb17a71fd19aaa46bd44307a2c953edd7c67b6895ed5d901`、Assembler execution
catalog `sha256:31dd7cd9dc989a3f8a800d52054c6ff11d97b590191e9907910c55290d4a3a91` 和 Product UI source
`sha256:1d8bc1e3db2182548965ad8c7398c642836a605b9b03855d25521d9d7a19849d`。两个 catalog digest
代表不同的规范化合同，并非漂移；生成器会分别验证。Pastas/MF6 独立 spike 和 `remote_http` 声明不被
冒充为默认部署或服务可用性。

## 输出位置

| 内容 | 当前解析位置 |
|---|---|
| 锁定开发工具链 | `/home/mtd/.cache/groundwater-v6/toolchains/v6-8e1c7fdd5ab99996` |
| uv cache | `$XDG_CACHE_HOME/groundwater-v6/uv` |
| 当前依赖 materialization | `/home/mtd/.cache/groundwater-v6/deps/sha256-e1f4836145ac83b0c7462c36f329a77e1283b3862615e96a29803e2f84ffa46a` |
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

## NOT_RUN

以下命令本次没有执行：

```text
../bin/bootstrap --sync
../bin/gw deps materialize --scope python
../bin/gw deps materialize --scope npm
../bin/gw deps materialize --scope system
../bin/dev check --scope all
python -m pytest -q
python -m scripts.checks.check_native_tests
npm test
npm run typecheck
npm run build
python -m scripts.operations.test_full_project --product groundwater
```

`bootstrap --sync` 和依赖 materialization 会修改默认开发/依赖缓存；当前任务明确禁止修改默认
Native/QGIS/root runtime，所以保持 `NOT_RUN`。由于锁定工具链尚未就绪，后续 `dev` 全量门禁也没有
运行，不能把本页的 CLI focused tests 或修复前宿主解释器结果解释为完整项目通过。
