# UI-13 生命周期验收

基线 `f0b9f6d`。保留开始时的工作区修改，未访问 GitHub，未改 Kernel、Executor 或 Data 公共合同。

## 验收证据

生产构建 Chromium 覆盖界面交互与 API 边界，使用合同兼容测试响应。相关 Kernel/Data/Executor 用例单独验证持久化、权限、运行和发布。隔离 Compose 验收使用真实 Gateway、Kernel、Data、Executor 与固定测试 Provider。真实外部 Provider 语言质量保持 NOT_RUN。

| 场景 | 权威证据与边界 |
|---|---|
| 01 普通咨询 | 隔离 Compose Task Collaboration 的 `casual_no_task` 和 `metadata_no_execution` 断言。 |
| 02 创建持续研究 | Task Collaboration 的 `explicit_goal_and_review` 和 `additive_revision`，加生产浏览器主任务卡恢复。 |
| 03 澄清提交 | 隔离 WebUI 在 390×844 显示水位高程和井口埋深两项。点选埋深后，真实 Kernel 将选项原文作为第二轮 AUTO 用户消息持久化，刷新后保留解释。两轮均无 Confirmation 和 Run。外部 Provider 的自然语言质量另计。 |
| 04 上传和身份 | 隔离 WebUI 上传、附件、刷新、checksum 和字段质量；Data Profile 回归。 |
| 05 局部阻塞 | 生产浏览器使用 READY/BLOCKED 合同响应。现有写入路径每次单模型准备替换 head，确认边界仅记录纳入批次的 READY 条目。同轮独立时序可用、空间受阻的跨服务验收待 G18。 |
| 06 方法草稿 | 隔离 WebUI 验证通过真实算子目录及 Knowledge 形成水位描述统计草稿，默认折叠依赖图，可查正文和导出持久 payload。缺少输入时保留缺项，草稿不产生执行授权。 |
| 07 修订 | 隔离 Interaction Gate 验证真实冻结确认修订及旧确认失效。隔离 WebUI 独立验证重新编排后两份草稿有各自 ID，旧稿保持原样，刷新后两份均可读取。 |
| 08 批准后运行 | 隔离 Interaction Gate 核对批准前无运行、确认后真实运行以及重复审批隔离。 |
| 09 节点与刷新 | 隔离 Interaction Recovery 读取超时节点、恢复尝试和刷新；节点图大规模布局由组件测试覆盖。 |
| 10 执行时追加 | 隔离 Interaction 暂停隔离原生 Worker 保留执行窗口，核验消息仍在本地队列、当前 Turn 不变，恢复 Worker 后追加要求仅提交一次，原 Run 成功且确认批次 action 快照不变。 |
| 11 停止竞争 | 隔离浏览器验证两种顺序。停止先到达服务端时，已派发的 Run 最终取消且无下载。延迟浏览器 POST 让发布先完成时，Run 保持成功且两个成果仍可下载。两条路径各自核对真实 202 回执、持久控制记录和按请求 ID 回放的 SSE。已受理停止与并发发布提交的内部竞争另由 Kernel/Executor 测试覆盖。 |
| 12 失败恢复 | 隔离 Interaction Recovery 的真实作业超时、续接、新批次审批和成果下载。 |
| 13 成功后成果尚未同步 | 生产浏览器及 Selector 测试覆盖不同读取时点。现有 Executor 在 Data 提交后报告成功，成功且 Data 尚未发布不是当前合同的服务端组合（G06）。 |
| 14 多 Run 归属 | 隔离 WebUI 对同一 Task 分别确认两次真实运行，将各自的 AgentRun/WorkflowRun 登记为不同交付条件的证据。两次运行均发布同名汇总。归档刷新后核对各运行卡片的精确下载地址、对象 SHA-256，确认两份汇总没有串入对方卡片。隔离 Interaction Recovery 另覆盖恢复尝试。未登记的完整 Task↔Run 归属受 G03 限制。 |
| 15 基于成果新建会话 | 生产浏览器验证精确附件以及空新会话，隔离 WebUI 核对真实旧语义版本。 |
| 16 断线刷新 | 隔离 Interaction Gate 验证已受理回执丢失及 SSE 续接，Task Collaboration 验证 Kernel 重启。 |
| 17 会话切换 | 隔离 Interaction 延迟真实 Kernel HTTP 历史快照，切换后释放，旧待确认批次未进入新会话。通过原生 SSE 捕获真实 `confirmation.rejected`，在切换后交付原事件，当前界面和事件日志均未接收旧会话状态。 |
| 18 评审归档 | 隔离 WebUI 在同一 Task 建立两个成果条件和一个评审条件，登记两次真实运行的证据，用户显式评审后归档。重开并刷新恢复同一已完成 Task、评审原文和四份成果引用，两份汇总对象均通过下载校验。 |

## 仍需后端合同支持

[G01–G18](../contracts/CONTRACT_GAPS.md) 继续跟踪独立研究目标、目录任务摘要及最近活动、精确 Task 归属、逐节点与逐条件证据、发布、科学验证和同轮多个准备候选等缺口。UI-03A 基础导航通过，完整目录投影仍待 G01。服务端重大合同变更需另行批准。

## 命令与产物

- `../bin/dev check --scope web` 从 `source/` 执行，退出 0。38 个文件、371 项通过，TypeScript、生产构建、Ruff 与 diff 检查通过。
- `../bin/dev check --scope contracts` 退出 0，187 项及 Runtime OpenAPI、生成 TypeScript 和 Python SDK 验证通过。
- `npm --prefix source/web run test:e2e:build` 退出 0，Chromium 145.0.7632.6，33 项通过，0 失败、跳过或 flaky。响应式包含 1440×900、1024×768、768×1024、390×844。Playwright JSON、截图和日志在 `~/.codex/visualizations/2026/11/13/` 的 `ui-06-data-preparation`、`ui-12-responsive` 和 `ui-13-acceptance` 目录。
- 隔离 Compose 首次在构建阶段发现当前依赖摘要未物化。使用仓库命令 `bin/gw deps materialize --scope {python,system,npm}` 分别补齐，三个范围均通过，现行锁文件保留。
- `python -m scripts.checks.check_compose_e2e --product groundwater --interaction-only --webui-report …` 重跑退出 0，隔离 Compose Gate PASS。三项用例验证 Web/SDK/TLI 对同一工作流的修订、丢失已受理审批回执后无重复执行、真实作业超时后的 SSE 续接和新批次恢复。Gateway、Kernel、Data、Knowledge 与 Executor 的健康检查通过，111 份知识文档导入和幂等重部署通过。
- 端到端场景表同时引用了组件、后端和测试响应证据。逐项贯穿真实服务的完整 18 条验收尚未完成，真实外部 Provider 验证为 NOT_RUN。UI-13 总体标记 PARTIAL，隔离 Compose 子门禁独立标记 PASS。

## 总目标停点

完整 UI-03 需要 G01 的目录投影。独立有效目标与完整目标历史需要 G02，精确任务运行归属需要 G03，通用科学验证需要 G08。当前已将公开合同支持的事实接入 UI，保留缺失字段的未知态。完整验收涉及公共合同设计和服务端变更，按总目标要求暂停这些变更并报告。

后续验收应在获批后先确定 Session 目录排序与游标、Task 修订、显式运行归属及同轮准备候选的兼容读取方案，再由领域负责人确定科学验证对象和标准版本。兼容要求和验收边界见[待审批的合同扩展](DECISIONS.md#待审批的合同扩展)。外部 Provider 的水位澄清与草稿质量待单独验证。任务条件的已登记 Run 证据通过验收，完整 Task↔Run 成员关系仍待 G03。

## 页面与交付索引

| 页面或交付 | 实现入口和已核验证据 |
|---|---|
| 首页与会话目录 | `App.tsx`、`SessionNavigation.tsx`、`MessageComposer.tsx`。生产浏览器验证模板、重排和基础会话操作，完整状态分组见 G01。 |
| 会话与轮次 | `TurnTimeline.tsx`、`ChatCards.tsx`、`use-session-stream.ts`、`use-message-actions.ts`。见[轮次恢复](UI-04_TURN_TIMELINE.md)及上表。 |
| Task 工作台 | `WorkspacePanel.tsx`、`TaskCard.tsx`、`ui-selectors.ts`。见[任务概览](UI-05A_TASK_OVERVIEW.md)、[交付条件](UI-05B_TASK_REVIEW.md)和[输入准备](UI-06_DATA_PREPARATION.md)。 |
| 方案与确认 | `WorkflowDraftCard.tsx`、`WorkflowDraftGraph.tsx`、`ConfirmationEditor.tsx`。见[方案](UI-07_WORKFLOW_DRAFT.md)与[确认](UI-08_CONFIRMATION_REVIEW.md)。 |
| 执行与恢复 | `WorkspacePanel.tsx`、`WorkflowNodeDetails.tsx`。见[执行身份和节点](UI-09_WORKFLOW_RUN.md)与[失败及恢复](UI-10_STOP_AND_RECOVERY.md)。 |
| 成果、资料与知识 | `ArtifactViewer.tsx`、`DatabaseWorkbench.tsx`、`Drawers.tsx`。见[成果评审](UI-11_RESULT_REVIEW.md)及隔离 WebUI 的实际目录、知识正文、发布对象和下载校验。 |
| 四对象、状态和视觉 | [字段矩阵](../contracts/CONTRACT_MATRIX.md)、[实体关系](../contracts/ENTITY_RELATIONSHIP_AUDIT.md)、[状态映射](../contracts/STATE_MAPPING.md)、[Selector](../contracts/UI_SELECTOR_DESIGN.md)、[视觉与响应式报告](UI-12_VISUAL_RESPONSIVE.md)。 |

## 补充验收

- 隔离 Compose `--tasks-only` 退出 0，固定测试 Provider 下通过 9 项断言，覆盖普通咨询无任务、无目标控制澄清、任务创建与增量修订、请求回放、Kernel 重启、证据验收及元数据操作无执行。证据保存在 `ui-13-acceptance/compose-task-collaboration.json`。
- 隔离 Compose `--webui-only` 的 Chromium 门禁通过 17 项，0 失败、跳过或 flaky。使用当前工作区构建的 Gateway 与实际 Kernel、Data、Knowledge 和 Executor，固定本地 Provider。真实业务覆盖包含澄清经普通消息持久化、上传后核对 checksum 与字段质量、冻结输入版本、发布下载与旧语义版本续研、拒绝、任务评审及两类归档恢复。5 项独立路由用例使用浏览器测试响应。证据在 `ui-13-webui-final/results.json`。
- 首次 WebUI 门禁 10 项通过、3 项失败、4 项因串行组失败而跳过。复跑前修正路由测试的立即断开 SSE、CSV 数值 profile 的过时预期，以及归档测试中尚未关闭的数据库对话框。
- `scripts.evaluation.evaluate_task_collaboration --quality-cases` 生成 `ui-13-acceptance/provider-task-quality.json`。缺少隔离外部 Provider 配置，权威状态为 `NOT_RUN / MISSING_MODEL_CONFIGURATION`。
- 本轮调整 `MessageQueue.tsx` 的关闭说明以反映本地持久队列。关闭页面暂停，重新打开会话后按既有条件续发。组件回归先失败后通过，当前工作区 38 个 Vitest 文件、373 项通过，TypeScript 和生产构建通过。重新执行 33 项生产 Chromium 回归，0 失败、跳过或 flaky。用例及门禁修改见 `v12-interaction-recovery.spec.ts`、`interaction_gate.py`，公开 API 和执行服务保持现有合同。
- 同一真实流场 Run 的生产浏览器专项通过。刷新前后分别取得 HTTP 200 的 graph 和 nodes，核对 5 个完成节点及 4 条边，并点开速度节点。首轮浏览器定位同时匹配节点与算子详情，收窄至节点标题后复跑通过。见 `multi-node-final.log`、`multi-node-browser.json`、`multi-node-service.json`、`screenshots/real-flow-field-nodes-after-reload.png`。
- 隔离 Compose `--interaction-only` 最终通过 5 项。新增真实 HTTP 快照延迟切换和执行时排队，原有真实超时恢复及审批回执用例继续通过。门禁证据在 `ui-13-queue-interaction/platform-groundwater-e2e-0a48b2a5e8ec/gate.json`，运行使用本地固定 Provider、隔离浏览器 Cookie 账户和已安装 SDK/TLI。
- 增补迟到 SSE 与停止两种顺序的测试。`ui-13-stop-interaction` 首跑 5 项通过、1 项失败。两次停止的页面终态和成果权限断言均通过，采集器在第二个分支累计了第一个分支的事件。后续改为按会话和停止请求 ID 从真实 SSE 端点回放，保留顺序断言。两条停止路径使用真实原生 Worker 与合成观测表；发布先完成的路径只延迟发往 Gateway 的浏览器停止请求。
- 本轮定向 Web Selector/进度 68 项、Kernel/Executor 停止相关 76 项、Kernel 工作流草稿 56 项通过。扩展后的 Playwright 测试文件通过独立 TypeScript 检查。公共合同门禁复跑通过，含 187 项测试、生成合同、Ruff 与 diff 检查。
- 停止和 SSE 复跑 6 项全部通过，0 失败、跳过或 flaky。证据在 `ui-13-stop-final/platform-groundwater-e2e-0d19fe624162/gate.json`，附两个停止分支、会话切换、冻结批次和恢复证据。Chromium 145.0.7632.6，1440×1000，固定本地 Provider。服务从当前工作区构建，Kernel 7.0.0、Executor 7.0.0、Data 3.0.0、Knowledge Next 2.0.0。
- 水位澄清新增固定 Provider 路由和回复合同测试，覆盖两个互斥选项及其后续回复。390×844 隔离浏览器通过澄清场景，`ui-13-water-level` 保存截图及持久消息证据。该次门禁尚未包含方法草稿 Provider，草稿断言失败。加入从真实算子目录和 Knowledge 读取证据的 fixture 后，`ui-13-clarification-draft` 已读到服务端完整草稿，浏览器测试在定位折叠区时失败，随后修正定位。
- 草稿无 payload 时的绿色成功徽标已修复。回归先失败再通过，Web 单测 373 项、生产 Chromium 33 项通过。公共合同门禁 189 项、Ruff 与 diff 检查通过。
- 使用锁定 Python 执行 `python -m scripts.checks.check_compose_e2e --product groundwater --webui-only --webui-report …/ui-13-clarification-draft-final`。Chromium 145.0.7632.6 全套 19 项通过，含 390×844 澄清和 1440×1000 草稿。固定本地 Provider 发出模型工具请求，Kernel 编译和持久化，Knowledge 提供已核对正文。证据含 Playwright JSON、澄清消息事实和草稿历史截图。
- 新增同一 Task 的成果评审与归档场景。执行收尾沿用 Kernel 的 `submit_reply`，成果登记在后续 Turn 中读取 `get_execution_fact` 并用 `update_task` 绑定精确运行。中间复跑已验证真实发布与交付条件绑定，并发现 Task 级成果聚合返回该运行的两份成果，条件级入口按所需成果名称展示。测试据此核验两份持久引用及汇总对象的校验和。固定 Provider 与 Kernel Task 回归 64 项、公共合同门禁 190 项通过。
- 隔离 WebUI `--webui-only` 在 `ui-13-task-archive-final` 全套通过 20 项，0 失败、跳过或 flaky。证据含原运行、已完成 Task、评审原文、精确成果身份和归档后截图。运行使用固定本地 Provider 和真实 Gateway、Kernel、Executor、Data 与 Knowledge。
- 扩展同一 Task 场景为两次独立确认和执行。通过公开 Task 证据与 Session Results 的精确运行关联检查同名成果，核对两份汇总的校验和及归档后运行卡片。固定 Provider 合同测试 25 项与 Ruff、TypeScript、diff 检查通过。新增证据在 `ui-13-task-two-runs`。

## 正式授权验收

本节为首次正式授权的判定。其时 G18 为 **BLOCKED_BACKEND**，场景 13 为 **BLOCKED_CONTRACT**，真实外部 Provider 门禁为 **FAIL**。18 个场景为 16 PASS、1 BLOCKED_BACKEND、1 BLOCKED_CONTRACT。当前结论见文末 G18 复验。

### 基线与取证

- HEAD `f0b9f6d6d5e1df970794d2cd16b9cdcec387b7fc`。初始 56 个跟踪文件已修改，11 项未跟踪。完整状态与跟踪 diff 指纹保存在 `ui-13-final/initial-worktree.txt` 和 `initial-tracked-diff.sha256`。保留既有工作树。
- 本机产品网关公开 `groundwater / product-ui.v1`。根项目 Docker 入口可见 Gateway、Kernel、Data、Executor 和 Knowledge 实例，Kernel/Data/Executor 容器包版本分别为 7.0.0/3.0.0/7.0.0。生产 Web 源版本为 2.0.0。Groundwater 装配域版本 6.1.0。验收请求使用新建隔离 Compose 实例，本机研究网关仅做只读探测。
- Node 22.23.1、npm 10.9.8、锁定 CPython 3.12.8、Compose 2.35.1。Chromium 测试视口包含 1440×900、1024×768、768×1024、390×844。隔离 WebUI 默认 1440×1000，个别用例切到移动端。
- 本轮原始证据根目录为 `~/.codex/visualizations/2026/11/13/01a1d279-d4f8-7513-b4a5-ce6e2a23c4de/ui-13-final/`。
- [逐项执行矩阵](../acceptance/UI-13_EXECUTION_MATRIX.md)记录前置条件、输入、服务、请求、持久化与浏览器证据。场景 09 有同一真实流场 Run 的浏览器刷新前后证据。场景 13 区分服务端发布与客户端同步。
- 后端改动限于测试。文件清单见本轮修改文件表。

### 后端修复优先级

1. **G18 和真实 Provider**。先按 ADR-UI-012 定位真实 Provider 的结构化输出、草稿和确认参数失败。**G18** 让准备检查按同一轮的候选身份独立保留。先确定候选身份和同候选更新规则，复用已有准备修订表作为历史来源。当前投影应同时呈现 READY、BLOCKED，确认时继续显式冻结纳入批次的 action。回归要求顺序无关、重放幂等、切换轮次隔离，且空间阻塞不阻止已确认的独立时序操作。
2. **G03、G02** 公开真实 Task 绑定和只读任务规格修订。绑定使用现有持久关系，历史缺值保持未知。目标变更的写入和重新审批规则需另行设计。
3. **G08** 由领域负责人确定验证标准和证据；服务端记录判定及其与 Run 或精确成果版本的绑定。页面继续使用未知态。
4. **G01、G05、G06、G13、G15** 明确目录聚合、发布关联及历史证据的权限、游标和兼容读取。客户端读取重试沿用当前合同。

### 真实服务集成与浏览器报告

- 从 `source/` 运行 `../bin/dev check --scope web`，退出 0，38 个文件、396 项测试、类型检查、生产构建和 Ruff。合同门禁 `TMPDIR=/dev/shm ../bin/dev check --scope contracts` 退出 0，通过 190 项，覆盖生成的 OpenAPI、TypeScript、Python SDK 和 Ruff。默认磁盘首次运行有 1 项 `OUTPUT_CONCURRENT_REPLACEMENT` 失败。
- 选定 Kernel、Data、Executor 集成测试在 tmpfs 上通过 219 项，覆盖会话历史与边界、任务及评审、确认恢复、成果与发布权限、运行控制及草稿。默认磁盘首次 218 通过、1 项慢文件 IO 写锁测试失败。另有 101 项边界和准备测试、8 项测试门禁自检通过。两次运行使用同样的断言。参见 `integration-tests.log`、`integration-tmpfs.log`、`contracts.log`、`contracts-tmpfs.log`、`boundary-preparation.log`。
- 隔离 Compose `--tasks-only` 通过 9 项服务断言，包含真实 Kernel 重启后的恢复。项目隔离、任务目标与规格更新、steps、criteria 证据、完成和归档均有服务端读回。证据为 `task-collaboration.json` 和 `tasks.log`。
- 隔离 Compose `--webui-only` 通过 20 项，其中 15 项直接使用真实服务，5 项受控浏览器响应。上传及质量、两次真实执行、成果归属、科学验证未知、结构化报告预览、真实消息分页与评审归档有单独证据。参见 `webui/results.json`。
- 隔离 Compose `--six-model-only` 复跑通过 6 个真实模型生命周期。监测趋势保存 10 个独立节点，流场保存 5 节点 4 条依赖边。首次运行中趋势用例的新增检查错误假设存在依赖边，已按注册模型拓扑改为断言 10 个独立节点，并保留流场精确依赖断言。参见 `six-model-retry/v12-six-model-lifecycle.json`、`six-model.log`、`trend-node-sqlite-evidence.txt`。
- 同一真实流场 Run 的生产浏览器专项通过。刷新前后分别取得 HTTP 200 的 graph 和 nodes，核对 5 个完成节点及 4 条边，并点开速度节点。首轮浏览器定位同时匹配节点与算子详情，收窄至节点标题后复跑通过。见 `multi-node-final.log`、`multi-node-browser.json`、`multi-node-service.json`、`screenshots/real-flow-field-nodes-after-reload.png`。
- 隔离 Compose `--interaction-only` 最终通过 6 项，覆盖冻结修订、丢失回执幂等、停止竞争、迟到历史与 SSE 隔离、执行中排队、真实超时后重新审批与恢复。初次 Compose 健康等待超时，第二次 5 项通过，超时恢复观察期内仍为 RUNNING。使用 tmpfs 复跑原断言通过。参见 `interaction.log`、`interaction-retry.log`、`interaction-verified/platform-groundwater-e2e-d0020c4f6543/gate.json`。
- 从仓库根目录运行 `npm --prefix source/web run test:e2e:build`，退出 0，生产构建 Chromium 全套 35 项通过。四个规定视口覆盖首页、工作台与成果页；键盘焦点、无障碍严重项扫描、长队列下输入框可达、表格局部滚动、窄屏无页面横向溢出均有断言。首次全套 GeoTIFF 等待探针超时，原断言单独复跑通过。第二轮复现身份初始化期间移动菜单接收点击后无法打开；修复为身份就绪前禁用按钮，并增加延迟 capabilities 的回归测试。最终证据为 `browser-verified.json`、`browser-build-verified.log` 和 `screenshots/`；首次和第二次结果保留为 `browser-first.json`、`browser-second.json`。
- [真实 Provider 验收](../acceptance/UI-13_PROVIDER_REPORT.md)为 FAIL，24 次中 13 次通过。样本可用于复查失败码和服务归因。
- [G01–G18 状态](../acceptance/UI-13_CONTRACT_STATUS.md)分别记录当前公开证据和未开放能力。G18 已有真实持久化复现及 ADR-UI-011。
- 未运行独立 PDF 渲染验收与各类物理模型的科学准确性评审。结构化 JSON 报告预览、成果版本与文件校验已运行。

### 本轮修改文件

| 文件 | 用途 |
|---|---|
| `source/web/src/App.tsx` | 身份存储范围可用后开放移动导航 |
| `source/web/e2e/application-accessibility.spec.ts` | 固定重现早期导航点击与跨视口焦点检查 |
| `source/web/e2e/v12-live.spec.ts` | 真实科学未知态、结构化报告与历史分页证据 |
| `source/kernel/tests/test_internal_modules.py` | G18 服务端检查与 SQLite 修订复现 |
| `source/tests/e2e/groundwater/model_lifecycle.py` | 真实图、节点、状态与拓扑断言 |
| `acceptance/UI-13_EXECUTION_MATRIX.md`、`acceptance/UI-13_CONTRACT_STATUS.md`、`acceptance/UI-13_PROVIDER_REPORT.md` | 当轮验收与原始产物索引 |
| `progress/UI-13_LIFECYCLE_ACCEPTANCE.md`、`progress/PROGRESS.md`、`progress/DECISIONS.md` | 总验收、进度和后端 ADR |


## G18 Kernel 修复复验

G18 为 **PASS**。G18 复验时 UI-13 矩阵为 **17 PASS、1 BLOCKED_CONTRACT**，场景 13 仍缺公开发布关联。真实外部 Provider 的原门禁为 **FAIL（13/24）**。

- 同一 AgentRun 内按候选身份保留准备检查，使用已有 SQLite 修订重建快照。较新检查替换相同身份，旧回执重放不改当前修订。候选与冻结 action 通过各自公开投影区分。历史头记录可从修订恢复。Task progress 使用同一读取逻辑。
- Kernel 回归覆盖两个提交顺序、同一候选复检、旧回执、旧数据恢复、跨轮迟到和只冻结可执行分支。全量 Kernel 891 项通过，类型、Ruff、Core imports 通过。`dev check --scope kernel` 整体退出 1，仓库级架构检查命中本轮之前已存在的 `web/src/style.css:37` 产品专用选择器。合同门禁 190 项通过，含 OpenAPI、TypeScript 与 Python SDK。
- 隔离 WebUI 门禁 21 项通过。Compose 使用固定 Provider 驱动工具，真实 Gateway、Kernel、Data、Executor 和生产构建 Chromium 完成场景 05。上传无坐标的两井月度水位，流场报告缺 xField、yField。刷新后两分支可见，确认仅冻结监测趋势；真实 Run 成功，返回 21 份成果引用。已按 Session ID 与 WorkflowRun 核对 SQLite、浏览器和服务端证据。
- 改动包含 Kernel 的 `tasks/preparation.py`、`tasks/progress.py`、`agent_runtime/evidence.py`、`agent_runtime/preparation_tool.py`、Kernel 两个测试文件、Compose 固定 Provider 夹具和 `v12-live.spec.ts`。前端产品代码、公开 Schema 和数据库结构沿用现有合同。
- 证据见 `g18-kernel-final.log`、`g18-contracts.log`、`g18-webui-final/results.json`、`g18-final-lifecycle.json`、`g18-final-sqlite-evidence.json` 和 `screenshots/g18-preparation-after-refresh.png`。


## ADR-UI-013 复验

批准调整后的 **18 个生命周期场景全部 PASS**。UI-13 的真实 Provider 门禁仍为 **FAIL**，最近一次为 13/24，本轮未复跑。

- 13a 在隔离 Compose 暂停 Data worker。生产浏览器按真实 Run 显示 Data `PREPARING`，运行仍为 `RUNNING` 且没有输出。恢复 worker 后同一发布 ID 为 `PUBLISHED`，Run 为 `SUCCEEDED`。已核对 Executor SQLite 的 job、run、publication 关联。跨项目、未认证及直接读取 Executor 所有的 Data 发布记录均被拒绝。Kernel 另覆盖非所有者、旧记录无关联、上游失败和远端作业身份不匹配。
- 13b 在真实服务上延迟浏览器 Results 请求，再让请求返回 503。Run 与 Data 均已成功，浏览器在两种读取状态下都无下载。重试通过真实 Results 和精确 Data 对象校验后，下载链接恢复，内容 SHA-256 与对象引用一致。
- 新增独立的 Executor 与 Kernel 发布读取路由、生成合同和 SDK、Web 运行详情、专项服务测试、两条浏览器路径和 HTTP API 文档。保留现有发布提交顺序与旧 JobStatus 响应。无数据库迁移。
- 服务 63 项、合同 190 项、Web 396 项、隔离 WebUI 21 项和 Web/TLI/SDK 交互 7 项通过。早期环境先后遇到严格枚举序列化和隔离 Docker 操作超时，修复并复跑。证据见 `adr-ui-013-service-release.log`、`adr-ui-013-contracts.log`、`adr-ui-013-webui-final/results.json`、`adr-ui-013-interaction-verified/platform-groundwater-e2e-20bdc8cd812f/gate.json`、`adr-ui-013-publication-evidence.json`、`adr-ui-013-executor-sqlite.json` 及 `screenshots/publication-preparing.png`。
