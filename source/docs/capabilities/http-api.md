# HTTP API v12

`/api/v11/*` 已退场，统一返回 `410 Gone` 与 v12 升级说明。

## WebUI、终端和 API

三个入口使用同一 Gateway 和 Public API v12。同一实例、同一项目和身份下，会话 ID 可跨入口复用，消息、确认、任务进度和成果均来自同一份服务端记录。Kernel 管理确认与执行状态，Data 管理版本和对象。WebUI 使用由 OpenAPI 生成的 TypeScript 类型和验证器，终端（TLI）使用 Python SDK。客户端可以从独立机器安装和运行，只需要能连接 Gateway。

| 入口 | 使用方式 |
| --- | --- |
| WebUI | 打开研究实例首页，从浏览器会话 URL 的 `#/sessions/<会话ID>` 取得会话 ID |
| 终端 | 安装 `groundwater-api-client` wheel 后运行 `groundwater-tli`，输入 `/help` 查看命令。支持账号交互登录或 `GROUNDWATER_API_TOKEN` |
| API | 使用 `GroundwaterClient` 或直接调用本页 HTTP 接口。SDK 与 WebUI 从同一份公开 OpenAPI 生成 |

安装时使用当前发布中的 `groundwater-api-client` 和 `platform-protocol-contracts` wheel。终端复用浏览器会话的示例：

```bash
groundwater-tli --origin http://127.0.0.1:28186 --username researcher --session "$GROUNDWATER_SESSION_ID"
```

交互模式支持 `/sessions`、`/new`、`/use`、`/datasets`、`/attach`、`/history`、`/status`、`/events` 和 `/results`。普通文本直接发送到当前会话。查看 `/status` 中冻结的参数及输入版本后，使用 `/confirm <确认ID>` 或 `/reject <确认ID> <原因>`。`/events --cursor <事件ID> --follow` 从已读事件自动续接，到当前轮待确认或执行批次结束时退出，也可按 Ctrl-C 停止。`/download <版本ID> <对象ID> <目标路径>` 校验大小和 SHA-256 后保存文件。单次命令省略斜杠，例如 `groundwater-tli --session <会话ID> results`，使用 `GROUNDWATER_API_TOKEN` 可供脚本调用。

Python SDK 示例从同一个 Gateway 读取会话和成果：

```python
import os

from groundwater_api_client import GroundwaterClient

with GroundwaterClient(
    "http://127.0.0.1:28186",
    api_token=os.environ["GROUNDWATER_API_TOKEN"],
) as client:
    session_id = os.environ["GROUNDWATER_SESSION_ID"]
    session = client.session(session_id)
    print(session.agent_steps)
    for result in client.results(session_id).items:
        print(result["workflowRunId"])
```

SDK 提供 `send_message`、`input_reference`、`confirm`、`reject`、`events`、`results` 和 `download_object`。公开 JSON 接口和内容流也可通过 `call` 或 `stream` 传入生成的 `operations` 常量访问。消息重试保留原 `requestId` 和相同正文及引用，事件续接使用 `afterEventId`。

使用 `client.workflow_run(run_id, project_id=session.project_id)` 读取类型化工作流状态。若提交确认后连接断开，可先读取原会话和确认状态，再以同一个确认 ID 复核处理结果。

## 健康与能力

| Method | Path | 含义 |
| --- | --- | --- |
| GET | `/api/v12/health` | Kernel 本地存活检查；不代表下游可用。 |
| GET | `/api/v12/ready` | 全链路 readiness；DOWN 时返回 503 和逐组件状态。 |
| GET | `/api/v12/capabilities` | 观察工具、装配模型和当前用户 display name/roles 的公开投影。 |

聚合 readiness 检查 Kernel 调度器与 SQLite、Data PostgreSQL 与对象卷、Knowledge Next PostgreSQL/pgvector 与 Embedding、Executor Control 与配置 Worker 心跳、Agent Provider 和 Intent Router Provider。响应只包含固定状态和错误分类，不包含 URL、凭证或上游响应正文。

## Kernel 会话

| Method | Path | 含义 |
| --- | --- | --- |
| POST / GET | `/api/v12/sessions` | 创建 Session；按当前 owner 分页列出 Session，GET 支持 `query` 和 `archived`。 |
| PATCH | `/api/v12/sessions/{sessionId}` | 修改标题或归档状态，不删除历史事实。 |
| GET | `/api/v12/sessions/{sessionId}` | Session 当前投影。 |
| GET / POST | `/api/v12/sessions/{sessionId}/messages` | 分页读取消息；提交统一 `message-request.v5`，POST 返回 202。 |
| GET | `/api/v12/sessions/{sessionId}/results` | 按 goal revision 读取执行结果引用。 |
| GET | `/api/v12/sessions/{sessionId}/task` | 当前 `TaskContextV1`。 |
| GET | `/api/v12/sessions/{sessionId}/preparation` | 当前 `TaskPreparationV1` 动作准备集合；返回派生的新鲜度与生命周期。 |
| POST | `/api/v12/sessions/{sessionId}/stop` | 持久化停止请求，返回 202。 |
| POST | `/api/v12/sessions/{sessionId}/confirm` | 确认冻结的 Action。 |
| POST | `/api/v12/sessions/{sessionId}/reject` | 拒绝待确认 Action。 |
| POST | `/api/v12/sessions/{sessionId}/confirmations/{confirmationId}/revisions` | 创建不可变确认修订。 |
| POST | `/api/v12/sessions/{sessionId}/workflow-runs/{workflowRunId}/recoveries` | 为失败运行创建恢复确认。 |
| GET | `/api/v12/sessions/{sessionId}/workflow-drafts/{draftId}` | 读取不可执行的草稿。 |
| GET | `/api/v12/sessions/{sessionId}/workflow-drafts/{draftId}/export` | 导出冻结草稿 JSON。 |
| GET | `/api/v12/sessions/{sessionId}/preview` | 临时、不可重放的回复预览 SSE。 |
| GET | `/api/v12/sessions/{sessionId}/events` | 可续接的 Session Event v20 SSE。 |
| POST | `/api/v12/sessions/{sessionId}/control-proposals/{proposalId}/confirm` | 确认自然语言控制提案。 |
| POST | `/api/v12/sessions/{sessionId}/control-proposals/{proposalId}/dismiss` | 取消控制提案。 |
| GET | `/api/v12/audit/events` | `viewer` 读取项目内无正文 actor/事件事实。 |

`POST .../messages` 是用户消息统一入口。请求可带 `inputReferences`，每项为完整 `DataVersionRefV2`，最多 20 项，按用户选择顺序保存并按 `versionId/objectId` 唯一。正文与引用分别持久化；引用属于幂等载荷的一部分。AUTO 请求由规则和 Intent Router 决定模式；DIALOGUE 与 WORKFLOW_DRAFT 是显式模式。相同 Session/request ID 与完全相同载荷幂等返回原回执，载荷变化冲突。项目默认有 8 个 Agent 并发槽和 100 个等待位；队列满返回 429、稳定代码和 `Retry-After`。公开 API 没有直接创建 Job、节点执行或绕过 Confirmation 的接口。

业务接口使用 Kernel 本地账号的密码会话或个人 API Token。浏览器写操作同时要求 CSRF Token；Gateway 只把 Kernel 已验证的项目身份传给内部 Data/Knowledge 服务。项目角色为 `admin/researcher/viewer`。非项目成员返回 404，项目成员权限不足返回 403，未认证返回 401。
`/api/v12/health` 仅为无身份的存活检查，不提供业务数据。

消息历史中每个 Turn 可包含只读 `progressUpdates`。它由持久 Plan、Confirmation
和 WorkflowRun 事实确定性投影，使用稳定进度标识表达案例命中、输入核验、等待确认、
执行、发布、终态和恢复结果。该字段不包含模型推理、工具参数、观察正文、checksum
或 Provider 私有信息；旧客户端可以忽略这个附加字段。

## Agent 与 WorkflowRun

| Method | Path | 含义 |
| --- | --- | --- |
| GET | `/api/v12/agent-runs/{agentRunId}` | AgentRun、Action 和结构化 AgentFinal v5。 |
| GET | `/api/v12/agent-runs/{agentRunId}/trace` | 已脱敏的顺序 Trace。 |
| GET | `/api/v12/agent-runs/{agentRunId}/provider-requests` | Provider 阶段、尝试、结果、错误类、Token 和时延。 |
| GET | `/api/v12/workflow-runs/{workflowRunId}` | WorkflowRun 权威执行状态和 Data 输出引用。 |

Provider 请求记录不保存 API key、原始提示、工具参数、观察正文、模型推理或上游私有错误正文。

## Data

Gateway 将 `/api/v12/data/*` 映射到 Data `/data/v3/*`。主要资源包括：

- `GET /api/v12/data/capabilities`
- `GET /api/v12/data/inventory`
- `POST /api/v12/data/discovery`
- `GET /api/v12/data/datasets`
- `GET /api/v12/data/datasets/{datasetId}/versions`
- `GET /api/v12/data/datasets/{datasetId}/governance`（Operator 所有者、质量趋势、可读血缘与审计活动）
- `GET /api/v12/data/activity`（Operator 跨任务操作记录）
- `GET /api/v12/data/versions/{versionId}`
- `GET /api/v12/data/versions/{versionId}/semantics/{semanticId}`
- `POST /api/v12/data/versions/{versionId}/semantics`
- publication begin、item upload、prepare、commit、abort 与状态读取
- query create、status、cancel、content
- preview create、status、cancel（`xlsx_table`、`geopackage_layer`）
- `GET /api/v12/data/versions/{versionId}/objects/{objectId}/content`

Data 查询只允许 CSV/Parquet。Preview 只提供 XLSX 工作表和 GeoPackage 图层的有界只读结果，不执行公式或任意 SQL。对象内容支持单段 `Range` 请求，供 COG/GeoTIFF 按需读取。发布支持装配算子目录声明的格式，并在提交时验证 format、MIME、大小和 checksum。内部 Agent 工具是 `get_data_inventory`、`discover_datasets`、`inspect_data_version` 和 `inspect_data_table`。目录概览与发现结果是当前服务状态的候选投影；执行仍以精确版本检查和冻结引用为准。

## Knowledge Next

Gateway 将 `/api/v12/knowledge/*` 映射到 Knowledge Next `/knowledge/v3/*`。主要资源包括 capabilities、imports 及 preflight、documents/versions、单条与批量 lifecycle、Operator activity、retrievals、evidence read/validate 和 lookup。批量生命周期先按当前文档版本生成差异，再以版本和修订号 CAS 在一个事务内全部提交；任一项冲突时整批不写入。公开语义是知识版本和 Evidence Unit，不投影旧 document/chunk API。

Kernel 使用内部 `retrieve_evidence`、`read_evidence` 和 `lookup_knowledge`。引用必须对应本轮完整读取、状态为 `OK` 的 `versionId/unitId/checksum`。

## 内部接口

内部服务不发布主机端口。Kernel 通过固定项目和服务主体调用 Data/Knowledge 只读工具和 Executor；Executor 使用 Execution Protocol v6，并以 `platform-executor` 服务主体通过 Data 私有发布合同解析输入、原子发布输出和记录 lineage。各服务提供 `/internal/health/live` 与 `/internal/health/ready`，供 Compose 和聚合 readiness 使用。

## 公共合同与错误

公开错误固定包含 `type/title/status/detail/code/requestId/traceId/errors`；请求校验问题使用
`VALIDATION_ERROR`，并在 `errors` 中以 JSON Pointer 和说明定位字段。`Content-Type` 固定为
`application/problem+json`，`X-Trace-ID` 用于关联服务侧请求。

[`products/groundwater/contracts/public-api-v12.json`](../../products/groundwater/contracts/public-api-v12.json)从 Kernel、Data、Knowledge
各自的运行时 OpenAPI 确定性生成，并把服务内路径转换为 Gateway 公开路径。对应的
TypeScript 类型由 `openapi-typescript` 生成到 `web/src/generated/`。修改公开路由、请求、
响应或错误模型后运行 `npm --prefix web run contracts:generate`；开发检查使用
`npm --prefix web run contracts:check` 拒绝未更新的合同快照。

字段 Schema、分页参数和错误响应以该公共合同及运行候选的服务 OpenAPI 为准；版本与状态
所有权以[协议与持久化](../reference/contracts.md)和[科研验收](../operations/acceptance-gates.md)为准。
