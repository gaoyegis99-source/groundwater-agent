# UI-13 生命周期场景执行矩阵

本矩阵记录当前工作区授权验收。隔离 Compose 使用从本次源码装配的 Groundwater 6.1.0、Kernel 7.0.0、Data 3.0.0、Executor 7.0.0、Knowledge Next 2.0.0 和 Web 2.0.0。数据为合成夹具。除独立外部 Provider 门禁外，模型响应来自固定本地 Provider。生产浏览器测试另用符合合同的 HTTP/SSE 响应检查布局及可控竞态。下表中的持久化证据来自写后读取、刷新、服务重启或标明的数据库断言。

证据目录为 `ui-13-final`。WebUI 用例标题可在 `webui/results.json` 中检索。Interaction 最终通过证据在 `interaction-verified/platform-groundwater-e2e-d0020c4f6543/artifacts`。六模型、Task 协作和补充测试的命令与退出码见[总验收报告](../progress/UI-13_LIFECYCLE_ACCEPTANCE.md)。

| 场景 | 前置条件、数据与服务 | 请求、返回和持久化证据 | 浏览器验收与证据 | 当前状态 |
|---|---|---|---|---|
| 01 普通咨询 | 空 Session，方法咨询，隔离 Kernel | POST messages，随后 GET Session、Task、messages 和 results。回复持久化，Task 为空，未生成授权和 Run。 | WebUI 逐题 01 与 Task 协作 `casual_no_task`。 | PASS |
| 02 创建持续研究 | 空 Session，明确研究目标及交付条件，Kernel | POST messages 后 GET Session、task 与 messages。任务及修订随 Turn 读取，Kernel 重启后复查。 | WebUI Task review 检查研究标题区；Task 协作 `explicit_goal_and_review` 和 `additive_revision`。 | PASS |
| 03 地下水需求澄清 | 无输入附件，提问水位，隔离 Kernel | POST messages 返回澄清选项，点选井口埋深再 POST 同一入口。刷新 GET messages，核对第二轮原文且无授权/Run。 | WebUI「水位高程和埋深澄清」。 | PASS |
| 04 上传与质量 | 合成 CSV，Data + Kernel | 上传、发布、GET Data version 与消息，核对版本、字段质量、SHA-256 及刷新附件。 | WebUI 03 与 Data format 回归。 | PASS |
| 05 同轮局部阻塞 | 合成两井、12 个月水位 CSV，无 XY。隔离 Gateway、Kernel、Data、Executor 与生产构建 Chromium，Provider 使用固定工具脚本。 | Data 实际发布 CSV。POST messages 后 GET preparation 同时返回流场 BLOCKED 与监测趋势 READY。SQLite 同时保留两项，缺项为 xField、yField。POST confirm 仅冻结趋势 action；GET WorkflowRun 返回 SUCCEEDED，21 份已登记成果。 | 刷新后仍显示「待补充」与「可提案」，随后由浏览器确认并执行趋势。见 `g18-final-lifecycle.json`、`g18-final-sqlite-evidence.json`、`screenshots/g18-preparation-after-refresh.png`。Kernel 另测两种顺序、重复、历史恢复和迟到写入。 | PASS |
| 06 方法建议及草稿 | 监测描述统计、隔离 Knowledge/Kernel | POST messages；GET draft 与导出，核对方法依据、草稿内容和 Session 无确认/Run。 | WebUI「水位方法草稿保留依据和旧草稿」。 | PASS |
| 07 修改方案 | 旧草稿或待批次、合成观测表，Kernel | 再提交草稿消息获得独立 ID，GET 旧稿仍可读。POST `/confirmations/{confirmationId}/revisions`，GET messages 核对旧批次已替代。 | WebUI 草稿与 Interaction `revised-proposal.json`。两个独立用例。 | PASS |
| 08 确认后创建 Run | 冻结确认和精确输入，Kernel/Executor | POST confirm，确认前 GET results 无 Run，确认后读取唯一 Run，丢失响应重试不重复执行。 | Interaction `interaction-verification.json`、`sdk-verification.json`。 | PASS |
| 09 多节点及刷新 | 合成监测时序和四井流场，Kernel/Executor/Data | 真实趋势有 10 个独立节点。真实流场按同一 Run ID 请求 graph 与 nodes，均返回 200，复查 5 个完成节点与 4 条依赖边。 | 流场生产页面刷新前后读取同一持久 Run，节点和终态一致，可点开速度节点。见 `multi-node-browser.json`、`multi-node-service.json`、`screenshots/real-flow-field-nodes-after-reload.png`。 | PASS |
| 10 执行中追加 | 暂停隔离 Native Worker，冻结汇总批次 | 追加请求在 Web 队列。GET history 核对原确认、Turn 与 Run。恢复 Worker 后 POST messages 一次，原批次完成。 | Interaction「queues a follow-up」及队列截图。 | PASS |
| 11 停止竞争 | 已派发 Run；分别验证取消先到、发布先完成 | POST stop 返回 202 REQUESTED，GET history 与 SSE 持久回放核对同 requestId。GET Run 分别 CANCELLED 无成果、SUCCEEDED 保留两份成果。 | Interaction `stop-cancelled.json`、`stop-succeeded.json` 及截图。 | PASS |
| 12 超时与恢复 | 停止隔离 Native Worker 直至作业超时，Kernel/Executor/Data | GET 原 Run 与节点，POST recoveries 生成 PENDING 新批次，批准后查独立新 Run。保留旧尝试、超时证据和新成果。 | Interaction `first-attempt.json`、`recovery-pending.json`、`cross-interface-recovery.json`。早期执行超过观察期仍为 RUNNING，原断言复跑通过。 | PASS |
| 13 发布与客户端同步（ADR-UI-013） | 真实汇总 Run，隔离 Gateway/Kernel/Executor/Data，固定 Provider。13a 暂停 Data worker。13b 延迟浏览器 Results 请求，再返回一次 503。 | 13a GET Run 为 RUNNING 且无 outputs，GET Run publication 为 PREPARING。恢复 worker 后同一发布 ID 为 PUBLISHED，Run 为 SUCCEEDED，SQLite 核对作业关联。13b 成功 Run 的发布为 PUBLISHED，重试后按 Data 精确对象校验下载。 | 13a 浏览器显示准备发布且无下载，跨项目和直读完整记录被拒绝。13b 浏览器延迟及 503 均无下载。见 `adr-ui-013-publication-evidence.json`、`adr-ui-013-executor-sqlite.json` 和发布截图。 | PASS |
| 14 同 Task 多 Run | 两轮汇总同名成果，已登记两个 criteria，Kernel/Executor/Data | GET history、results 与精确对象，按两个 Run ID、版本和 SHA-256 对应。公开接口缺少未登记 Run 的 Task revision 关系。 | WebUI 07 覆盖已登记的两次真实运行，完整关联见 G03。 | PASS |
| 15 成果新建 Session | 已发布汇总，Kernel/Data | POST sessions 只传 projectId；GET 新 Session 和 messages，服务端无 Task、确认、运行、历史。草稿附件保留精确对象与语义修订。 | Interaction `continued-result-mobile.png`、WebUI 04。 | PASS |
| 16 断线与刷新 | 已受理确认的响应丢失，隔离 Kernel/Executor | 对照原 POST、GET history 和 SSE cursor，确认请求未盲重放。TLI/SDK 回放服务端事件；Kernel API 专项核对 Last-Event-ID 优先级。 | Interaction 回执丢失、超时恢复。生产浏览器另断言原生 EventSource 的续接请求头。 | PASS |
| 17 会话切换 | 两个独立 Session，延迟真实历史和事件 | GET 旧会话消息在切换后交付；回放旧会话真实拒绝事件。核对新快照、界面和事件记录保持新 Session。 | Interaction `different-session-after-delayed-snapshot.png` 及同用例产物。 | PASS |
| 18 评审归档 | 两个成果条件与一个评审条件，Kernel/Executor/Data | POST 评审消息、PATCH Session（`archived: true`），再 GET messages 和 results。刷新后任务完成、保留评审原文及成果下载校验。 | WebUI 06/07，Task 协作及持久化测试。 | PASS |

## 请求与证据索引

- Session 请求以 `/api/v12/sessions/{sessionId}` 为前缀，创建使用 `POST /api/v12/sessions`。表中的 history 是 `GET /messages`，SSE 是 `GET /events`。场景 16 的浏览器重连、SDK/TLI 实际回放与 Kernel `Last-Event-ID` 优先级分别有独立断言。
- 运行读取使用 `GET /api/v12/workflow-runs/{runId}` 及其 `/graph`、`/nodes`。Data 验证使用精确 dataset、version、semantic、object 身份和对象内容，范围包含普通与历史 Run。数据权限的负例在真实 Kernel/Data 边界测试中执行。
- 场景 14 的 PASS 范围为明确登记 criteria evidence 的两次 Run。其余 Run 的 Task revision 关系为 `BLOCKED_CONTRACT`，见 G03。场景 05 的数据库、浏览器和运行结果已按同一个 Session 与 WorkflowRun 核对。
