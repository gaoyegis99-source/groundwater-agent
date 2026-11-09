# Kernel

Kernel 是产品中立的**交互与执行编排控制面**。它把用户请求、Intent、Agent、
Task、人工确认和 WorkflowRun 组织为可恢复、可审计的持久状态机，但不拥有数据内容、
知识证据、Workflow DAG 或节点执行。

架构原则以[架构契约](../docs/architecture/architecture-contract.md)为准；公开字段和状态模型以
[HTTP API v11](../docs/capabilities/http-api.md)及冻结 OpenAPI 为准。本文只说明 Kernel 组件自身的
功能、接口和边界。

## 当前身份

| 版本轴 | 当前值 |
| --- | --- |
| Module Contract / module version | v9 / 10.0.0 |
| Python distribution | `platform-kernel-services` 7.0.0 |
| Public HTTP | v11 |
| Kernel Config | v8 |
| Kernel SQLite | v33 |

Module version、distribution version、HTTP、Config 和 SQLite 是独立兼容轴，不能合并为
一个“Kernel 版本”。可执行事实见 [`module.yaml`](module.yaml)。

## 职责边界

| Kernel 拥有 | Kernel 不拥有 |
| --- | --- |
| Session、MessageRequest、消息和目标修订 | 用户账户、成员关系和 Session owner |
| Intent 请求、决策和路由记录 | Provider 的模型实现和服务状态 |
| AgentRun、Plan、Action、记忆、上下文视图和终态回复 | Data 的 Dataset、Version、Object、Semantics 和 bytes |
| TaskSpec、TaskProgress、TaskPreparation | Knowledge 的知识版本、Evidence Unit 和向量索引 |
| Confirmation、修订、拒绝和控制提案 | Executor 的节点、尝试、租约和检查点 |
| WorkflowRun、提交/轮询进度和输出引用投影 | 完整 Workflow DAG 和 Operator 实现 |
| Session Event、Provider 请求元数据和脱敏 Trace | Worker workspace 或未发布中间结果 |

Kernel 可以保存 Data/Knowledge 的精确身份、checksum 和本轮检查事实，但不能复制其目录、
对象正文或证据正文成为第二份权威状态。`TaskPreparationV1.status=READY` 只证明当前 Turn
所需合同和身份已检查，不证明数据质量、科学适用性或业务验收。

## 功能分区

| 子模块 | 功能 | 稳定进程内入口 |
| --- | --- | --- |
| `api` | HTTP、项目边界、Problem Details、SSE 和 wire 映射 | FastAPI routers |
| `access` | 身份解析、声明式路由角色策略和对象所有权调用顺序 | `resolve_identity`、`RouteAccessPolicy` |
| `composition` | 装配数据库、Provider、HTTP client、owners 和 runtime | `PlatformApplication` |
| `runtime` | 启动恢复、队列领取、并发调度和 commit signal | `KernelRuntime.start/stop/recover` |
| `interaction` | Session、消息、Intent 请求、Confirmation、控制和历史投影 | `InteractionOwner` |
| `intent_routing` | 语义分类、任务动作和有限协议修复 | `IntentRouter.route` |
| `agent_runtime` | 上下文、Agent 工具、SDK 阶段、计划、动作、回复和恢复 | `AgentRuntimeOwner` |
| `tasks` | Task 修订、完成条件、进度和准备投影 | `get_task`、`save_preparation` |
| `workflow_execution` | WorkflowRun、Executor 提交、轮询、取消和恢复 | `WorkflowExecutionOwner` |
| `settings` | Kernel Config 和环境变量的严格校验 | `KernelSettings.from_environment` |
| `readiness` | 下游服务、Worker、Provider 和定义摘要聚合 | `PlatformReadiness.check` |
| `schema` / `database` | SQLite v33、事务、事件和 owner DDL | `connect_database`、`initialize_database` |

三个业务 owner 通过 `application/ports.py` 和窄 facade 协作。它们共享一个 SQLite，以便
Session、Agent、Task、Confirmation 和 WorkflowRun 的关联变更与事件可以原子提交；表写入
所有权由核心导入检查保护。

## 公开 HTTP 接口

Gateway 下共有 26 个 Kernel Public API v11 operation。公开 API 不提供直接创建 Job、节点
执行或绕过 Confirmation 的接口。

| 接口组 | 主要能力 |
| --- | --- |
| 系统 | `GET /api/v12/health`、`/ready`、`/capabilities` |
| Session | 创建/分页列出 Session，读取当前投影 |
| 消息与任务 | 提交 `message-request.v5`，读取消息、Task、Preparation 和 Results |
| 执行控制 | 停止、确认、拒绝、确认修订和 WorkflowRun 恢复 |
| 自然语言控制 | 确认或取消持久化的 control proposal |
| 工作流草稿 | 读取和导出不可执行的 `WorkflowDraftV1` |
| 流 | 可续接的 Session Event v20 SSE；临时、不可重放的 preview SSE |
| 审计 | AgentRun、脱敏 Trace、Provider requests 和 WorkflowRun |

接口约定：

- HTTP JSON 和查询参数使用 camelCase；Python 与 SQLite 内部使用 snake_case。
- `POST .../messages`、确认和停止等异步命令返回接收状态；后续事实从查询或 SSE 获取。
- `requestId` 用于幂等；相同 ID 的载荷变化发生冲突。
- 消息正文与最多 20 个有序 `DataVersionRefV2` 引用分开持久化。
- 已知边界错误返回 RFC 9457 `application/problem+json`，稳定分类在顶层 `code`。
- `X-Trace-ID` 和 `X-Request-ID` 用于跨边界关联；当前 `project_id` 是单项目隔离边界，
  不是用户身份认证。

完整方法、路径和 DTO 见 [`contracts/openapi-v12.json`](contracts/openapi-v12.json)。

## 身份与访问策略

V12 由 Kernel 管理本地用户、密码会话、个人 API Token 和项目成员关系。公开请求通过统一
Gateway 转发后仍由 Kernel 复核；角色仅为 `admin/researcher/viewer`。资源归属用于溯源，
PRIVATE 仅 owner/admin 可读，PROJECT 对项目成员可读。人工维护入口见
[`../docs/maintenance/command-directory.md`](../docs/maintenance/command-directory.md)。

## Agent 工具边界

Kernel 按请求模式裁剪 Agent 可见工具，模型不能自行扩大权限。

| 模式 | 可用能力 | 明确禁止 |
| --- | --- | --- |
| 澄清 | 提交结构化澄清回复 | Observation、计划、模型调用和执行 |
| READ_ONLY 对话 | Data/Knowledge Observation、任务/执行事实、终态回复 | 计划、模型调用和 Confirmation |
| EXECUTION_PROPOSAL 对话 | Observation、`prepare_task_inputs`、`record_plan`、已装配模型工具 | 未确认执行、动态 DAG 和未装配模型 |
| WORKFLOW_DRAFT | Observation、Task 元数据/执行事实、冻结 Operator Catalog、草稿提交 | 执行计划、模型调用、Confirmation 和 Job |

Kernel 自有工具分为；具体模式只装配其获准的子集：

- 计划和准备：`record_plan`、`prepare_task_inputs`；
- 任务和执行事实：`update_task`、`list_execution_facts`、`get_execution_fact`；
- 草稿目录：`list_operators`、`get_operator`；
- 统一终态：`submit_reply`。

WORKFLOW_DRAFT 中的 `update_task` 只保存用户目标、要求、`DRAFT` 完成条件和证据；它不会
创建 Plan、Action、Confirmation、WorkflowRun 或任何执行权限。

当前装配的 Observation 和模型工具来自 `kernel-config.v8`，不是写死在 Kernel 中。工具调用仍
受输入 Schema、当前 Turn 证据、计划绑定、TaskPreparation 和 Confirmation 门禁约束。

## 下游接口

| 对端 | Kernel 使用的接口 | Kernel 侧约束 |
| --- | --- | --- |
| Agent / Intent Router Provider | OpenAI 兼容 Responses 或 Chat Completions；零生成健康探测 | Agent/Router 独立配置；一次同模型协议修复；不跨模型回退 |
| Data | Config 声明的只读 Observation HTTP | 请求/响应 JSON Schema 校验；执行只冻结精确 `DataVersionRefV2` |
| Knowledge Next | Config 声明的检索、读取和 lookup Observation HTTP | 最终引用必须是本轮完整读取且状态为 `OK` 的精确证据 |
| Executor | Execution Protocol v6：提交、查询、按 request 查询和取消 | 新提交前复核当前 worker 的 Model/Workflow/Executor 身份、服务名与 `definition_set_digest`；同一 WorkflowRun 使用幂等 request identity |
| Gateway / Web | Public API v11、Problem Details 和 SSE | Gateway 无业务状态；Web 不能创建执行成功事实 |

服务之间只交换声明的 HTTP 合同，不导入对方实现、不读取对方数据库，也不挂载对方持久目录。

## 主运行流程

1. `POST .../messages` 原子保存 MessageRequest、用户正文、输入引用和目标修订，并返回回执。
2. Kernel 的规则或 Intent Router 解析语义和任务动作；Kernel 再决定实际运行模式和权限。
3. Agent 在当前 Turn 读取所需 Data/Knowledge，必要时保存 Task 更新或 TaskPreparation。
4. 只读请求经 `submit_reply` 校验后完成；工作流草稿只保存/导出，不进入执行链。
5. 执行请求先保存 Plan 和不可变 AgentAction，再由 Kernel 生成 Confirmation。
6. 用户确认冻结内容后，Kernel 按位置创建 WorkflowRun 并向 Executor 提交幂等 Job。
7. Kernel 轮询权威 Job 状态；Executor 成功发布后只接收持久化的 Data 输出引用。
8. Agent 从已保存 RunState 恢复，Kernel 用 WorkflowRun/Data 事实校验终态并投影到 Session。
9. 每个关键提交同时写入 Session Event，客户端通过 SSE 或查询恢复当前视图。

模型文本、SDK tool return、Worker 临时文件和传输 ACK 都不能单独建立“执行成功”事实。

## 持久化、恢复与并发

- 唯一持久文件是 `/data/kernel.sqlite3`，当前 Schema 为 v33。
- 新空库按 v33 初始化。仅签名精确匹配的 v29/v30/v31/v32 数据库可以在生成内容校验冷备后，
  按 v29→v30→v31→v32→v33 的适用后缀逐步事务升级；其他版本或结构不完全匹配时启动失败并返回
  `SCHEMA_VERSION_UNSUPPORTED`。
- 启动恢复覆盖 Workflow dispatch、Confirmation decision、control proposal、Intent route、
  Agent start、运行中的 AgentRun 和等待执行结果的 continuation。
- Intent Router、Agent compute segment 和 Workflow dispatch 共用默认八槽计算门禁；
  dispatch 另有默认八槽的局部上限。两者均可配置；等待确认和等待 Workflow 不占槽。
- `SUBMIT_UNKNOWN` 先按稳定 request identity 查询远端。仅当没有已知 Job、截止时间与取消门禁仍通过，且当前 worker 冻结的 Model/Workflow/Executor 版本及远端 definition-set digest 与原 WorkflowRun 一致时，才允许新的 submit。若远端 Job 已存在，继续对账，不因本地版本变化抹掉已知结果。当前协议尚无动态权限/费用预算查询端口，不能将版本复核误称为这两项检查。
- WorkflowRun 冻结服务名，但没有冻结服务 URL。部署仍须保持原端点绑定；远端定义摘要只能校验定义，不能证明端点身份，不能声称已经完成端点级恢复绑定。
- 定义或服务名不匹配时保留原派发状态、记录稳定原因，并以 30 秒间隔重查；兼容 worker 恢复后仍使用原 request identity 续接，不立即空转。
- preview 是内存态；Session Event、Agent/Workflow 状态和结果引用是持久权威事实。
- Trace 只保存允许的元数据；Provider key、原始提示、观察正文、工具参数和模型推理不入库。

## 配置与运行约束

`KERNEL_CONFIG_PATH` 必须指向严格的 `kernel-config.v8`。配置包含产品身份、system prompt、
服务绑定、Observation 合同、Executable Model 投影、固定执行案例和冻结 Operator Catalog；
完整 Workflow DAG 只进入 Executor Config。

部署还必须分别提供 Agent 与 Intent Router 的 Provider、协议、URL、模型、key file、上下文窗口、
输出上限、请求超时和 segment 超时。可配置同 Provider、同模型的备用 base URL，但端点只能在
调用开始前选择；已经开始的失败请求不会切换端点或重放。

当前边界限制：

- 一个 Kernel 进程只配置一个 `PLATFORM_PROJECT_ID`；
- 没有账户、成员和授权模块，外部暴露前必须由可信边缘补充认证、授权和可信头重写；
- 能力在装配期冻结，不支持运行时安装 Operator、模型或动态执行 DAG；
- 当前 SQLite 和进程内调度设计面向单 Kernel 实例，主动—主动横向扩展不是既有保证。

## 验证

从仓库根目录运行：

```bash
pytest -q kernel/tests
python -m scripts.checks.check_core_imports
```

变更 HTTP、存储、配置或跨服务协议时，还必须按
[执行手册](../docs/operations/execution-playbook.md#per-change-checks)扩大验证范围。开发检查通过不等同于
正式发布、真实数据质量或科学适用性认证。
