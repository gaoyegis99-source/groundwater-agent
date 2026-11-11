# 开发与验收执行手册

## 快速反馈

```bash
../bin/bootstrap --status
../bin/bootstrap --sync
../bin/dev compile --product groundwater
../bin/dev operator-smoke --operator-id timeseries.gap_runs --operator-version 1.0.0
../bin/dev workflow-golden --case monitoring-trend
../bin/dev real-case --case all --source-root /home/mtd/liweigis/workflow \
  --runtime-image source-groundwater-executor-native-worker:latest
../bin/dev agent-replay --case all
../bin/dev capability-inventory --verify
../bin/dev interactive --product groundwater --dry-run
../bin/dev interactive --product groundwater
../bin/dev check --scope all
```

装配、Vite、Playwright、wheel 和 E2E 工作目录默认位于 `$XDG_CACHE_HOME/groundwater-v6/build/`，并按 linked worktree 摘要隔离。测试必须使用临时目录或该 cache，不得创建源码 `.generated`、`dist` 或 `build`。
`bootstrap --status` 是只读检查；`--sync` 才会联网并写入摘要绑定工具链。所有 `dev` 子命令都会先
验证该工具链并使用其中的锁定 Python，缺失时返回 `PROJECT_TOOLCHAIN_NOT_READY`。当前 checkout 的
逐命令实测与 `NOT_RUN` 清单见 [Vibecoding 本地快速参考](../development/vibecoding-quick-reference.md)。

需要连续开发时使用[交互式增量开发模式](../development/interactive-incremental-mode.md)。它在源码树外生成开发 Compose 覆盖和状态，前端使用宿主 Vite HMR，Python API 使用显式 `/workspace/.../src` 监视目录，worker 只做受影响服务重启；默认 Native/QGIS/root runtime 和产品装配不承载控制器代码。

单算子反馈入口复用签入的 catalog smoke 与生产 task 子进程。它在 XDG build 临时目录中以锁定
CPython 和 hash-pinned wheelhouse 离线构造 Native 环境，命令结束即删除；成功的 replay、摘要、
artifact 与 SHA-256 只写入 `$XDG_CACHE_HOME/groundwater-v6/operator-runs/`。fixture 可用重复的
`--fixture PORT=FIXTURE_ID` 覆盖，参数可用重复的 `--parameter NAME=JSON` 覆盖；非法 schema 参数
返回稳定错误码且不发布半成品目录。

Workflow 级反馈入口复用现有 compiler 和同一生产 task 子进程，按稳定拓扑运行签入 case，并将
workflow/catalog/fixture/output identity 与关键值同签入 snapshot 比较。差异以 JSON Pointer
定位到具体字段或数值，证据写入 `$XDG_CACHE_HOME/groundwater-v6/workflow-golden-runs/`。需要
GDAL 的 case 可显式传入 `--runtime-image <local-image>`；CLI 只使用项目 rootless daemon 中已验证的
本地 Linux/amd64 image，以 no-network、read-only、no-pull 边界提供系统工具，不使用其中的 Python
或源码，也不修改默认 root/Native/QGIS runtime。完整命令、当前三条重复 PASS 和 NOT_RUN 边界见
[Vibecoding 本地快速参考](../development/vibecoding-quick-reference.md)。

真实数据案例入口使用产品开发目录中的签入清单，允许读取调用者显式给出的本地数据根，并且只从
清单固定的官方 HTTPS URL 下载网络数据。下载缓存、准备数据、运行结果和逐节点 checksum 全部写入
`$XDG_CACHE_HOME/groundwater-v6/real-cases/`；源码树、默认装配和 Native/QGIS/root runtime 不承载
这些状态。入口重新验证 size、MD5（清单声明时）和 SHA-256，安全解包后才调用真实 workflow compiler
与生产 task 子进程。`--runtime-image` 必须是项目 rootless daemon 中已验证的本地 Linux/amd64 Native
镜像；运行时 no-network、read-only、no-pull。`--case all` 运行完整案例集，单案例 ID 用于快速复核。
当前数据选择、质量边界、外部核验结果和可复现运行证据见仓库级
`plans/REAL_DATA_VALIDATION_2026-11-11.md`。

Agent 级反馈入口复用真实 Kernel 状态机，以签入 `ScriptedModel` 步骤和进程内 `MockTransport` 回放
六个关键边界；默认每个 case 重复三轮，核对逐项不变量、Confirmation/WorkflowRun 计数和稳定
snapshot identity。运行证据原子写入 `$XDG_CACHE_HOME/groundwater-v6/agent-replay-runs/`。该入口完全
离线，不把脚本结果冒充真实 Provider 准确率，也不替代 Compose、网络协议或浏览器验收。完整 case、
命令、当前重复 PASS 与公共工具链 fail-closed 边界见
[Vibecoding 本地快速参考](../development/vibecoding-quick-reference.md)。

当前功能清单入口从产品、domain manifest、Operator Catalog index 和 Product UI 的真实声明重算
[开发参考表](../development/current-capability-inventory.md)。`--verify` 要求动态生成内容与签入文件逐字节
一致，因此新增模型、workflow、算子、UI 示例或 viewer 后，不需要也不允许维护另一套手写数量。该清单
描述声明与使用关系，不代表 `remote_http` 服务可达，也不把 Pastas/MF6 独立 spike 纳入默认 runtime。

## Per-change checks

按改动范围运行 `../bin/dev check --scope <scope>`；不确定影响范围时使用 `--scope all`。该入口按组件拥有者定义的检查拓扑运行，不允许以手工挑选的单测替代适用检查。

`--scope all` 将 Python 套件拆为 foundation、Kernel、Data、Knowledge、Executor、跨服务合同和产品装配七个互不重叠的切片，并分别报告结果与显式 skip 原因；同时运行主界面、Data 界面和 Knowledge 界面的测试、类型检查与构建。切片清单由 `scripts/checks/v6_test_suite.py` 单点维护，架构测试会核对根 Pytest 收集范围内的每个测试文件恰好属于一个切片，避免新增组件或产品测试被静默漏掉。Native 套件始终使用 `toolchain.lock.json` 指定的精确 CPython 版本创建离线环境，不继承调用入口的宿主 Python ABI。

## 锁与静态门禁

```bash
uv lock --check
npm ci --ignore-scripts --offline
../bin/gw deps verify --scope python
../bin/gw deps verify --scope npm
python -m scripts.checks.check_core_imports
python -m scripts.checks.check_worktree_hygiene
python -m scripts.maintenance.check_internal_layout --scope source
bash scripts/checks/check-boundaries.sh
git diff --check
```

`npm ci` 只验证/安装根 workspace lock，不得在子项目生成 lock。正式发布前还必须验证 images/system scope。

## 测试

```bash
python -m pytest -q
python -m scripts.checks.check_native_tests
npm test
npm run typecheck
npm run build
python -m scripts.operations.test_full_project --product groundwater
```

根 `pytest.ini` 的普通 Python 套件不收集 Operator Pack 测试，因为它们需要独立 Native 锁域。
`check_native_tests` 先验证已物化的 Python wheelhouse，再用锁定的 Native/build wheel 离线创建
XDG 临时环境、安装本地 wheel、检查依赖，并运行 Operator Pack 与 Executor 子进程合同测试；
pytest 取自独立物化的测试 wheelhouse，不在检查时联网，也不进入运行镜像。
`../bin/dev check --scope executor|all` 和完整项目报告都包含这道独立门禁；缺少 wheelhouse
时明确 `NOT_RUN`，不能把普通套件通过当作 Native 覆盖。

完整项目报告默认写入 XDG state 的 validation audit，并同时生成机器可读 JSON 与同名中文 Markdown；可用 `--output /absolute/path` 显式覆盖 JSON 位置。Markdown 只汇总阶段、计数、失败分类和结论边界，不复制命令 payload。隔离 Compose 检查只通过 `../bin/docker`，使用随机 project/network 和临时数据，不能接触活动实例。

轻量 Agent 包另有独立、可安装交付门禁；它不依赖 v11 服务或前端构建：

```bash
cd packages/lightweight-agent-core
uv run python scripts/verify_developer_delivery.py
```

该入口通过 `verification` 依赖组运行包内 Ruff/import-linter/pytest、检查 `uv.lock`，
核对 `verification-profile.json` 中的必需套件；缺依赖、跳过/xfail、收集缺口或 teardown
失败均不通过。它从同一份正向输入清单捕获源码副本用于检查和 wheel 构建，缓存不进入摘要，
匹配清单的未跟踪源码仍进入摘要。`--skip-*` 返回非零 `incomplete`，不能当成开发通过。
随后安装干净 wheel 的 `[workflow]`，执行真实本地工具、SQLite query、完整 local-recovery 后继续执行，
以及 `HostRuntime` 清理探针。JSON 报告记录 source/worktree 摘要、wheel/lock SHA-256、
Python/OS/extra 组合、执行范围、跳过和未评估项。真实模型请求和 Playwright 浏览器
trace 必须显式启用；未运行时保留未评估，不能由离线测试冒充。可用
`--archive-artifacts /new/absolute/directory` 保存 wheel、锁、来源摘要与当前 wheel 真实
生成的审批等待、工具已完成恢复金样，并生成独立的
`compatibility-verification.json` 与 `compatibility-matrix.json`。当前生产者只能得到
`BASELINE_ESTABLISHED`，不是历史版本兼容证据。下一版本必须在自己的干净环境中运行：

```bash
python scripts/delivery_baseline/verify_compatibility.py \
  --baseline /immutable/producer-baseline \
  --output /tmp/compatibility-verification.json \
  --matrix-output /tmp/compatibility-matrix.json \
  --consumer-source-sha256 sha256:... \
  --consumer-wheel /path/to/consumer.whl
```

只有流程定义解析、原审批身份续接、工具完成态零重放都通过，才能形成该生产者/消费者
版本对的运行记录证据。消费者按冻结 manifest 明确拒绝时记录
`INCOMPATIBLE_SAFE_REJECTION`，与 verifier 自身崩溃/异常分开；驱动在途续接仍按精确
driver binding 单独判断，禁止跨驱动翻译。

## 归档验收

日常变更检查不等于可归档。`experiment` 归档允许科学和工程状态为
`PASS`、`FAIL` 或 `NOT_ASSESSED`，以便失败实验仍可复现；`benchmark` 归档必须同时满足
scientific 与 engineering `PASS`。Agent 体验状态单独记录，不阻塞数值结果归档。

归档验证必须核对源码快照、dirty patch、依赖锁、装配配置、输入输出 checksum、参数、
随机种子、实现摘要、lineage 和验收报告。Compose smoke、快照恢复和干净环境重建没有执行时，
保持 `NOT_ASSESSED`，不得用单元测试替代。签名与加密可选，不属于归档前置条件。
