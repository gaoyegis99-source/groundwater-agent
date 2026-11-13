# 任务进度

状态：TODO / IN_PROGRESS / PASS / FAIL / BLOCKED / NOT_RUN / ALREADY_FIXED_VERIFIED

| ID | 状态 | 本地 HEAD | 修改文件 | 测试结果 | 说明 |
|---|---|---|---|---|---|
| SYS-01 | PASS | `cc83e93` | `03_LOCAL_BASELINE.md`、进度、台账 | 工具链、依赖、帮助、功能清单、实例状态和差异检查均退出 0 | 本地产品装配与当前实例基线见 `03_LOCAL_BASELINE.md`；行为测试 NOT_RUN |
| SYS-02 | PASS | `cc83e93` | `architecture-contract.md`、`architecture-structure-and-flow.md`、`reference/contracts.md`、进度、台账 | 产品 compile、contracts（187 passed）、assembler（119 tests collected，scope 退出 0）、边界、Ruff、`git diff --check` 均 PASS | 编译产物 `6631e5afb705…` 与本地实例报告的装配一致；四个运行时 OpenAPI 快照、公开合同、生成 TS 与 Python SDK 一致。历史 Pytest 临时目录清理警告已登记。 |
| SYS-03 | PASS | `cc83e93` | 进度、台账 | 离线 Agent 回放 6 × 3；Web 聚焦 64 passed；文档检查、`git diff --check` 退出 0 | 复核本地基线标签历史源码审计、差距矩阵和算子分析；未证实问题保留 UNKNOWN，领域科学验收 NOT_RUN |
| SYS-04 | PASS | `cc83e93` | `interaction/revisions.py`、`test_confirmation_recovery.py`、进度、台账 | 修复前 2 failed / 1 passed；修复后聚焦 23 passed，Kernel scope 870 passed，mypy、Ruff、边界、差异检查 PASS；离线 Agent 回放 6 × 3 PASS | 修订保留先持久化的确认决定。真实 Provider 与浏览器长任务恢复本卡 NOT_RUN |
| SYS-05 | PASS | `cc83e93` | `agent_runtime/context_projection.py`、`test_context_compiler.py`、进度、台账 | 修复前 1 failed；修复后上下文、路由、回复和草稿聚焦 173 passed，Kernel scope 871 passed，mypy、Ruff、边界、差异检查 PASS；离线回放 6 × 3 PASS | Task 以 Data 精确版本关联成果；已有跨轮、工具参数、旧证据、确认前停止和草稿回归通过。真实 Provider NOT_RUN |
| SYS-06 | PASS | `cc83e93` | `web/src/api.ts`、`api-polling.test.ts`、Data `test_service.py`、进度、台账 | 取消测试修复前 1 failed / 2 passed，修复后 3 passed；Data scope 95 passed / 2 deselected，新增后端聚焦 2 passed；Web 289 passed、typecheck/build PASS，Data UI 25 passed；差异检查 PASS | 取消已受理查询；验证目录搜索游标与精确版本、相同 sort 值稳定跨页。2 个 service_image 用例本卡 NOT_RUN，待镜像验收。 |
| SYS-07 | PASS | `cc83e93` | `agent_runtime/evidence.py`、`tooling/replies.py`、`test_internal_modules.py`、进度、台账 | 修复前 2 failed；Knowledge scope 98 passed；Kernel 聚焦 162 passed、scope 873 passed；引用离线回放 3 × PASS；类型、边界、差异检查 PASS | 使用现有本地 pgvector 镜像创建隔离测试库；STALE 只影响请求的精确身份，已有权限、回执和当前轮全文引用回归通过 |
| SYS-08 | PASS | `cc83e93` | Workflow Compiler `graph.py`、`test_compiler.py`、进度、台账 | 3 个重复声明指针用例先 FAIL 后 PASS；编译器与 Kernel 草稿 120 passed、产品 compile、Ruff、差异检查 PASS；额外 monitoring-trend golden FAIL | 单端口基数、artifact 输入、具名 value/artifact 输出、fan-out/fan-in、环与不可达节点合同已验证；golden 的目录基线差异转 SYS-14 |
| SYS-09 | PASS | `cc83e93` | Executor `results.py`、`test_execution_boundaries.py`、进度、台账 | 修复前 1 failed / 2 passed；Executor 174 passed、关联聚焦 74 passed、Native 160 passed / 38 warnings；Ruff、边界、差异检查 PASS | 过期的 FINALIZING 任务恢复时优先撤销发布；已提交回执仍按既有逻辑恢复。Native warnings 为 Rasterio/Affine 待弃用提示。 |
| SYS-10 | PASS | `cc83e93` | `ConfirmationEditor.tsx`、对应测试、Kernel `revisions.py` 与确认回归、进度、台账 | Web 修复前 2 failed / 7 passed，修复后聚焦 9 passed、Web scope 290 passed、typecheck/build PASS；Kernel 修复前 1 failed / 3 passed，确认聚焦 24 passed；差异检查 PASS | 使用真实 query/cursor 合同；51 项可浏览和搜索，翻页保持选择。编辑其他参数遇到冻结引用漂移返回现有冲突。 |
| SYS-11 | PASS | `cc83e93` | `WorkflowDraftGraph.tsx`、`WorkflowDraftCard.tsx`、对应测试、`style.css`、进度、台账 | 图中具名输出和缺项端口用例先 2 failed；最终 Web 290 passed、typecheck/build PASS，Kernel 草稿 55 passed，差异检查 PASS | 复用只读 DAG。每个具名输出连到来源端口，缺项显示具体节点/端口。 |
| SYS-12 | PASS | `cc83e93` | `SpatialViewers.tsx`、`ChartView.tsx`、`raster-probe.ts` 与对应测试、进度、台账 | 修复前 5 failed / 10 passed，校正 Float32 栅格夹具后同缺陷单独复现；修复后 Web 294 passed、typecheck/build PASS，最终查看器聚焦 16 passed；差异检查 PASS | 150,002 个 GeoJSON 测试要素；轴类型、稳定查询排序、空值、截断、Float32 NoData 与 EPSG:32650 旋转仿射探针已验证 |
| SYS-13 | PASS | `cc83e93` | 成果卡片、Workspace、Database Workbench、API、对应 Web/浏览器测试、进度、台账 | 修复前 5 个目标断言失败；修复后 Web 297 passed、续接聚焦 14 passed、Data/Kernel/Executor 聚焦 38 passed、生产浏览器 2 passed；类型、构建、差异检查 PASS | 预览和比较冻结对象/历史语义，比较列出血缘且可读取后续版本；跨会话复用保持完整引用且不发送消息 |
| SYS-14 | PASS | `cc83e93` | 三份 Workflow golden、回复质量固定身份与 CLI 回归、Executor 基准入口、Data/Chromium 性能场景、验收矩阵、进度、台账 | `dev check --scope all` 复跑退出 0；三组隔离验收、三个 Workflow golden、Agent 回放 6 × 3、依赖和性能基准均 PASS。4 个独立领域试验 SKIP | 原始日志在 XDG state `audit/system-engineering-cc83e93-wGpvi9DY/` |

### SYS-04 状态矩阵

| 场景 | 应保持的持久状态 | 回归入口 |
|---|---|---|
| 普通对话 / 缺参 / 执行提案 | 对话与缺参无 WorkflowRun；提案停在 Confirmation | `agent-replay` 的 `dialogue-no-execution`、`missing-field-correction`、`execution-proposal-confirmation` |
| 修订 / 决定并发 | 修订可重放；已有 REQUESTED 决定时保留原确认和决定，拒绝替换 | `test_confirmation_recovery.py` |
| 拒绝 / 重启 | 拒绝恢复无 Job；已批准批次按冻结身份恢复；原失败记录保留 | `test_interaction_recovery.py`、`test_confirmation_recovery.py` |
| 重复消息 / 并发 | 相同请求重用持久记录，改 payload 冲突，同一会话只认一个并发 claim | `test_unified_messages.py`、`test_kernel_concurrency.py` |
| Provider 失败 / SSE | 失败不重放 Provider 调用；SSE 从游标续接，heartbeat 不持久化 | `test_provider_availability.py`、`test_v11_contract.py`、`test_chat_resume.py` |

## SYS-14 系统工程验收报告

验收基线为 `cc83e9339c165eb424271d4ef46db93cc32ca8cc` 与本次工作区差异，装配 `6631e5afb7050cd4e23fedc51344a73e2b1e5bbd75c67a044547f7fe441511ac`。开始前的交互式开发改动已在 `03_LOCAL_BASELINE.md` 登记。原始命令日志、退出码、性能数据及浏览器证据索引位于本机 `~/.local/state/groundwater-v6/audit/system-engineering-cc83e93-wGpvi9DY/`。

14 张任务卡均通过本阶段工程验收。未解决项和领域验收范围见下表。验收使用隔离实例，修改保存在本地工作区。

### Bug 修复

| 范围 | 已验证的修复 |
|---|---|
| Kernel | 确认修订遵守先持久化的批准或拒绝；未改动的冻结输入遇到语义变化会明确冲突；上下文按精确数据版本保留当前任务成果；错误 checksum 的 STALE 证据不会撤掉另一个 checksum 的正确全文 |
| Workflow / Executor | 重复 DAG 声明返回可定位的 JSON Pointer；已过期的 FINALIZING 任务恢复时先撤销发布；修复本地 Executor I/O 基准，使其测量当前执行路径 |
| Data / Web | 已受理的查询在客户端取消后收到取消请求；确认选择器支持搜索和游标分页；草稿图显示实际端口与具名输出 |
| 查看器与成果 | 修复大 GeoJSON 极值、图表轴/排序/空值/分页提示、小数 Float32 NoData；成果预览与比较读取冻结对象和历史语义，比较展示血缘，非终态成果无下载入口 |
| 工程门禁 | 校准三个金样的算子目录摘要；能力清单测试从 Manifest 选择 UI 配置；回复质量工具以当前固定领域身份和模型集核验装配 |

逐项失败前证据、复现条件与修复后回归见 [缺陷台账](DEFECT_LEDGER.md)。

### 测试与构建

| 命令 | 状态与证据 |
|---|---|
| `../bin/dev check --scope all` | 复跑退出 0，见 `check-all-final.log` 与 `check-all-final.exit`。首跑退出 1，平台切片 607 passed、3 failed、4 skipped；失败为本轮台账退役路径引用及两个旧产品身份断言。修正后，台账架构聚焦 10 passed，CLI 聚焦 19 passed |
| 全量复跑 Python 切片与 Worker wheel | 共享协议与编译器 136 passed，Kernel 874，Data 98（2 deselected，已在专用镜像验收执行），Knowledge 98，Executor 174，跨服务合同 187，平台/CLI/产品 610（4 skipped），Native 160（38 条 Rasterio/Affine 待弃用警告）。Kernel mypy 检查 9 个文件通过 |
| 全量复跑领域试验跳过项 | SKIP 4。MODFLOW 6.8.1 发布输入未提供 1 项，独立 FloPy 环境未激活 1 项，独立 Pastas 环境未激活 2 项 |
| 全量复跑前端与生成合同 | 主 Web 297、Data UI 25、Knowledge UI 18 项单元测试通过，三套 typecheck 与生产 build 均 PASS。运行时 OpenAPI、TypeScript 生成合同与 Python SDK 生成合同均 PASS |
| 全量复跑生产浏览器与隔离 Compose | 生产 Chromium 17 passed，见 `production-browser-results.json`；Compose Chromium 16 passed，六个模型生命周期、Kernel 任务重启、Executor Control 重启、Knowledge 111 份文档引导与重复装配、故障隔离及清理均通过 |
| 全量复跑静态检查 | Ruff、`git diff --check`、核心导入、架构边界与文档检查全部通过 |
| `python -m scripts.checks.check_data_knowledge_e2e --output <证据目录>/data-knowledge-e2e.json` | 退出 0，Data 100 passed（含 2 个 service_image），Knowledge 98 passed，两套 UI、Gateway 合同、跨服务语义绑定、Knowledge 备份恢复及隔离清理 PASS |
| `python -m scripts.checks.check_compose_e2e --product groundwater --interaction-only ...` | 退出 0，已安装 SDK/TLI 与 Web 三端 3 passed、0 failed、0 skipped。正常执行、回执丢失去重、真实超时和恢复的证据见 `interaction/*/gate.json` |
| `../bin/dev workflow-golden --case <case>` | 三个 case 均退出 0、`differences=[]`。`render-map`、`flow-field` 使用本地 Native image。只更新全局目录摘要，三个原快照中的工作流定义、输入输出与数值保持同一证据 |
| `../bin/dev agent-replay --case all` | 退出 0，固定模型 6 场景各 3 次，逐场景快照身份稳定。见 `agent-replay.json` |
| `../bin/gw deps verify --scope {python,npm,system}` 与 `uv lock --check --offline --no-python-downloads` | 三个依赖 scope 和锁校验均退出 0 |

### 性能基线

测量环境为本机 Linux 5.15.0-139-generic、Intel Xeon E5-2667 v4、32 个可见逻辑 CPU、CPython 3.12.8、Node 22.23.1。测试期间观测到主机 I/O stall，以下为本机带负载样本。

| 场景 | 样本与核验 | 实测 | 证据 |
|---|---|---|---|
| Data 查询 | 100,000 行 CSV（1,200,016 字节），按版本和语义提交，50,000 行匹配、10,000 行持久化，第二页保持排序和完整覆盖回执 | 发布 4,460.02 ms；提交、DuckDB Worker 和首读共 2,589.96 ms；第二页 12.88 ms | `data-query-perf.log` |
| Chromium GeoJSON | 150,000 点（16,952,731 字节），实际构建的 OpenLayers 渲染出画布与完整极值，零页面异常 | Chromium 145.0.7632.6，从选择数据集到画布与图例可见 8,181 ms | `geojson-perf.log` |
| Executor I/O 64 MiB | 两个并行租约，4 个心跳采样，恢复后核对已提交检查点 SHA-256，未发生非预期租约丢失 | claim 22,964.60 ms；complete 15,424.23 ms；心跳 p95 19,429.11 ms | `executor-io.json` |
| Executor I/O 1 GiB | 两个并行租约，81 个心跳采样，恢复后核对已提交检查点 SHA-256，未发生非预期租约丢失 | claim 9,413.64 ms；complete 53,515.62 ms；心跳 p95 9,734.25 ms | `executor-io.json` |
| Executor I/O 复测 | 64 MiB 和 1 GiB 再各通过一次，分别取得 10 和 87 个心跳样本，非预期租约丢失均为 0 | 64 MiB 的 claim / complete / 心跳 p95 分别为 276.82 / 5,101.89 / 4,695.87 ms；1 GiB 为 15,991.64 / 92,992.07 / 4,972.58 ms | `executor-io-repeat.json`、`executor-io-repeat.pressure.txt` |
| SSE 长任务与跨端恢复 | 暂停隔离 Native Worker 触发实际超时，浏览器打开 SSE 3 次、收到 12 条事件，恢复结果 2 份下载均通过校验 | 首次 TLI follow 62.872 s；原运行 TIMED_OUT，61,988 ms；新确认后的恢复运行 SUCCEEDED，6,677 ms | `interaction/*/gate.json`、`cross-interface-recovery.json` |

复测入口包括 `pytest -q -s services/data/tests/test_service.py -k large_table_query`、`npm --prefix web run test:e2e:build -- build-viewers.spec.ts -g '150,000 features'` 与 `python scripts/evaluation/benchmark_executor_io.py --config <当前装配>/executor-config.json --output <源码树外>/executor-io.json`。

### 未解决问题与验收边界

表中状态取自当时的阶段验收。后续复核见本文件后续记录及缺陷台账。

| 项目 | 状态 | 记录 |
|---|---|---|
| 新选输入的浏览到保存语义竞争 | UNKNOWN，公共合同项暂停 | `SYS10-03`。当前请求提交版本 ID，Kernel 保存时重新冻结完整引用。要锁定浏览时的语义，须同步设计修订 Schema、现有客户端兼容和校验错误 |
| 查询提交回执丢失 | UNKNOWN | `SYS06-01`。客户端已知 query ID 时发出取消；回执丢失时的远端状态未在本阶段复现 |
| 显式顺序草稿中的中间输出 | UNKNOWN | 历史 `AG-03`。fan-out 回放通过，显式顺序检查仍要求具名输出来自 sink，需专项验证中间输出请求 |
| 历史 Pytest 临时目录清理 | UNKNOWN（形成条件） | `SYS02-02`。默认临时目录的 CLI 聚焦复跑再次记录既有 Toil 目录清理警告，19 项通过；全量复跑使用独立临时目录 |
| Data / Knowledge 生成校验器分块 | UNKNOWN（性能影响） | `SYS14-05`。两套构建均报告 573.80 kB 分块，gzip 后 42.86 kB；独立解析和弱网首屏耗时未测 |
| 真实 Provider、真实外部计算和地下水科学验收 | NOT_RUN | 本阶段使用本地固定 Provider、隔离服务和合成数据执行工程验收；科学结果与真实服务质量属于独立验收 |

### 第二阶段可用的工程基础

- 领域开发可以通过现有 Product Manifest 和带 checksum 的 Domain Package 装配模型。Workflow v6 支持具名输入、分支与汇合、多个 artifact/value 输出。单输入端口基数以及 artifact 绑定规则以编译器合同为准。
- 科学验证可用 Data 的版本、语义、对象、checksum 和输入血缘构造可复现样本。Knowledge 引用可以回到本轮已完整读取的不可变证据。
- 研究者可以先查看只读 DAG 和缺项，再在新确认批次中冻结执行输入。Executor 保存节点状态、检查点、重试信息及原子发布回执。成果可按精确身份预览、下载、比较和附加到新草稿。
- 阶段二仍需针对每项地下水方法独立验证科学适用性、输入质量和数值结果。

### 修改文件清单

本阶段修改 52 个已跟踪文件。相对仓库根的完整清单位于证据目录 `changed-files.txt`，已排除开始前的 12 个已跟踪修改和 3 个未跟踪文件。逐卡文件归属见上方进度表。


## 2026-11-12 多批次集成复核

基线为 `cc83e93` 加当前工作区。复核开始时保留了 81 个已有跟踪文件改动和 13 个未跟踪文件。对历史阶段验收与当前集成重新取证，原阶段记录继续作为当时证据。

- 交互式开发的两类失败传递、启动期文件变化、停机队列回归均先失败，再通过修复后的定向检查。
- 监控测试归入 CLI/平台切片。交互控制器与监控聚焦 24 项通过，Ruff 和差异检查通过。
- 全量范围首次预检因没有设置独立 Knowledge 测试库而为 NOT_RUN，已用当前实例采用的本地镜像启动临时测试库，独立复跑。原始日志、起始清单和退出码保存在 XDG state `audit/multi-batch-integration-i2uxq1/`。
- 修复 WebUI 标签页共享登录 Cookie 却缺少写入令牌的问题。两项单测先失败后通过。生产构建 Chromium 双标签页实测会话创建 201。本轮未部署 WebUI。
- 生产浏览器门禁在空构建目录复现 preview 等待超时，调整为先构建再启动 preview 后，同一入口在新的空目录通过两个定向场景。
- 隔离 Compose 的已安装 SDK、TLI 与 Web 三端通过 3 项。包括执行交接、确认回执丢失去重、Worker 实际超时后的恢复。该批镜像在 WebUI 跨标签页修复之前构建，跨标签页修复另用生产浏览器定向验证。
- 冷启动生产 Chromium 全量 18 项通过。150,000 点 GeoJSON 从选择到图例与画布可见约 5,276 ms。
- 复核 SQLite 读取副作用。停机快照探测改用临时副本，活动归档改由核对过挂载的 Kernel 容器读证据。快照与归档 5 项通过，独立 rootless 容器验证数据库与辅助文件 UID 一致。
- 联合 Data/Knowledge 首跑暴露搜索竞态。延迟 Data 响应时，Knowledge 数据集列表无法保持和呈现新结果。已修复并连续通过 3 次定向生产浏览器回归，重新运行联合门禁。
- 固定 Provider 复现了明确要求中间和最终两个输出仍被草稿终点校验拒绝的问题。按 `SYS08-03` 补正向回归并修复。草稿聚焦 56 项通过，双输出草稿不创建运行。
- `dev check --scope all` 已退出 0。包括共享协议/编译器 136、Kernel 879、Data 98、Knowledge 98、Executor 174、跨服务合同 187、平台切片 620、Native 160、主 Web 299、Data UI 25、Knowledge UI 18。平台切片 4 项独立领域试验按实际结果记录 SKIP。全量采集启动后加入的维护归档与 Knowledge 竞态修复单独复跑。
- 后续审计继续保留 Data 查询提交回执丢失、确认新选数据的语义漂移以及生成校验器的弱网性能。真实 Provider 和科学计算另行验收，本轮未复跑。
- Data/Knowledge 联合复跑退出 0。后端分别 100 和 98 项通过，Data 浏览器 9 项模拟和 1 项真实链路，Knowledge 浏览器 11 项模拟和 4 项真实链路。精确语义绑定与备份恢复通过，隔离资源已清理。
- `monitoring-trend` Workflow 金样通过，差异为空。固定 Provider 离线回放首跑在本机 I/O 压力期间触发请求期限，按 FAIL 留证。复跑 6 个场景各 3 轮，全部通过。
- 最新 Kernel 范围复跑 880 项通过，mypy、Ruff、核心导入、架构边界与差异检查通过。此前全量运行未覆盖的归档、交互控制器、监控最终组合另行通过 30 项定向回归。


## Data 查询回执与加载性能复核

证据保存在 XDG state `audit/query-receipt-BqxQFf/`。

### Data 查询回执恢复

`SYS06-01` 的主 Web 和 Data 独立工作台在首次回执不明时使用同一请求体和幂等键补发一次。主 Web 已关闭视图时立即结束等待，拿回查询 ID 后补发取消。连续两次无法读取回执时报告 `QUERY_SUBMISSION_UNCERTAIN`。浏览器在隔离 Gateway 把首个 Data 202 交给服务后主动丢弃回执，两次真实 HTTP 提交返回同一查询 ID。Data 服务端取消契约、Data 浏览器模拟 9 项及真实 1 项通过。第一次联合运行随后因 Knowledge 绑定选择器超时失败，隔离资源清理通过。`data-knowledge-final.json` 记录各阶段的独立状态。

### 确认新选输入的合同影响

特征测试已复现 `SYS10-03`。浏览读取语义 A 后，将 Data 当前指针切到 B，再向现行修订端点发送同一版本 ID。保存返回 201，修订冻结 B，状态仍为 `PENDING`，运行列表为空。

兼容方案已落实在 v12 修订 action 的可选 `expectedInputReferences`。Web 在选择时保留引用，后续分页及搜索不会替换它。Kernel 核对目标数据输入字段及 Data 返回的完整身份，变化时沿用 `CONFIRMATION_DATA_VERSION_INVALID`，保留原待确认批次。省略该字段的旧客户端沿用原有行为与幂等指纹。新 Web 需要先部署支持该字段的服务端。成功保存会创建新的 `PENDING` 批次，批准前运行列表为空。

`SYS10-06` 同时复现自有私有数据导致的 409。Kernel 改用当前请求已鉴权的主体和角色读取 Data。测试核对非 ASCII 显示名的编码和解码，并验证没有运行被创建。

### Knowledge 精确版本选择

`SYS07-03` 在首次联合验收出现。版本下拉框在同一次鼠标操作中展开又关闭，诊断已确认加载完成后也可出现。现用原生选择控件与独立版本搜索，加载期明确等待，继续支持游标分页和选择后核对精确语义。Chromium 延迟列表的聚焦回归连续 3 次通过。

### 首屏校验与弱网

在 Chromium 145、本机夹具、冷缓存、150 ms 延迟、1.6 Mbps 下载和 4 倍 CPU 降速下，每套工作台各测量两次。无压缩时到工作台标题可见，Data 均值从 6.65 秒降至 4.39 秒，Knowledge 从 9.24 秒降至 5.50 秒。Data 校验器从 573,808 字节降至 60,328 字节。完整校验器留给首屏以外的操作。生成合同检查、本地生产 Data 9 项和 Knowledge 11 项浏览器测试通过。原始样本见 `validator-performance-before.json`、`validator-performance.json`。

### 联合复跑与三端证据

- Data/Knowledge 联合复跑退出 0，后端 100 和 98 项通过。Data 浏览器模拟 9 项、真实 1 项，Knowledge 模拟 11 项、真实 4 项。Gateway、精确绑定、备份恢复和隔离清理通过。真实回执恢复再次确认两次提交返回同一查询 `34fa9fb14da046bf9fab180106af91a8`。见 `data-knowledge-rerun.json`。
- 隔离三端最终 7 项通过，逐步日志 658 条。直接 HTTP 实际写入私有合成 CSV 的新语义，WebUI 提交旧选择得到预期 409，原批次仍待批准且无运行。重新选择后返回 201，TLI 读取到新批次待批准、运行列表为空。另一个场景由 TLI 批准修订后的新批次并核对只执行一次。初次扩展实测的夹具和分组操作问题均保留失败证据。见 `three-interface-verified/interaction-log.md`、`interaction-events.jsonl`、`run-summary.json` 及同目录截图。已核对测试前后工作区指纹及隔离资源。
- 主 Web 305 项、合同 187 项、语义修订后的 Kernel scope 885 项通过。私有数据身份转发修改后，确认及会话聚焦 18 项通过。最新运行时 OpenAPI、生成合同、核心导入、架构边界、Ruff、文档与差异检查通过。
- `SYS02-02` 的同一用例在默认临时目录出现 58 条历史 Toil 清理告警，独立 `--basetemp` 无告警，两次均通过。历史文件 UID 与 rootless Docker 的宿主映射一致，目录清理和生成链路仍待处理。见 `pytest-historical-ownership.txt` 及两次用例日志。

## 多批次验收续跑与证据隔离

证据位于 XDG state `audit/multi-batch-checkpoint-PLOAAQ/`。

- 常规 `dev check` 和完整项目验收为每个 Pytest 调用生成独立临时目录，正常结束、失败或中断均由调用上下文清理。显式 `--basetemp` 仍由调用者管理。常规装配复跑 119 项通过，输出无历史 Toil 清理告警。已有历史目录保留。
- `--resume` 核验 Git HEAD 和工作区内容指纹。已跟踪文件和未跟踪源文件即使 Git 短状态相同，内容改动也会拒绝复用。报告检查点与收尾检测源码漂移。复用旧源码报告的真实 CLI 返回 2，报告校验和不变。
- 联合验收和最终 Compose 的证据按报告运行和阶段尝试分别写入。已通过阶段续跑保留原路径，强制重跑使用新的路径。模拟失败覆盖了尚未写出证据时的缺失状态，核对不会拾取上次输出。
- 确认修订的 OpenAPI 更新导致装配金样失配。修复前聚焦断言复现，比较两份实际 `assembly-lock.json` 后确认只变动 `/modules/kernel/httpContracts/kernel-http-v12`，其余六个生成文件一致。只更新装配锁金样摘要，保留七项比对。
- `full-verified.json` 的 14 个阶段全部通过。Kernel 886 项，跨服务合同 187 项，平台与产品 632 项通过、4 项独立领域试验跳过。Native 160 项、主 Web 单测 305 项、最终 Compose 浏览器 16 项通过，13 个 wheel 完成离线构建与安装校验。Data／Knowledge 联合验收后端分别 100 和 98 项通过。核对 9 份证据快照的 SHA-256，并验证隔离资源和七个 Pytest 临时目录已清理。
- 使用同一源码和原报告副本执行真实续跑，复用 14 个已通过阶段，阶段记录和证据内容校验和一致。全量验收后补入清理错误处理，注入 `PermissionError` 的聚焦回归确认阶段与总报告均为 FAIL，原用例计数保留。最新聚焦 49 项以及 Ruff、文档、工作区检查和 `git diff --check` 通过。

### 节点诊断的合同影响

现有真实 Worker 超时回执中，任务面板拿到 `TIMED_OUT` 和 `JOB_DEADLINE_EXCEEDED`。公开 WorkflowRun 和会话执行任务投影缺少节点字段。Executor 已有 `executor_get_job_nodes` 内部查询，保留按 `jobId` 定位的节点事实。证据见 `node-visibility-characterization.json`。

可沿用 WorkflowRun 的归属校验，使用该运行冻结的 `remoteJobId` 读取 Executor 已有诊断，再增加明确字段的只读公共投影。旧 `JobStatusV6` 采用严格字段集合，保持原合同可避免旧客户端拒绝响应。内部查询读取完整行，公共投影应只传节点标识、算子、状态、时间与经过约束的错误码。租约、参数、绑定、远端状态和日志内容不经过该投影。节点尝试次数应读取 `node_attempts`，不能用 `lost_count` 替代。WebUI 根据所选运行加载节点详情，需验证跨运行切换、重试、授权和旧运行缺少远端记录的行为。

### 节点诊断验收

- `SYS09-02` 已实现。Web 按 WorkflowRun 展开节点详情，Kernel 先核对项目和运行归属，再查询该运行冻结的 Executor 作业。覆盖错误码约束、无远端作业、绑定不一致、旧 Executor 缺少尝试计数、重试和关闭详情后丢弃迟到回执。旧 `JobStatusV6` 合同保持不变。
- 隔离三端交互 3 项通过。浏览器通过直接 HTTP 核对原运行 `2fa14236-4159-4f06-babd-a16779100f22` 的 `describe` 节点为 `CANCELLED`、尝试 0 次、错误码 `JOB_DEADLINE_EXCEEDED`。重新批准后的运行 `041bc967-b159-4c46-8333-e0c5aa3c9efd` 的同名节点为 `SUCCEEDED`、尝试 1 次，旧运行诊断保持一致。核对隔离容器、网络和卷均已清理。
- 运行时 OpenAPI、公开合同与 TypeScript／Python 生成合同检查通过，合同用例 187 项通过。装配锁只变更 Kernel HTTP 合同摘要。静态检查、文档与工作区规则通过。真实交互镜像构建于 Kernel 路由 DTO 导入修正前，两版的节点接口响应和 OpenAPI 一致。
- 原始失败与复跑日志、`gate.json`、`node-diagnostics.json` 和浏览器截图保存在 XDG state `audit/workflow-node-diagnostics-BQAcRF/`。
- 最终源码的 Kernel 权限与兼容探针 2 项通过。Web 范围首次并行运行出现一项上传对话切换测试失败，定向复跑 14 项通过，完整 Web 范围随后 306 项、类型检查及构建通过。

### 成果比较的语义一致性

- `SYS13-03` 的失败回归让版本列表记录语义 A，随后详情返回同一版本的新语义 B。修复后比较使用列表选中的语义 A，并沿用 Data 已有的精确历史语义读取，校验返回的版本和语义身份。成果侧继续使用该次发布冻结的语义与对象。
- 主 Web 306 项通过，类型检查与构建通过。生产 Chromium 4 项通过，其中成果比较使用可控 HTTP 回执复现语义变化。Data 服务真实存储的历史语义与对象绑定用例 1 项通过。日志、回执和截图见 XDG state `audit/workflow-node-diagnostics-BQAcRF/comparison/`。
- 同一工作区复跑 Knowledge 引用相关的 Kernel 套件，39 项通过。覆盖本轮完整读取、精确身份绑定、缺少可信读取时拒绝引用及草稿引用。此结果仅验证引用规则，历史 `KNOW-01` 的完整工作台交互仍需另验。

### Knowledge 引用回原文

- `SYS07-04` 的失败回归复现同名证据无法按段落定位。多来源回答按已持久化的 `sourceIndexes` 显示对应证据编号，单来源保留折叠入口。选择后沿用现有精确版本、单元、checksum 读取。组件测试覆盖定位信息、必需上下文及读取失败后的重试。
- 隔离 Compose 的 WebUI 门禁 16 项通过。固定 Provider 对合成知识提问后，Chromium 用键盘打开第 2 条引用，真实 Knowledge 回执的原文与页面一致。退出阅读后焦点回到原引用，刷新后仍能定位。直接 HTTP 改错一个 checksum 字符得到 `STALE` 且无正文。该对话没有创建确认或 WorkflowRun。所用演示文档无必需上下文声明，因此浏览器实测覆盖精确原文，必需上下文和来源定位由组件夹具覆盖。
- 主 Web 307 项通过，类型检查与构建通过。独立生产构建的 Chromium 可访问性和会话回归 11 项通过。回执、截图和测试输出见 XDG state `audit/knowledge-citation-skpUau/`。

### 字段画像精度的合同影响

`SYS06-03` 用 Arrow `int64` 复现。原始画像 JSON 中的 `9007199254740992` 和 `9007199254740993` 不同，Node 对该响应执行 JSON 解码后两值相同，样例和值域均受影响。证据见 XDG state `audit/data-field-profile-QySCQ9/unsafe-integer-*.json`。

已发布的版本和对象身份需要保留。若在响应中补充精确十进制文本，应保留现有数字字段，并覆盖新生成及已保存画像的读取行为。需核实严格客户端对新增字段的接受程度，以及新前端连接旧服务时如何提示精度不可证实。当前尚未修改该公共表示。

### 字段画像入库与显示

- `SYS06-02` 先用分批 Arrow 扫描复现 4,500 位数字文本触发整数转换上限。修复后按 Decimal 核对 int64 范围。真实 Parquet 发布包含超长数字与前导零，精确版本保留非空计数、类型推断证据及 160 字符样例。同次发布的数值列全量范围包含第六行的极值，前五个样例包含一个单列计数的非有限数。Data 后端范围 99 项通过，2 项镜像测试从该范围排除。扩展后的画像聚焦 4 项通过。
- 主数据库工作台读取版本已有画像，显示全量行数、空值与非空值、非有限数、值域和有界样例，字段单位取同一版本语义。独立 Data 工作台将原来的“有效值”改为“非空值”，历史行数缺失时显示未统计。两个前端复用画像响应校验。主 Web 308 项、独立 Data 单测 26 项及生产 Chromium 9 项通过，构建和运行时 OpenAPI 检查通过。
- 隔离首跑把 CSV 水位列误设为数值，实际保留为字符串。第二次已核对同一 CSV 版本和语义、两个页面的 `m` 单位、`81.2` 与 `82.4` 字符样例及全量覆盖，并保存了两个截图。后续直接 HTTP 核实旧播种脚本将 CSV 的规范化查询对象误当成独立 Parquet 夹具。已改为按标题播种和选择原生 Parquet。各次失败与复跑证据独立保存在 XDG state `audit/data-field-profile-QySCQ9/`。
- 最终隔离 WebUI 门禁 16 项通过，无跳过。直接 HTTP、主工作台和独立 Data 工作台的 CSV 版本及语义一致，原生 Parquet 值域为 `81.2–82.4`。验收回执 `field-profile.json`、两个截图与 `compose-accepted/results.json` 已保存。所有本轮隔离容器、网络和卷已清理。

### 大整数画像精度修复

- `SYS06-03` 在 Data 读取投影增加可选的 `exactIntegers`，仅为 `int64` 和 `uint64` 超出 JavaScript 安全整数范围的样例和值域端点补十进制文本。原 JSON 数值保持原样，样例索引对齐。两个 Web 按精确文本显示，旧服务缺少文本时将对应整数标注为精度不可确认。
- 后端 API 与两套 Web 回归修复前失败。Data 画像聚焦复跑 4 项通过，核对有符号及无符号 64 位极值、普通安全整数和非整数列。PostgreSQL 的 `versions.descriptor` 仍保存原始整数，精确文本在目录或工具读取时生成。主 Web 308 项、独立 Data 单测 28 项及生产 Chromium 9 项通过。失败和复跑证据见 XDG state `audit/field-profile-precision-gpL9VK/`。
- Data 后端范围 99 项通过，2 项镜像测试排除。运行时 OpenAPI、TypeScript 与 Python SDK 生成校验及合同 187 项通过。隔离浏览器首轮在等待重复选择触发 HTTP 响应时超时。改为独立读取精确版本并核对界面版本后，在同一隔离实例定向通过。回执记录浏览器解析后旧数值相同，两个工作台用补充文本显示了两个不同的原值。
- 新隔离实例完整 WebUI 门禁 16 项通过，无跳过。`integer-precision.json` 核对版本 `1c5c4ef0de384023a74cd1d7f7ff59cc` 的样例及值域两端，两个浏览器截图和 `compose-accepted/results.json` 已归档。两个隔离项目的容器、网络和卷均清理完毕。

### 成果刷新、复用与逐格式核验

- `SYS13-04` 的两项失败回归分别覆盖页面刷新和新会话附件预览。成果选择按用户与会话保留完整引用，工作台结果入口也传递精确引用。打开或核验待发送、已附加、已准备及冻结资料时，复用已有的历史语义与对象核验。生产构建 Chromium 延迟成果列表，刷新后的 GeoTIFF 仍显示发布时语义及像元物理值。
- `SYS13-05` 在隔离真实 JSON 成果链接复现建议名 `summary`。现按格式补充命名下载扩展名，已有相符扩展名的输出继续沿用。生产 Chromium 的 12 个成果格式夹具逐项核对预览或安全元数据、下载文件名／字节数／SHA-256、待发送附件引用和版本比较的语义／对象／血缘。夹具格式集合与本地产品模型声明一致，未提交消息。夹具的表格查询和 GeoPackage 预览使用可控 HTTP 回执，下载使用同源测试服务器的实际字节。
- 主 Web 309 项通过，类型和构建通过。失败回归、生产浏览器及逐格式回执位于 XDG state `audit/result-format-verification-kMGJtr/`。逐格式真实算子发布属于独立运行验证。
- 使用登录 Cookie 的隔离交互门禁 3 项通过，覆盖浏览器原生 JSON 下载、TLI 与 SDK 下载的字节数和 SHA-256，以及回执丢失去重与真实 Worker 超时恢复。带测试 Bearer 请求头的浏览器原生下载尝试发生取消，WebUI 门禁改用直接 HTTP 核对字节，并保留浏览器链接属性断言。续接预览的断言限定为版本详情，因为同页目录会同时显示当前语义。
- 最终隔离 WebUI 16 项通过，无跳过。真实 JSON 成果发布后写入新语义，原会话附加和新会话草稿继续保留发布引用，新会话预览读取原语义。HTTP 核对下载字节和 SHA-256，新会话未发送消息且未创建 WorkflowRun。证据见 `published-result-reuse.json` 与 `continued-exact-result.png`。

## 多批次工程收尾验收

证据位于 XDG state `audit/current-full-validation-5bCX4X/`。全量工程验收 `ebe025ed6cb4` 的 13 个阶段通过，最后隔离 Compose 阶段为 FAIL，退出码 1。六个模型的执行与成果发布通过。浏览器 13 项通过、1 项失败、2 项因同组串行规则跳过。首轮报告 `full.json`、`full.md` 和证据快照保留原始结果。不同标题的夹具与精确版本选择已落地，完整隔离复跑见 `final-compose-rerun/`。

- `SYS14-14` 与 `SYS14-15` 已合入。CLI 聚焦 38 项通过。现有实例状态按运行装配核对了 111 份文档的初始化回执，清理预演保留该装配并继续识别闲置装配。核对过程仅查询现有实例和生成清理预演。见 `followup-cli.log`、`integrated-deployment-status.json` 和 `clean-live-verification.json`。
- 历史表中的 Data 首个回执丢失和显式顺序草稿中间输出已按 `SYS06-01` 和 `SYS08-03` 验证。旧 Toil 共享临时目录保留，常规检查使用独立目录。见 `reconciled-defect-evidence.json`。
- 合入后装配范围 119 项通过，Ruff、核心导入、架构边界、文档和工作区检查通过。用独立 Git 索引还原两个夹具的旧版本后，验证除两份 CLI 文件、两份测试夹具和三份文档外，源码指纹与首轮一致。见 `integrated-assembler-scope.log` 与 `followup-source-verification.json`。
- 首次修复后复跑在浏览器前结束。固定 Provider 的歧义场景在批准后返回 `JOB_DEADLINE_EXCEEDED`，该轮字段画像浏览器测试为 NOT_RUN。保留 `final-compose-rerun/compose.log` 及 Executor 诊断，第二次隔离复跑见 `final-compose-retry/`。

### 隔离复跑验收

`final-compose-retry/compose.exit` 为 0。六个固定 Provider 场景、六个真实模型的执行与发布、重启恢复和 16 项 Chromium 回归均通过。2 行 CSV 画像和 Parquet 精确大整数由两套工作台分别核对，保留四张截图和版本回执。原全量报告继续保留最后一阶段 FAIL，独立复跑的 PASS 与首轮前 13 个 PASS 共同覆盖工程门禁。两轮失败及最终复跑的隔离资源均已清理，见 `final-compose-verification.json`。

| 范围 | 工程证据 |
|---|---|
| Python | 共享协议与编译器 136、Kernel 886、Data 99、Knowledge 98、Executor 174、跨服务合同 187、平台与产品 633、原生算子 160 项通过。SKIP 4，分别为 1 项 MF6 发布输入缺失、1 项独立 FloPy 环境未激活、2 项独立 Pastas 环境未激活 |
| Web | 主 Web／Data UI／Knowledge UI 单测 309／28／18 项，生产浏览器 19／9／11 项通过。三套类型检查和生产构建 PASS。Data／Knowledge 联合后端 101／98 项通过 |
| 装配与发布 | 当前产品装配、Ruff、Kernel mypy、公开和内部合同、生成类型、13 个离线 wheel 及安装校验通过。最终差异见 `final-worktree-files.txt`，证据指纹见 `followup-source-verification.json` |
| 三端与工作流 | 独立核对 7 个场景、658 条事件。修订仍待批准、TLI 批准后只运行一次，直接 HTTP 与 TLI 下载一致。固定 Provider 离线回放 6 场景各 3 次，三个 Workflow 金样差异为零 |
| 性能 | 150,000 点 GeoJSON 从选择到图例及画布可见 5,160 ms。100,000 行 Data 样本发布 2,999.79 ms，提交到首读 970.80 ms，第二页 11.61 ms。Executor 并发和恢复证据见本报告性能基线 |

`SYS14-17` 的历史超时保留，复跑未再出现，原因仍待核验。复跑中同一作业的 Kernel 终态、Executor 终态、节点和成果发布 ID 已留存。真实 Provider 质量、逐格式真实算子发布和科学有效性本轮为 NOT_RUN。研究实例上待部署项见台账。监控跨网段客户端验收仍缺访问源地址和具体错误。

### Kernel 路由领取恢复复核

- 11 月 12 日 16:16:58 Kernel 首次返回未就绪。16:30 的现场检查显示 UID 10001 可写主数据库，WAL 和 SHM 属于容器内 UID 0 且对 Kernel 不可写。授予写权限后登录恢复。16:36:24 受理的请求仍在队列等待，16:42:04 Kernel 重启后领取，等待 340.532 秒。领取后的路由处理约 1.25 秒。历史日志未保留调度任务退出栈，也未确认最初创建辅助文件的命令。
- `SYS04-02` 的回归先使用真实 SQLite 复现领取事务抛出 `SQLITE_READONLY`，异常经过子任务回收终止调度。修复仅对已回滚的本地调度事务等待可写恢复，数据库恢复后继续领取。未知调度异常仍终止任务，并立即记录异常栈。
- 隔离 FastAPI、临时 SQLite 和固定 Provider 复测。消息返回 202，故障期间公开存活检查为 200、内部就绪为 503、同一请求保持待领取且无 Provider 调用。恢复写权限后未重启服务，内部就绪回到 200，原请求路由、Provider 和回复各执行一次。另以当前 Kernel 镜像启动无网络临时容器，复现 WAL/SHM 属主不一致导致的只读错误，并验证恢复属主后可写。173 项相关回归、Kernel 类型检查、Ruff、架构和差异检查通过。证据见 XDG state `audit/kernel-routing-store-22iBP6/`。研究实例未部署本次修改。

### 研究实例分批发布

- 2026-11-13 04:11 CST 单独重启 Kernel，使用挂载的修复源码。先在容器内备份活动 SQLite，备份完整性检查通过，重启前后的既有记录清单一致。Gateway 上的合成请求返回 202，SSE 观察到受理、路由开始、路由结束和 Agent 完成。入队到领取 18 ms，路由尝试一次，无 WorkflowRun。临时研究身份已禁用。证据见 XDG state `audit/research-rollout-MycB4C/kernel-smoke.json`。
- 04:13 CST 依次重启 Data 服务、Data Worker 和 Executor Control，重启前后 Data 记录清单一致。研究实例用合成 Parquet 核对 4,500 位数字文本、160 字符样例、两个不同的大整数精确文本及下载 checksum。首个验收脚本把值域字段写为 `min/max`，实际合同为 `minimum/maximum`，更正后重用同一版本验证通过。
- 04:16 CST 启用离线构建的新 Gateway 镜像，仅重建 Gateway，保留原镜像用于回退。匿名访问独立工作台得到 401，随后用临时身份确认主 Web、Data、Knowledge 均返回 200。Chromium、TLI 和直接 HTTP 以同一研究员身份验收私有观测数据修订。保存返回 201，批准前无运行，TLI 批准后执行一次并核对两份成果校验和。验收后禁用临时身份，原令牌验证返回 401。证据见 `audit/research-rollout-MycB4C/`。
- 研究员使用真实登录页面登录后在第二标签页新建会话成功。两套工作台通过同一不可变版本读取合成 Parquet，核对全量 2 行和两个精确整数。节点诊断通过直接 HTTP 和 Web 展开验证。首轮独立 Data 测试通过目录按钮定位超时，复跑使用工作台的精确版本入口通过。见 `audit/research-rollout-verification-7VgBQx/` 的浏览器回执与截图。
- `SYS14-17` 保持开放。对同一个成功合成运行，从 Kernel WorkflowRun 关联 Executor 作业与 Data 发布。节点完成到发布 3,356 ms，发布到 Executor 终态 30 ms，Executor 终态到 Kernel 回写 985 ms。历史失败缺少同一运行的发布与回写时间，成功对照用于定位后续取证。见 `audit/research-rollout-MycB4C/successful-publication-timeline.json`。
- 研究实例验证无分组统计提案停在待批准，运行列表为空。真实 Provider 返回四条知识来源，从段落打开第 2 条引用，回执与原文一致。确认修订中选中的合成数据语义变更后，首次保存返回预期 409，重新选择保存返回 201，仍需批准。见 `audit/research-rollout-results-final-gdCDxB/` 与 `audit/research-rollout-semantics-xqIr23/`。
- `SYS13-05` 的真实下载揭示 Data 响应头覆盖浏览器链接建议名。补回归先复现对象 ID 文件名，随后让 Data 为已发布的工作流原对象按不可变发布回执命名。成果比较、刷新与复用继续等待真实下载修复后的连贯验收。

- 04:31 CST 仅重启 Data 服务以启用下载响应头修复。Data 100 项回归通过，2 个镜像用例未包含在本次切片。运行时 OpenAPI、Ruff、文档和差异检查通过。04:32 CST 研究实例 Chromium 原生下载 `summary.json`，大小和 SHA-256 与发布回执一致。确认后只创建一次运行。把合成成果当前语义改为新值后，刷新与新会话复用仍显示原语义和对象；新会话无消息和运行。比较列表加载后再改变右侧版本当前语义，界面仍按列表所选的语义比较。13 项待部署修复均已上线并验收，`SYS14-17` 按计划继续保持开放。见 `audit/research-rollout-results-live-KdXAnl/`。

### SYS14-17 已批准作业超时复核

- 隔离 Compose 使用原固定 Provider 和原歧义用例。批准前运行、作业及成果计数均为 0。批准后仅生成运行 `862c4b08-642d-4635-b4e7-8f061d019ad5` 和 Executor 作业 `058a2179-5995-40ae-b5f4-8d46b8f5084a`。Data 发布 `7cbc64aea54f41e8a358aa8146f2ce6c`，成果可读且校验和通过。Executor 与 Kernel 均为 `SUCCEEDED`，Executor 完成时距截止还有 55.509 秒。
- 按三服务持久记录核对，`describe` 节点完成到 Data 发布 3,520.66 ms，发布到 Executor 终态 26.34 ms，Executor 终态到 Kernel 回写 759 ms。本轮未复现批准后的超时，历史失败缺少同一运行的发布和回写时间，根因保留 UNKNOWN。
- 外置取证脚本首次解析 PostgreSQL 的多行 JSON 失败，已在隔离环境清理前独立补读。两个改名的追加用例在批准前以 `AGENT_MODEL_OUTPUT_INVALID` 退出，未生成运行。其余五个固定 Provider 用例 PASS，隔离入口整体退出 1。隔离容器、网络和卷的清理已验证。原始回执、失败对照和复核见 XDG state `audit/sys14-17-xk4sMY/`。
