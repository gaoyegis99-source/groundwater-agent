# UI 实施进度

| ID | 状态 | HEAD | 修改文件 | 测试结果 | 备注 |
|---|---|---|---|---|---|
| UI-01 | PASS | f0b9f6d6 | CONTRACT_MATRIX.md、ENTITY_RELATIONSHIP_AUDIT.md、CONTRACT_GAPS.md、DECISIONS.md、PROGRESS.md | 合同门禁 187 项；相关 Kernel/Executor 104 项 | 按真实持久化、HTTP、SSE、Web 类型审计；G01–G10 已记录 |
| UI-02 | PASS | f0b9f6d6 | STATE_MAPPING.md、UI_SELECTOR_DESIGN.md、ui-selectors.ts、ui-selectors.test.ts、types.ts、DECISIONS.md、PROGRESS.md | Selector 19 项；Web 331 项；类型检查及构建通过 | 独立纯投影，保留合同未提供状态 |
| UI-03A | PASS | f0b9f6d6 | SessionNavigation、App、目录/消息 hooks、重命名与测试 | Web 345 项，生产构建 Playwright 26 项 | 已批准的基础导航范围 |
| UI-03（完整范围） | TODO | | | | 等待 G01 目录投影及下一阶段批准 |
| UI-04 | PASS | f0b9f6d6 | TurnTimeline、活动与消息组件、快照读取、测试 | Web 353 项、Kernel 59 项、Playwright 27 项 | [实施与恢复报告](UI-04_TURN_TIMELINE.md) |
| UI-05A | PASS | f0b9f6d6 | Task Header、TaskCard、WorkspacePanel、UI-02 Selector、测试 | Web 375 项、Kernel 43 项、生产 Chromium 33 项、公共合同 190 项、隔离 WebUI 20 项 | [实施与验证报告](UI-05A_TASK_OVERVIEW.md) |
| UI-05B | PASS | f0b9f6d6 | Task 详情、科研交付条件、证据与成果、评审入口及发送恢复 | Web 375 项、Kernel 43 项、生产 Chromium 33 项、隔离 WebUI 20 项 | [实施与验证报告](UI-05B_TASK_REVIEW.md) |
| UI-06 | PASS | f0b9f6d | 数据准备条目、精确版本质量、建议版本核验、会话及修订隔离 | Web 378 项、Kernel/Data 47 项、生产 Chromium 33 项，含窄屏准备修订竞态 | [实施与验证报告](UI-06_DATA_PREPARATION.md) |
| UI-07 | PASS | f0b9f6d | 科研方案摘要、按需构建依赖图、只读草稿与身份核验 | Web 383 项、Kernel Draft 56 项、隔离 WebUI 20 项，含草稿身份错误恢复 | [实施与验证报告](UI-07_WORKFLOW_DRAFT.md) |
| UI-08 | PASS | f0b9f6d | 冻结引用、参数差异、5xx 回执核对、断线与旧轮次写入保护 | Web 387 项、Kernel 14 项、生产 Chromium 33 项、隔离 Interaction 6 项 | [实施与验证报告](UI-08_CONFIRMATION_REVIEW.md) |
| UI-09 | PASS | f0b9f6d | 运行身份、时间与尝试、冻结快照、成果与节点隔离 | Web 391 项、Kernel/Executor 22 项、生产 Chromium 33 项、隔离 Interaction 6 项 | [实施与验证报告](UI-09_WORKFLOW_RUN.md) |
| UI-10 | PASS | f0b9f6d | 停止回执隔离、响应失败后核对、取消与发布竞争、新确认恢复 | Web 394 项、公共合同 190 项、Kernel/Executor 90 项、生产 Chromium 33 项、隔离 Interaction 6 项 | [实施与验证报告](UI-10_STOP_AND_RECOVERY.md) |
| UI-11 | PASS | f0b9f6d | 成果复核、发布读取与精确下载、空会话复用 | Web 396 项、公共合同 190 项、相关后端 13 项、生产 Chromium 33 项、隔离 Interaction 6 项 | [实施与验证报告](UI-11_RESULT_REVIEW.md) |
| UI-12 | PASS | f0b9f6d | 四视口、平板工作台内容、矮视口队列与键盘焦点 | Web 396 项、生产 Chromium 35 项（其中可访问性与重排 20 项） | [实施与验证报告](UI-12_VISUAL_RESPONSIVE.md) |
| UI-13 | FAIL | f0b9f6d | 生命周期验收、移动导航回归、G01–G18 专项 | 18 个场景 PASS；真实 DeepSeek 最近门禁 13/24 | 生命周期矩阵与真实 Provider 门禁独立记录。[总验收](UI-13_LIFECYCLE_ACCEPTANCE.md) |
| G18 | PASS | f0b9f6d | Kernel 同轮准备候选、重放和历史恢复 | Kernel 891 项、合同 190 项、隔离 WebUI 21 项、真实趋势 Run 和 SQLite 交叉验证；仓库级架构检查命中已有 Web CSS | [G18 复验](UI-13_LIFECYCLE_ACCEPTANCE.md#g18-kernel-修复复验) |
| G06 / ADR-UI-013 | PASS | f0b9f6d | 公开发布关联和场景 13a/13b | 服务 63 项、合同 190 项、Web 396 项、隔离 WebUI 21 项、Web/TLI/SDK 交互 7 项 | [复验](UI-13_LIFECYCLE_ACCEPTANCE.md#adr-ui-013-复验) |

## 第一次执行核验

- 审计 HEAD `f0b9f6d6d5e1df970794d2cd16b9cdcec387b7fc`。初始 `git status --short` 仅有未跟踪的 `plans/ui-interaction-spec/`，保留既有计划。
- `bin/bootstrap --status` 退出 0，锁定 CPython 3.12.8 已就绪；Node 22.23.1、npm 10.9.8、Vitest 5.0.3。
- 从 `source/` 运行 `../bin/dev check --scope contracts`，退出 0。Runtime OpenAPI、公共/TypeScript 合同、Python SDK 合同、187 项 Pytest、Ruff、diff 检查通过。
- 使用上述锁定 Python 从 `source/` 运行 `python -m pytest -q kernel/tests/{test_schema_contract,test_session_history,test_tasks,test_turn_progress,test_task_controls,test_confirmation_recovery,test_session_results,test_workflow_progress}.py services/executor/tests/test_schema_contract.py`，退出 0，104 项通过。Pytest 清理旧临时目录时有权限警告。
- `npm --prefix source/web test -- src/ui-selectors.test.ts` 退出 0，19 项通过。另用 TypeScript 编译器按现有 Web 配置显式纳入测试文件，退出 0。首次补充检查发现 Task evidence 手写类型过窄，已按实际投影补齐并复检通过。
- 从 `source/` 运行 `../bin/dev check --scope web`，退出 0。36 个测试文件、331 项测试、Web typecheck、生产构建、Ruff 和 diff 检查通过。另执行 `git diff --check`，并直接检查本次未跟踪交付文件的 Markdown 本地链接及尾随空白。
- 浏览器 E2E、真实 Provider 和完整跨服务验收未运行。本轮验收范围为源码合同、相关后端回归和独立 Selector，尚未接入页面。

UI-03 等待用户批准。完整导航范围还需 G01 的最近活动和目录主任务/待处理投影，或用户明确批准基于现有合同的范围。见[合同缺口](../contracts/CONTRACT_GAPS.md#ui-03-前置条件)。

## UI-02 补充审查 · PASS

- 分开投影 Results 未请求、加载中、已加载和失败。成功运行仅在完整读取后仍缺匹配引用时显示「成果待同步」。按精确 Data 核验逐项开放预览和下载。
- Task 非空 criteria、空 steps 与恢复候选维持现有语义。原 19 项覆盖保留，新增 9 项，Selector 共 28 项通过。含测试文件的 TypeScript 检查通过。
- 从 `source/` 执行 `../bin/dev check --scope web` 退出 0，36 文件、340 项测试、类型检查、生产构建、Ruff 和 diff 检查通过。相关 Kernel 回归 57 项通过。文档本地链接和空白检查通过。
- 详情见 [UI-02 Selector 补充审查](UI-02_SELECTOR_BOUNDARY_REVIEW.md)。UI-03 仍为 TODO，完整范围需要 G01 投影或批准缩减范围，并等待用户批准开始。


## UI-03A 基础会话导航 · PASS

- 通过现有 v12 Session 路由验证新建、目录、搜索、游标分页、重命名、归档及恢复。会话行通过 UI-02 Selector 读取归档状态及可用操作。桌面 240px 导航在窄屏改为全宽会话视图，支持 Escape、键盘焦点返回及选择后关闭。
- 会话目录按项目和身份隔离，过期筛选与分页结果不覆盖当前数据；迟到的 Session 快照和新建响应不劫持当前选择。Results 读取四态和 Data 精确引用核验仍由 UI-02 保留，导航只接入 Session 元数据。
- 修改 `App.tsx`、`SessionNavigation.tsx`、`session-navigation.css`、`SessionRenameDialog.tsx`、`use-project-sessions.ts`、`use-session-stream.ts`、`use-message-actions.ts`、`SessionNavigation.test.tsx`、`conversation-polish.test.tsx`、`e2e/conversation-polish.spec.ts` 及本文件。
- `../bin/dev check --scope web` 从 `source/` 执行，退出 0，37 个文件、345 项通过，含 28 项 Selector 测试。类型检查、生产构建、Ruff 和 `git diff --check` 通过。
- `npm --prefix source/web run test:e2e:build` 在生产构建上运行 Chromium，API 使用符合公开合同的测试响应。首次与 Web 门禁并行运行时，1440px 确认按钮可见性等待超时（25 通过，1 失败）；串行复跑退出 0，26 项通过。新增导航用例检查桌面/390px 交互、迟到响应、键盘焦点、无横向溢出以及 axe 无 serious/critical。
- 截图由 Chromium 实际渲染，桌面 1440×900 和窄屏 390×844。真实跨服务 E2E 未运行。Kernel API、数据库 Schema、Executor 和 UI-04 未改动。


## UI-04 Turn 时间线 · PASS

- 轮次组织、精确业务卡归属、科学 Markdown/MathML、只读历史确认、滚动与分页位置恢复均已接入。目录与工作台维持原有范围。
- Web 门禁退出 0，37 文件、353 项通过。TypeScript、生产构建、Ruff 与 whitespace 通过。Kernel 相关合同及恢复回归 59 项通过。
- Playwright 生产构建最终 27 项通过。初次浏览器回归发现确认布局测试用瞬断 SSE 替代稳定连接，以及历史确认折叠后旧测试仍直接点卡片。更新测试按实际交互验证。最终桌面 1440×900、窄屏 390×844 截图均由 Chromium 产生。
- 浏览器使用合同兼容的 HTTP/SSE 测试响应，Native EventSource 续接请求实际发送；真实跨服务 E2E 未运行。见[实施与恢复报告](UI-04_TURN_TIMELINE.md)。
- Task 目标、修订历史和精确归属缺口见 G02/G03。


## UI-05A Task 生命周期与研究任务概览 · PASS

- 当前 Session 有主 Task 时显示标题区，任务概览复用现有工作台。真实业务步骤、完成条件证据、约束和阻塞原因取自 Session 历史快照。标题区和工作台共用 UI-02 Selector。
- UI-05A 复核补齐原始目标标签、补充信息和当前执行确认入口。正常提交补充信息后读取新快照，刷新后恢复新规格。会话切换与迟到快照继续隔离。
- 当前工作区 Web 38 个文件、372 项及 Kernel Task/Session 历史 43 项通过。生产构建、TypeScript、diff 检查通过。Playwright 生产构建 33 项通过，浏览器使用合同兼容响应，输出 1440×900 和 390×844 截图。见[实施与验证报告](UI-05A_TASK_OVERVIEW.md)。
- 长对话复核将 Task Header 移到滚动区上方，桌面与窄屏滚到最新消息时仍可打开任务概览。本轮 Web 373 项、Kernel 43 项、生产构建 Chromium 33 项通过。
- 长研究目标窄屏复核通过。任务概览和当前执行确认入口保持可见，概览保留完整目标。
- 完成证据复核覆盖 USER_REVIEW 原文与条件不一致、缺少评审轮次两种快照。Web 375 项、生产构建与两项定向 Chromium 用例通过。

## UI-05B Task 详情与科研交付条件管理 · PASS

- 科研交付条件显示服务端证据、确切成果和来源对话。业务步骤展示依赖、条件关联和已声明缺口。评审和修改沿用普通会话消息。
- Web 367 项、Kernel 43 项、Playwright 生产构建 29 项通过。浏览器验收使用合同兼容的响应，并生成桌面与移动端截图。真实跨服务 E2E 未运行。
- 逐步骤失败、逐条件证据明细登记为 G14/G15。UI-06 等待批准。见[实施与验证报告](UI-05B_TASK_REVIEW.md)。
- UI-05B 授权复核补齐任务页评审发送恢复。刷新后可重试原请求并保留草稿。Web 375 项、Kernel 43 项、生产 Chromium 33 项通过。
