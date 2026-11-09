# V6 lightweight Agent kernel

`packages/lightweight-agent-core` is an independent, code-first V6 product
surface.  It does not replace the v11 Groundwater product contract: v11 may
continue to require its Data service.  The lightweight package has its own
contract so a developer can install and run it without Data, Knowledge,
Temporal, a Web server, GIS packages, or a container runtime.

## Dependency boundary

The base package is standard-library only.  Install one integration only when
needed:

```bash
cd packages/lightweight-agent-core
python -m pip install .
python -m pip install '.[openai-agents]'
python -m pip install '.[pydantic-ai]'
python -m pip install '.[workflow]'
python -m pip install '.[authoring]'
python -m pip install '.[evaluation]'
python -m pip install '.[yaml]'
python -m pip install '.[web]'
# development-only invariant tests
uv run --group development --group invariant-testing \
  python -m pytest tests/test_state_invariants.py
# development-only architecture contracts
uv run --group architecture lint-imports --no-logo
```

The public kernel interfaces are deliberately narrow:

| Interface | Responsibility | Must not expose |
| --- | --- | --- |
| `AgentDriver` | Turn frozen JSON context into a framework-neutral `AgentDecision` | Live SDK result/state objects or executable checkpoints |
| `ToolExecutor` | Execute a pre-authorized, versioned `ToolReference` and reconcile an action | Plugin installation or unrestricted shell access |
| `ToolRegistrationExecutor` / `FunctionToolExecutor` | Bind trusted `def` and `async def` handlers through one exact-reference catalogue | A second action state machine or implicit execution authorization |
| `AsyncAgentDriver` | Run one v3 model turn asynchronously | Approval, execution, or persistent SDK state |
| `AsyncToolExecutor` | Execute a v3 action with a cooperative `CancellationToken` | Treating cancellation as proof that an external result is absent |
| `RunJournal` | Atomically save a `RunSnapshot` plus event | A database connection or filesystem path |
| `RunQuery` (optional) | Return bounded, payload-free run-list pages from a journal projection | Hydrating inputs, outputs, credentials, or making execution decisions |

For new embedded v4 code, `platform_lightweight_agent.api` exposes a curated
40-name working surface. Advanced authoring, domain, portability, recovery
maintenance, and adapter operations remain available from their explicit
modules. The package root still resolves all 219 existing names for this
release, so old explicit and star imports continue to work. Both the package
root and the 40-name entry point resolve symbols lazily through the standard-
library import machinery; the entry point retains explicit imports under
`TYPE_CHECKING` for source analysis. This release does not claim PEP 561 typed
wheel support. No root name is removed in 0.2.x. This is a staged
public-boundary reduction, not a claim that the underlying implementation has
already been split into the proposed contracts/ports/kernel/adapter packages.
A one-process cold-import
sample fell from about 175 ms to 13 ms; only the absence of eager child and
optional-module imports is a regression invariant, not that timing sample.

### Dependency compatibility policy

The embedding library deliberately declares no base runtime dependency. The
standalone authoring/runtime environment is reproduced from `uv.lock` and an
exported `pylock.toml`; those are exact environments, not an assertion that a
host application must use the same complete dependency graph. The current
adapter compatibility matrix is intentionally narrow and tested as separate
install surfaces:

| Surface | Verified owner/version | Boundary test |
| --- | --- | --- |
| base | standard library only | import does not load AnyIO, JSON Schema, an SDK, DBOS or an ORM |
| workflow | AnyIO 4.15.1 + jsonschema 4.26.0 + immutables 0.21 | v3/v4 concurrency, cancellation, SQLite recovery and v4 process-local read-view isolation |
| PydanticAI | pydantic-ai-slim 1.107.9 + jsonschema 4.26.0 | native tool/structured-output wire contract |
| Agents SDK | openai-agents 0.22.3 + jsonschema 4.26.0 | scripted native tool/approval wire contract |
| authoring/evaluation | Copier 9.19.1 / pydantic-evals 1.107.9 | safe template and opt-in evaluation paths |

The `development` uv group pins pytest and Ruff so the delivery gate cannot
silently use commands from the developer machine's `PATH`. The separate
`architecture` group pins Import Linter 2.15; neither group is emitted into
wheel runtime metadata. Its initial contracts deliberately protect small,
stable seams rather than claim that the existing engine is already fully
layered: `spec`, `journal`, `files`, `workflow_transitions`,
`runtime_contracts`, `runtime_ports`, and `runtime_services` may not depend
on orchestration/adapters; framework adapters may not directly own host,
authoring, portability, evaluation, or Web assembly, and the async scheduler
may not directly own any of those edge modules.

The small, standard-library `runtime_contracts.py` now owns the neutral
Agent/tool request, result, cancellation, authorization, and stable failure
types. `runtime_ports.py` owns the driver, executor, registration, and action
policy protocols. The older `engine` imports are aliases of these same classes;
adapters and the service façade import their neutral owners directly. This
removes a dependency on the full Engine for a tool or model port import, but
does not change journal representation or the v4 transition algorithm.

`engine.py` and `workflow_v3.py` remain one execution component, but the async
coordinator now receives a frozen `WorkflowRuntimeServices` façade instead of
reading dozens of `WorkflowEngine` private fields. The façade exposes only the
existing journal, driver/executor dispatch, cancellation registry, event
wakeup, and resource-lease callbacks. It owns no durable state and implements
no transition itself. A source-level regression check forbids renewed
`engine._...` access from the scheduler. This is the intended extraction
boundary—not a second scheduler or another persisted state representation.

Until a wider host-application matrix has passed the same native-contract
tests, SDK extras remain exact pins rather than speculative compatibility
ranges. This makes a dependency conflict explicit at installation time. A
future range relaxation must add its tested version rows and keep the lock file
as the standalone CLI/template reproduction path.

`SQLiteRunJournal` is a local implementation, not part of the public runtime
contract.  An embedded application may instead supply an in-memory or custom
journal.  Events and checkpoints reject credential-shaped data; credentials
are bound in the target environment, not embedded in definitions or exports.
After schema preflight, the local SQLite adapter selects WAL mode and keeps
`synchronous=FULL` on each connection. This lets frequent execution-control
reads coexist with ordinary state/event writers without changing the one-
journal CAS authority. [SQLite's WAL contract](https://www.sqlite.org/wal.html)
still allows `SQLITE_BUSY` in exceptional lock windows, so control polling
keeps its bounded busy retry and fails on persistent loss of access. WAL uses
`-wal`/`-shm` sidecars and requires same-host shared memory; a filesystem that
cannot provide WAL fails opening rather than silently weakening durability.
Online backup remains through SQLite's backup API, not by copying only the
main database file. Before atomically naming a temporary backup or restored
database, the adapter asks SQLite to checkpoint and return that temporary
file to single-file rollback-journal mode; the published run journal switches
back to WAL when opened. Existing destination WAL sidecars block overwrite,
so a stale sidecar can never be paired silently with a new main file. A future
multi-host/network-filesystem requirement needs a separate storage decision,
not a silent fallback to rollback-journal mode.
The v4 coordinator uses the `[workflow]`-only `immutables.Map` for a
process-local, revision-specific read view of already validated journal cells.
It applies editor-minted local writes only after the existing journal CAS and
event transaction succeeds; v3 compatibility reads and public snapshots keep
their prior JSON behavior. A v4 read view cannot mutate nested state, and a
caller-owned leaf value is detached on access. This does not replace the
journal, persist an SDK state, or remove the full-state validation performed
when constructing each new `RunSnapshot`. The remaining superlinear commit
cost must be addressed separately before claiming the long-workflow target.

## Workflow definition and runtime

`WorkflowSpec` is the single versioned format for Python builders and
JSON/YAML files. Since the 0.2.0 breaking API version, both direct
`WorkflowSpec(...)` construction and `workflow()` default to
`v6-light-workflow-v4`. They require complete v4 input/output and node
contracts; the small `api` facade exports `workflow` and retains
`workflow_v4()` as a named alias. Python code that implicitly created v1 in
0.1.x must now pass `schema_version="v6-light-workflow-v1"`. Serialized
JSON/YAML/bundle definitions and persisted snapshots carry their own explicit
schema version and are never reinterpreted. V1 Agent steps contain exact
`ToolReference` values and their drivers use the legacy JSON decision
envelope. There is no automatic migration between schema versions.

`v6-light-workflow-v2` replaces each Agent-step reference with a complete,
frozen `AgentToolSpec`: a provider-compatible short name, exact
`ToolReference`, description, strict Draft 2020-12 input/output object schemas,
bounded examples, and declared business errors. A v1 step cannot contain a v2
contract and a v2 step cannot contain a bare reference. Within a step, short
names and exact references are each unique. Credential-shaped fields, remote
schema references, open object schemas, and oversized contracts are rejected
before the workflow can run.

`v6-light-workflow-v3` adds complete `NodeContract` boundaries, explicit JSON
Pointer `ValueBinding` values, workflow input/output schemas, named isolated
parallel branches, and `WorkflowExecutionPolicy`. It retains v2's complete
`AgentToolSpec` requirement. Tool arguments and Agent inputs are exactly the
validated node input; the v1/v2 `$ref` argument-template expansion is not part
of v3. Conditions read their node input, and only a condition or parallel
node's declared output crosses its branch boundary.
`NodeContract` independently freezes input/output byte budgets (1 MiB defaults),
while `WorkflowExecutionPolicy` freezes an 8 MiB default cumulative retained
input/state budget. These limits are configurable up to 256 MiB and deliberately
do not reuse the Agent tool-contract size. A file crosses steps only through an
explicit `FileReference`; the runtime never rewrites oversized JSON into a file.

`v6-light-workflow-v4` is the explicit structured-Agent evolution of v3. An
Agent decision separates its optional human `message`, schema-validated
`output`, and host-executed `tool_calls`. Each Agent declares durable request,
tool-call, context-size, token and tool-output budgets; usage is persisted so a
restart cannot reset it, and missing token usage is `unknown` rather than zero.
Every v4 tool contract also freezes trusted recovery capabilities: external
side effects, idempotency-key support, result query, cooperative cancellation,
and an optional mutually exclusive resource. These facts come from package
metadata, never from the model. `check`/`plan` flag a side-effecting tool that
cannot be queried or safely deduplicated.

All versions support sequential steps, conditions, registered subworkflows,
Agent steps, explicit approval, and versioned tool calls. v1/v2 retain their
legacy deterministic traversal of parallel declarations; v3 performs genuine
bounded concurrency and deterministic named joins. Agent loops are bounded by
`max_turns` (1--32);
unbounded autonomous recursion is not a V6 feature. A tool must be both
registered by the embedding application and present in the Agent step's frozen
scope; a model proposal outside the list is blocked before approval or
execution.

The engine exposes the same operations to the CLI and Python embedding API:

```python
from platform_lightweight_agent import InMemoryRunJournal, LocalToolExecutor, WorkflowEngine

engine = WorkflowEngine(InMemoryRunJournal(), LocalToolExecutor(), drivers={})
snapshot = engine.run(spec, {"request": "example"})
snapshot = engine.resume(snapshot.run_id, approved=True)
snapshot = engine.cancel(snapshot.run_id, reason="operator stopped the run")
events = engine.events(snapshot.run_id)
```

v3 has a separate asynchronous surface and distinguishes a cancellation
request from proven termination:

```python
snapshot = await engine.run_async(spec, {"request": "example"})
snapshot = await engine.resume_async(
    snapshot.run_id,
    approvals=[ApprovalDecision(approval_id, approved=True)],
)
requested = await engine.request_cancel_async(snapshot.run_id)
async for event in engine.stream_events(snapshot.run_id, after=cursor):
    ...
```

The synchronous `run`, `resume`, and `reconcile` methods wrap v3/v4 only when
called outside an event-loop thread. Inside an event loop the async methods are
mandatory. Legacy `cancel()` rejects v3/v4; `request_cancel()` first persists
`CANCEL_REQUESTED` and never claims that a dispatched operation stopped.

The CLI uses the same engine:

```bash
platform-agent-workflow check workflow.json
platform-agent-workflow run workflow.json --journal runs.sqlite --run-id demo
platform-agent-workflow resume demo --journal runs.sqlite --approval-id APPROVAL --approve
platform-agent-workflow events demo --journal runs.sqlite --follow
platform-agent-workflow reconcile demo ACTION --journal runs.sqlite --succeeded --output '{}'
```

An embedding host should use `RuntimeAssembly` as the one explicit assembly
boundary. It accepts the journal, injected drivers, either a default executor
or exact immutable `ToolRoute` values, policy, published subflows, neutral
runtime metadata and explicitly owned closeables. A route may name credential
references, but the resolver's values cross only the opt-in executor boundary;
they never enter a request, event or snapshot. Entry-point discovery is useful
for diagnostics, not authorization to execute a tool.
`ToolRoute.binding` lets a custom executor declare its implementation/service
version without serializing a Python closure or runtime client. Shipped HTTP,
MCP, container and Python-function executors provide this descriptor
automatically; a Python registration may override the package digest with an
explicit `implementation_version`.

`HostRuntime(RuntimeAssembly(...))` is the optional in-process owner for a
long-lived application. It reuses AnyIO's `BlockingPortal`, `CapacityLimiter`
and cancellation semantics to impose one host-wide bound on active/queued
runs, tool calls and model requests. It has a bounded overload result and a
two-mode close policy (`wait` or durable `request_cancel`). `close()` returns a
`HostRuntimeCloseReport` that distinguishes execution stopped, owned-resource
cleanup complete and the portal fully closed. One deadline covers both draining
and cleanup. A timeout carries the same report and leaves a running synchronous
hook or asynchronous cleanup task owned by the host; it never force-kills the
work or reports it stopped. Independent close hooks run in reverse order even
when one fails, and an aggregate `HostRuntimeCleanupError` identifies unfinished
indices. A later `close()` retries only unfinished hooks. Only `closeables`
supplied by the host are closed. Python callers, the local Web surface and a
custom HTTP/MCP executor can therefore share the same
`RuntimeAssembly`/`HostRuntime`; no second scheduler or service is introduced.

Host control and execution use deliberately separate bounded lanes. Approval
decisions, cancellation intent and manual action receipts first make one small
journal transition under `max_control_operations`; they do not wait for an
active-run, model, or tool slot. A single fixed AnyIO supervisor consumes a
coalesced wake and scans the journal's compact lease-free recovery projection.
It uses a fixed worker set of at most `max_active_runs`, the normal host
admission/tool/model limiters, a compact `(revision, run_id)` scan cursor, and
a bounded timer (`recovery_scan_interval_seconds`, default one second).  Thus a
lease held by a process that exited before this host started is picked up after
expiry even when no new request arrives; incompatible frozen bindings become a
process-local, inspectable `RecoveryBlock` with a backoff instead of a tight
self-wake loop or a queue head-of-line block.  The journal remains the durable
continuation backlog, not an unbounded set of background tasks or per-control
run-id notifications. A manual reconciliation
with no remaining unknown Action returns the run to `RUNNING`, making it
discoverable by that same bounded scan. This also lets a newly started host advance a previously
accepted approval, and makes a bare `resume()` mean “drive this run now” after
a restart. Thus a control response means “accepted durably”, not necessarily
“the entire run has already advanced”. Invalid IDs, conflicting decisions,
overload and provider errors are ordinary errors and never get reinterpreted
as a caller cancellation.
The host's last tool-capacity reservation happens before its final cancellation
and authorization checks and before `DISPATCHED`. For a synchronous handler,
an AnyIO abandoned wait retains that reservation until the worker thread ends;
`close(wait)` likewise waits for those physical workers before closing owned
resources.

For an authoring project, `run PROJECT --target-config target.json` assembles
the exact tool registries, model adapters, instruction files and published
subflows through `load_project_runtime()`. `serve --project ...` uses that same
runtime. Resume uses `load_resume_runtime()` and reconstructs prompts, models,
tool-registry bindings, credential-reference names, permission requirements,
tool manifests and subflows from the immutable execution manifest. It uses the
supplied project only as a trusted Python module root: mutable project
instructions, workflow files, tool catalogues or `pyproject.toml` are not
reinterpreted as run semantics. Registry imports are serialized and isolated
by module root during startup, so two generated projects using the conventional
`runtime_tools` name cannot silently reuse one another's cached Python module.

## Declarative domain packs

`v6-domain-pack-v1` is a source-only authoring contract for a business domain.
It carries a fixed domain identity/version, exact entry and subworkflow
identities, exact `ToolReference` scope, requested permissions, business terms
and applicability limits, validation requirements, parameter profiles, pinned
method/test/evaluation assets, a deterministic context-selection policy,
declared driver capabilities, and one instruction file per Agent definition.
It intentionally has no Python import path, callable registry, endpoint,
credential reference/value, model binding, executable binary, Journal handle,
or snapshot. The optional `[authoring]` dependency owns source-schema checking;
the base runtime does not load it merely to run a compiled project.

`platform-agent-workflow domain check SOURCE` is a no-side-effect validation.
`domain compile SOURCE OUTPUT` first verifies local paths, symlink boundaries,
asset digests and size limits; exact workflow/tool identities; strict local
schemas; and the declared required-check topology. It then emits ordinary
`v6-agent-project-v1` entry projects using the existing portable-project
checker and one atomic publication. Compilation never resolves or installs
dependencies, imports a tool registry, invokes a model, or runs a tool. Tool
registries are caller-supplied development metadata only; they are never read
from the domain declaration and do not grant execution authority.

Each compiled entry contains `v6-domain-lock-v1`: the selected entry, capability
tool identities, requested permissions, pinned assets, rules, context policy,
driver capabilities and compiler identity. A domain-bound run uses the explicit
`v6-execution-manifest-v3`, which embeds that credential-free lock alongside
the existing frozen tool/driver bindings. `v6-execution-manifest-v2` remains
the required protocol for ordinary projects; no old run or snapshot is migrated.
Resume reconstructs only from the frozen manifest, then re-evaluates current
host authorization. The effective permission boundary remains domain request
∩ workflow tool scope ∩ host target grant ∩ current authorization; revoking a
grant blocks future assembly/resume rather than allowing the lock to replay an
old permission.

The existing `v6-light-bundle-v2` carries a compiled lock as its declared
`domain-lock.json` project file, preserving the normal archive digest and
staging guarantees rather than inventing a second transfer format. During
project import, that conventional file becomes a domain binding only if the
strict lock decoder accepts it; the assembled workflow/tool/resource identities
and target permission/capability intersection are then checked before the
single publication rename. A malformed ordinary file with that name remains a
normal project file and cannot create domain authority.

Required business checks do not add a special state machine. The compiler
requires the existing v3 composition `check tool → condition /status == PASS
→ pass branch | blocking branch`, where the check tool has the strict
`ValidationReportV1` output contract. A report records its rule reference plus
input and argument digests. `FAIL` and `UNKNOWN` cannot take the calculation
path; a capability implementation can additionally verify those digests before
consuming a `PASS` receipt. This is domain policy expressed in a portable
workflow, not a domain-specific decision branch in the core.

The checked `examples/domain-packs/groundwater` and
`examples/domain-packs/csv-statistics` packages prove the first integration
boundary: a controlled measurement flow and a non-GIS statistics flow compile
to ordinary projects and can run/recover through the same V6 engine. Groundwater
still has illustrative capability identities. CSV statistics ships a real,
versioned capability wheel and exact tool manifests; its clean-install,
approval/restart and result-file paths have executable tests. Automatic model
switching, model-driven plugin installation, a second recovery owner and a
marketplace remain outside this lightweight contract.

Web 也不是基础依赖。安装 `platform-lightweight-agent-core[web]` 后，CLI 可用
同一个 SQLite journal 和同一套 `WorkflowEngine` 启动一个仅限本机的检查/审批
界面；运行 v3 时同时安装 `[workflow]`：

```bash
V6_LOCAL_WEB_SESSION_TOKEN="$LOCAL_TOKEN" \
  platform-agent-workflow serve --journal runs.sqlite --tool-registry package.module:configure
```

v2 Web 路由（`/v2/...`，并保留原本机别名）异步调用 engine，提供
`POST /runs`、`GET /v2/runs` 的稳定 cursor 分页、读取 summary/detail/events、带
`approval_id` 的批准或拒绝、返回 `202 CANCEL_REQUESTED` 的 v3 取消、人工对账，
以及基于持久事件游标的 SSE 流。`[web]` 复用 `sse-starlette` 的断连检测、
心跳和发送超时，而不是自行维护 ASGI 流循环；它不拥有 V6 的恢复状态或游标。
SSE `id` 是编码后的实例绑定 `EventCursor`，重连优先验证
`Last-Event-ID`，与查询 cursor 冲突时明确拒绝。终态且 cursor 已追平的新连接
返回 `204`；已建立连接收到 `v6-terminal` 后由客户端关闭。心跳是传输层注释，
不写入业务 Journal。响应开始前验证运行、实例和 cursor（`404`、`409` 或
`422`）；响应开始后只发送可识别的 `v6-stream-error` 或关闭，绝不尝试改写 HTTP
状态。
CLI 固定只允许 `127.0.0.1`、`::1` 或 `localhost`；每一个控制请求还必须带
`X-V6-Local-Session`，本机 app 同时拒绝不受允许的 `Host` 和 `Origin`。如需远程
访问，宿主应用必须自行提供认证、审计和网络边界，不能把这个开发者 Web 表面直接
暴露出去。
同步 journal 查询通过 AnyIO 的有界 worker-thread 路径运行，不能阻塞 ASGI
事件循环；普通同步 I/O 并不会因为位于 `async def` 中而自动变成异步
（[FastAPI async guidance](https://fastapi.tiangolo.com/async/#other-utility-functions)）。
请求 JSON 默认上限 1 MiB；完整 snapshot detail 默认上限 4 MiB。超限时调用者
应使用无业务 payload 的 summary/explanation、分页 events 或显式文件引用。

`SQLiteRunJournal` 额外实现了可选的 `RunQuery`，而 `RunJournal` 本身没有因此
变成数据库或列表协议。其投影在与 snapshot/event 相同的 SQLite transaction 内更新，
只含 run/execution ID、workflow ID/version、状态、创建/更新时间、待审批数及短失败码。
排序是 `(created_at DESC, run_id ASC, execution_id ASC)`，cursor 因此不会因新运行插入而
跳过旧项；自定义 journal 可以没有该能力，HTTP 则明确返回
`RUN_QUERY_UNSUPPORTED`。CLI 的 `platform-agent-workflow runs` 与 HTTP 使用同一投影。

`/console` 是随 wheel 一起发布的静态 HTML/CSS/JS，不需要 Node、CDN 或单独的 UI
状态服务。它将模型/工具内容写为 text node，不使用持久浏览器存储；操作者手动输入的
local-session token 只保留在页面内存并仅作为现有控制 header 发送。它展示列表、详情、
待审批和已持久化事件，所有批准、取消和对账仍只调用 kernel 的 API，因此浏览器不拥有
恢复、action 或审批状态机。当前没有 HTTP 文件下载面；若以后增加，只能从 run 所引用且
由 `FileWorkspace.resolve_published()` 摘要验证的不可变产物读取。

`GET /v2/runs/{run_id}/view` 从同一持久化 revision 返回执行状态、最终输出、解释和
步骤进度；SQLite 在一个读事务内获取这些字段，只解析最终输出需要的值，不恢复全部
历史 payload。SSE 只提示刷新，不是进度权威。控制台为每次选择创建独立会话，检查会话
身份、请求序号和 revision，因此 A→B→A、旧错误和已关闭 SSE 的迟到回调不能覆盖当前
视图；控制操作绑定原运行实例，详情未就绪时不可用。业务结果仅来自输出 Schema 显式
要求的 `business_status` / `reason_code` 字段，不能把 `SUCCEEDED` 推断为业务通过。

For local tool execution, a developer explicitly passes a trusted Python
registry (`--tool-registry package.module:configure`).  Models cannot register
tools, install plugins, relax filesystem permissions, or turn on arbitrary
command execution.  A registry registers only exact `(tool_id, version,
package_digest)` references.  It is intentionally a separate check from the
workflow's Agent tool scope.

The standard project/runtime loader uses `FunctionToolExecutor`, a small
composition of the existing sync and native-async function executors.  The
same registry and verified wheel entry-point protocol accept `def` and `async
def` handlers; only the invocation boundary changes.  An opt-in second
handler parameter (`ToolContext`) exposes the stable action ID, cooperative
cancellation, and an optional host-owned `FileWorkspace`; it deliberately
does not expose journal handles, credentials, workflow state, or general
filesystem paths. `FileWorkspace.record_output(..., action_id=...)` copies a
completed action-isolated staging file into a content-addressed publication.
The returned `FileReference` can be resolved as the next input directly, and
the action publication index lets a recovery handler reuse an already
published artifact. File publication precedes journal result recording and is
idempotent, but it is not falsely described as a cross-resource transaction.
`published_manifest()` is built only from completed publication indexes and
revalidates every immutable blob. `staging_manifest()` separately exposes
temporary and partial files for diagnostics/cleanup; `output_manifest()` is a
compatibility alias for the formal published manifest, never a staging scan.

`RemoteHttpToolExecutor`、`McpToolExecutor` 与 `ContainerToolExecutor` 是可选的
执行边界，不是默认运行时服务。它们同样必须在宿主启动时以精确
`ToolReference` 注册：

- Remote HTTP 只接受固定 HTTPS endpoint（开发测试可使用 loopback HTTP），携带
  稳定 `Idempotency-Key` / `action_id`；超时、5xx 或无法验证的回执会进入
  `RECONCILING`，不会盲目重发。若配置了同源的查询 endpoint，恢复会用原
  `action_id` 对账。响应使用严格 JSON 解码；Python 默认接受的 `NaN`/
  `Infinity` 也被视为损坏回执，而不是确定业务失败。
- MCP 适配器只调用宿主注入、已审核的 server/tool pair，不发现工具、不安装 server，
  也不把 MCP 凭据带入流程输入或 journal。标准转换优先使用
  `structuredContent`，保留非结构化 `content`，并识别 `isError`。由于工具错误
  本身不能证明没有副作用，默认仍是结果未知；只有定义显式声明
  `tool_error_semantics="known_failure"` 时才生成稳定失败回执。连接或协议异常
  始终保持未知。仅传递 `action_id` 不代表远端承诺幂等或提供结果查询。
- Container 适配器只把固定 digest image、固定 command 和 JSON 参数交给宿主注入的
  隔离 runner；包本身不执行 Docker/Podman shell 命令。runner 负责网络、挂载、资源
  隔离并以原 `action_id` 对账。

这三个适配器不增加基础依赖，也不允许模型提供 URL、镜像、命令、凭据或插件包。
因此“可扩展”仍保留 V6 的安全默认值，而不是开放任意远程执行。

## v3 concurrency, validation, and recovery

The optional `workflow` extra pins `anyio==4.15.1` and
`jsonschema==4.26.0`. AnyIO owns task groups, cancellation scopes, capacity
limiters, worker-thread adaptation, and asynchronous polling. `jsonschema`
plus `referencing` compiles every Draft 2020-12 schema and local reference
before `RUN_STARTED` is written. The base import does not import either
dependency.

Preflight resolves binding visibility along each definite path, rejects
forward and cross-branch references, checks required targets, and rejects
explicit JSON type mismatches. Runtime validates input before a node starts
and output before it is journaled or visible downstream. Validation events
record only a stable code, instance path, and schema keyword; rejected values
are not copied into diagnostics.
The same boundary enforces the frozen byte budgets. Input overflow fails before
dispatch. Output overflow after dispatch closes the Action as `OUTPUT_INVALID`
and blocks with `STEP_OUTPUT_TOO_LARGE` (or the existing Agent-specific code),
so it is never reclassified as a retryable business error. Cumulative overflow
uses `RUN_RETAINED_STATE_TOO_LARGE`; the rejected payload is not retained.

One run-level `CapacityLimiter` and each parallel node's local limiter bound
leaf work. Orchestration does not consume a capacity token, so nested parallel
nodes cannot deadlock by holding capacity while waiting for children. Branches
receive detached JSON inputs and output spaces. A parallel join is assembled
in declaration order as `{branch_name: branch_output}`. `fail_fast` cancels
siblings cooperatively; `wait_all` lets every branch settle. Neither publishes
a partial join after a failed branch.

Every journal transition is serialized through a short coordinator section.
An action's `DISPATCHED` transition must commit before the executor is called.
The v3 snapshot additions are JSON branch state, run and active-node deadline
data, cancellation intent, and pending approvals. Tasks, streams,
`CancelScope` values, and framework objects are recreated, never persisted. An
already completed node or resolved action is skipped during reconstruction.
SQLite stores immutable workflow/execution definitions separately from compact
mutable snapshots. A verified canonical definition cache follows a
state-only `RunSnapshot.next_revision()` and the in-memory journal's defensive
copy, so neither path re-scans, re-copies, or reserializes an unchanged
workflow/manifest tree on every event. Mutable checkpoint sections keep a
separate, token-protected canonical JSON representation: each changed state is
still serialized and credential-scanned before CAS, while the journal-owned
defensive copy and the next transition decode that validated representation
instead of scanning and serializing the same tree again. A caller cannot use a
mutable public mapping view to alter either authority in a later revision.
Event atomicity, CAS, and credential-shaped-field checks remain at their
existing boundaries.

Cancellation has three distinct observations. `CANCEL_REQUESTED` records
intent and is not terminal. `CANCELLED` means all local work stopped and there
is no unknown external result. A dispatched operation that cannot prove a
result is absent produces `RECONCILING`; an async executor may use
`ToolExecutionStoppedError` to prove it stopped. Sync executors run through an
AnyIO worker-thread adapter and receive the live run/step token in
`ToolContext`, allowing cooperative exit. AnyIO abandonment alone cannot stop
a Python thread, so cancellation or timeout after dispatch remains unknown
until the handler actually proves it stopped or the action is reconciled.
Step timeout uses a child token which observes run cancellation without
cancelling sibling steps. Terminal precedence is unknown external result, cancellation,
failure/timeout, approval wait, then success. Node activity timeout is paused
while approval is pending, while the run deadline continues to advance.

`stream_events` treats the journal as the source of truth. It resumes from an
instance-bound `EventCursor(execution_id, sequence)` and uses short polling,
which also observes events written by another process; an in-memory
notification is never required for correctness. A legacy bare sequence is
only a convenience for the *current* same-name run. A cursor that carries an
old execution identity fails with `RUN_EXECUTION_MISMATCH` rather than silently
returning an empty page after retention has allowed a `run_id` to be reused.

## Execution hardening and reusable cores

The recovery and concurrency fixes intentionally remain within the existing
journal contract; they do not add a second scheduler, a daemon, or a shadow
state machine.

- An approval submission is a durable control-plane decision.  It is
  idempotent, returns after persisting that decision, and never competes with
  the active executor.  The one persisted owner subsequently consumes it, so a
  decision made while a sibling parallel branch is still active cannot leave an
  empty `WAITING_APPROVAL` state behind.
- Action facts are monotonic.  A confirmed `RESOLVED` receipt wins over a late
  executor exception, cancellation observation, timeout, or invalid late
  receipt; those late observations become diagnostic events and cannot reopen
  an action as `RECONCILING`.
- Reconciliation observes every dispatched/unknown action in a bounded round,
  not only the first one.  Each result query and the whole round have separate
  AnyIO time limits.  The action records query count, latest observation and a
  next-query hint, while an unsupported, timed-out, cancelled or failed query
  remains unknown and is never redispatched.  Recovery remains available after
  a normal run deadline because it only observes an already-dispatched effect.
- v4 persists a model-invocation ledger (`RESERVED` → `DISPATCHED` → usage
  confirmed or unknown) before each provider call.  A crash after dispatch
  therefore cannot turn unobserved token usage into zero or reset a token
  budget.  Context limits use the selected adapter's effective binding, so a
  project router's actual instructions are counted rather than its small
  wrapper metadata.
- Queued model and tool dispatches re-check durable cancellation and deadlines
  after acquiring capacity.  AnyIO propagates cooperative cancellation to an
  active model task; a tool resource mutex is process-wide and remains borrowed
  until the real task has returned a result or a stopped proof. The mutex
  registry uses a standard-library thread condition rather than an AnyIO
  primitive because separate `HostRuntime` portals own separate event loops.
  An abandoned synchronous worker cannot be mistaken for a released mutable
  resource. Idle entries are removed only when no holder or registered waiter
  remains; an unknown dispatched Action deliberately retains its lease.
  `HostRuntime.diagnostics()` reports registered resources, holders, waiters and
  incomplete cleanup count without exposing workflow values.
- SQLite connections use `contextlib.closing`, and the journal exposes a small
  control projection (revision, cancellation flag, owner generation and lease)
  for active-run polling.  The coordinator only reloads full snapshots after a
  revision change; immutable preflight validators are kept in a bounded cache
  keyed by compiler protocol, selected root identity/digest and complete
  catalogue. Neither optimization changes the persisted
  `DISPATCHED`-before-call boundary.

An Agent has two distinct identifiers.  `(workflow_id, version, step_id)` is
the immutable definition address used for instructions, model bindings and
tool scope.  The run-local step key (for example
`call/subworkflow/answer`) identifies one executing instance.  This separation
is carried by v1--v4 scheduling, project routing and bundle v2.  A legacy short
step name remains a convenience only when it identifies exactly one Agent
definition; two child workflows may both define `answer`, but then their
instruction and target bindings must use `workflow@version:answer`.

The end-to-end invariant suite combines deterministic fault cases with
Hypothesis stateful tests that restart a real `WorkflowEngine` over the same
SQLite journal.  It exercises approvals, cancellation, lost receipts,
reconciliation and fact monotonicity rather than merely copying a Python state
object.  This follows the reference-model style documented by
[Hypothesis stateful testing](https://hypothesis.readthedocs.io/en/latest/stateful.html).

These choices deliberately reuse the mature pieces that own the hard generic
problems: [AnyIO](https://anyio.readthedocs.io/en/stable/) owns task groups,
capacity limiting, cancellation and worker-thread abandonment;
[jsonschema](https://python-jsonschema.readthedocs.io/) owns Draft 2020-12
validation; and Hypothesis owns stateful test generation.  V6 retains only the
domain-specific parts that those libraries cannot decide: exact action identity,
approval, external-result uncertainty, journal ordering and portable target
bindings.

We evaluated `pydantic-graph==1.107.9` but do not use it as the v3 runtime or
persistence owner. Its builder is a useful reference for typed transitions,
fork/join structure, and async iteration, but parallel tasks share mutable
state, capacity is not bounded by the graph, and the current builder does not
provide graph-state snapshots. The deprecated graph persistence surface also
does not match V6 approval pauses and action reconciliation. In the isolated
evaluation it installed 13 distributions (about 9.97 MiB), an eight-way map
reached eight active tasks without an internal limit, and an explicit
`int -> dict` edge built successfully but failed only during execution. The
legacy persistence API emitted a deprecation warning. Those constraints
are documented by the upstream
[GraphBuilder](https://pydantic.dev/docs/ai/graph/builder/),
[parallel state sharing](https://pydantic.dev/docs/ai/graph/builder/parallel/#state-sharing-in-parallel-execution),
and [persistence limitations](https://pydantic.dev/docs/ai/graph/builder/#persistence-and-resumability)
documentation. V6 therefore uses the smaller AnyIO and JSON Schema cores while
retaining ownership of approval, action IDs, journal ordering, and recovery.

## Runtime ownership

The package journal is the sole recovery owner. No optional framework may
checkpoint, replay, or advance the same run, and external action identities do
not replace the journal's `RECONCILING` boundary. Any future runtime change
must be introduced as a new-run-only contract; existing runs are drained by
their original owner without dual write or shadow execution.

## Drivers and recovery

Framework-neutral prompt, schema and usage helpers plus the scripted driver
live in `_driver_common.py`; the PydanticAI and Agents SDK adapters each have
their own module. `drivers.py` remains a compatibility re-export for the
documented driver names, so frozen driver version strings do not change. The
Import Linter boundary includes all three implementation modules. This is a
source-ownership split only: neither adapter receives journal ownership or a
new SDK checkpoint, and framework packages are still imported only when an
adapter is used.

`PydanticAIDriver` is the recommended v2/v3/v4 adapter. It builds a fresh
`ToolDefinition`/`ExternalToolset` for every decision, includes declared return
schemas, and uses PydanticAI's `ToolOutput`, `DeferredToolRequests`, validation,
`UsageLimits`, and retry machinery. A small shared `jsonschema` bridge replaces
the external toolset's permissive argument validator and validates v4 final
output through Pydantic's output-schema hook; the framework receives one
native correction opportunity, but it never executes a business tool.
For a deferred business tool, adapter protocol v3 persists only PydanticAI's
officially serialized message JSON plus the opaque model `tool_call_id`. The
kernel binds that ID to its own durable `action_id`; after approval and result
validation, the next independent adapter instance reconstructs
`message_history` and `DeferredToolResults` through
`ModelMessagesTypeAdapter` and `DeferredToolRequests.build_results()`. This is
the native `assistant(tool_call) -> tool(result)` protocol, not a prose/JSON
retelling in a new user message. A changed driver version, malformed call
binding, incomplete result batch, or invalid serialized message fails closed as
`AGENT_CONTINUATION_INVALID`. When the durable tool-call budget is exhausted,
the adapter exposes no business tool on the resumed request.

`OpenAIAgentsDriver` provides the equivalent v2/v3/v4 contract for the Agents SDK. It
clones the configured Agent for each decision, removes preconfigured tools,
MCP servers, and handoffs, then injects strict `FunctionTool` definitions with
output schemas, pre-approval input guardrails, and mandatory approval. Every
handler is fail-closed. The SDK interruption is converted to the public
`ToolCall`; the adapter never approves or resumes SDK state.

v2/v3 use a typed `FinalReply`; v4 exposes the node's exact output Schema
through PydanticAI's pluggable Pydantic schema protocol or the Agents SDK's
`AgentOutputSchemaBase`. Both adapters translate all
results into the same framework-neutral `AgentDecision`. v3 invokes their
native asynchronous entry points (`Agent.run` and `Runner.run`); it does not
put a synchronous SDK loop inside the AnyIO runtime. The v2/v3 prompt carries
only task input, workflow values, and neutral history. Exact package identity
is resolved from the short name inside the adapter and remains host state.
If a provider emits a final reply and business-tool requests together, the
business-tool requests win and the public decision has an empty message.
Examples and declared error semantics are rendered once into tool descriptions;
input and output schemas use the frameworks' native tool fields. Successful
tool outputs are checked against the frozen output schema before another model
call; invalid contracts, model output, and tool output fail closed with stable
codes `AGENT_TOOL_CONTRACT_INVALID`, `AGENT_MODEL_OUTPUT_INVALID`, and
`AGENT_TOOL_OUTPUT_INVALID`. Declared business errors are model guidance only;
`model_retryable` never asks the kernel or executor to replay a business action.
Provider and model-timeout failures are normalized to
`AGENT_PROVIDER_FAILURE`. An optional `AgentInvocationUsage` observer receives
only neutral request/token/duration/provider/model fields; live SDK result
objects are neither journaled nor attached to snapshots. The PydanticAI
adapter's versioned continuation is detached JSON validated by the pinned SDK
type adapter; it is not an SDK object and is accepted only by the exact frozen
driver binding. v4 copies only the neutral
counters needed for its durable budget ledger, so a restart cannot reset limits
without persisting an SDK object.

For compatibility, v1 keeps the existing JSON-envelope behavior and the custom
`runner` test/embedding hook. The custom runner is deliberately unavailable to
v2/v3/v4 so tests and production cannot bypass framework tool parsing and validation.
The base package remains dependency-free; both framework extras include pinned
`jsonschema==4.26.0`, with `pydantic-ai-slim==1.107.9` and
`openai-agents==0.22.3` pinned independently.

The SDK never owns approval, action IDs, idempotency, journaling, execution, or
recovery. After a decision, all framework run objects are discarded. The
journal retains neutral tool-result facts and, for PydanticAI deferred calls,
the versioned serialized message continuation needed to preserve the provider's
call/result correlation. Snapshots and events contain no live SDK checkpoint,
agent, task, client or run object. Agents SDK approval interruptions continue
through neutral history; the adapter never saves or resumes an SDK run state.

The adapter boundary follows the frameworks' documented ownership model:
[PydanticAI output validation](https://pydantic.dev/docs/ai/core-concepts/output/),
[Agents SDK agent definitions](https://developers.openai.com/api/docs/guides/agents/define-agents),
and [Agents SDK guardrails and approvals](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals).

Each run saves an immutable `v6-execution-manifest-v2` containing the root and
all transitive subflow definitions, tool package identities, per-used-tool
execution target bindings, full driver/model/prompt bindings, policy version
and compatible Python runtime. HTTP freezes service/path/query identity, MCP
freezes server/tool/version-or-schema identity, and container freezes image
digest, command digest, execution-config version and any declared
`ContainerResourceLimits`. A strong-limit container registration and every
dispatch call the reviewed runner's capability probe; lack of CPU/memory/PID
cgroup enforcement rejects the action as
`RESOURCE_LIMIT_ENFORCEMENT_UNAVAILABLE` before container creation. Merely
accepting Docker flags or echoing HostConfig is not capability evidence.
Credential values and
unused registered capabilities are excluded. A resume refuses a changed or
missing binding before consuming an approval or dispatching an Action. A
ported workflow may choose new bindings only by starting a new run. Snapshots
with the previous manifest protocol cannot infer these identities and therefore
fail closed under this runtime rather than silently rebinding. Existing SDK checkpoints are never exported or translated:
switching framework creates a new run, while the original driver/version is
required to finish an existing one.

Before a tool is invoked, the journal commits a stable `action_id`.  If a
process dies after dispatch and the executor cannot prove the external result,
the run moves to `RECONCILING`; it is not blindly sent again.  An executor can
return the prior result through `reconcile`, or an operator can record the
confirmed result with `WorkflowEngine.reconcile`.
Custom trusted executors signal this condition with the public
`ToolExecutionUncertainError`; `LocalToolExecutor` retains that identity as
uncertain and rejects any later first-invoke attempt.  A manual reconciliation
can only settle an Action currently in `RECONCILING`, never overwrite an
already completed business fact.
When a handler can prove a normal negative business outcome, it returns a
failed `ToolExecutionResult` or raises `ToolExecutionFailedError` with a stable
code. Arbitrary handler exceptions are never treated as such proof: after the
side-effect boundary they become uncertainty, while a rejected output is kept
out of the journal as `TOOL_OUTPUT_INVALID`.
External adapters preserve the same four facts end to end: rejection before
dispatch, explicit success/failure receipt, proven stop, and unknown outcome.
Parser/client diagnostics are reduced to stable codes separately; they never
manufacture a failed business receipt. This matches Python's documented need
to reject non-finite constants explicitly and MCP's distinction between
protocol errors and `isError` tool results:
[Python JSON](https://docs.python.org/3.12/library/json.html#infinite-and-nan-number-values),
[MCP tool errors](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#error-handling).

Third-party executors can reuse the dependency-free helpers in
`platform_lightweight_agent.testing`: replay/conflict checks, outcome
classification, result-query-without-redispatch, distinct target bindings and
credential-free serialization. They use ordinary assertions and are designed
for pytest parametrization without adding pytest to runtime dependencies.

One persisted executor generation owns advancement of a run. Heartbeats extend
its SQLite lease, control clients may only submit cancellation, approval or
reconciliation, and action settlement is a conditional transition. A duplicate
identical result is idempotent; a different late result is recorded as a
conflict and cannot overwrite the first confirmed fact or any terminal output.
Approval submission is deliberately separate from execution advancement: an
approval arriving while another branch is active is stored idempotently and
returned to the caller without competing for the executor lease. The existing
owner re-reads pending decisions before it publishes `WAITING_APPROVAL`.

All Action completion paths use the same conditional transition helpers.
Confirmed `RESOLVED` facts cannot be reopened by a late transport exception,
stop, rejection, invalid receipt, timeout, or cancellation; a late problem is
diagnostic only. Recovery reconciles every already-dispatched Action before a
workflow failure is allowed to stop new node execution. A `mutex_resource`
uses one process-scoped, thread-safe registry lease keyed by stable `action_id`;
it never shares an asyncio/AnyIO synchronization primitive between host event
loops. The lease remains borrowed while a dispatched synchronous task has an
unknown outcome and is released only by a confirmed settlement. Entries with
no holder or waiter are reclaimed immediately. Cross-process locking is
intentionally not claimed by this contract.

v4 persists a small model invocation ledger (`RESERVED`, `DISPATCHED`,
`USAGE_CONFIRMED`, `USAGE_UNKNOWN`, or `NOT_DISPATCHED`). A crash or timeout
after dispatch marks usage unknown rather than zero, and a configured token
ceiling fails closed instead of replaying that request. The ledger separates
accepted Agent decisions, SDK model requests, and provider transport attempts:
unknown telemetry is recorded as unknown, never inferred as zero. PydanticAI
also receives its native `UsageLimits(request_limit=remaining)` fence, so a
framework repair retry cannot cross the durable SDK-request budget. Model
capacity queues repeat the durable cancellation/deadline checks immediately
before dispatch; active async SDK requests run inside the existing AnyIO
cancellation scope.
Project drivers expose the selected nested adapter binding for context-budget
measurement, and Agent configuration lookup uses the definition address
`(workflow_id, version, step_id)` rather than the runtime instance path.

可导出的 snapshot 与事件只保存稳定错误码和取消原因码，不保存异常消息或调用方
提供的取消文本；需要人工说明时，由宿主的受保护审计系统另存。

## Portable transfer

The existing `v6-light-bundle-v1` remains the default and keeps its original
workflow/tool/dependency/file behavior. `--format v2` is an explicit project
export. `v6-light-bundle-v2` adds a PEP 751 `pylock.toml` and
`v6-portability-contract-v1`: every Agent step carries its instruction text,
strict output schema, complete `AgentToolSpec` scope, source-driver hint and
hard `max_turns`; every referenced subworkflow carries an exact definition
digest and its input/output schemas when available. v1 Agent workflows cannot
be promoted because their three identity fields cannot reconstruct that
contract.

Every v2 payload has a SHA-256 and size in the manifest. The reader validates
all payloads, the 64 MiB uncompressed limit, the 4,096-member limit (including
the manifest, lock and other internal members), path safety, local-only
schemas, exact tool coverage, PEP 751 syntax, immutable dependency sources,
and agreement between the workflow and portability metadata. The writer uses
the same envelope/layout checks, writes an owned sibling temporary archive,
re-reads it through the normal parser, then publishes it with
[`os.replace`](https://docs.python.org/3.12/library/os.html#os.replace).
Consequently a failed compression or validation leaves a prior valid archive
unchanged. Runtime snapshots, approvals, actions, journals, credentials,
callables and SDK checkpoints remain excluded.

`v6-agent-project-v1` in `pyproject.toml` binds the workflow, tool catalogue,
instruction files, published subworkflow definitions, explicit files,
evaluation cases, trusted runtime registries and dependency lock. The optional `[authoring]` extra uses
Copier's safe renderer for bundled workflow/tool templates; it does not enable
tasks, migrations, custom extensions or remote templates. Dependency
resolution, PEP 751 export, target dry-run and wheel construction are delegated
to the repository-pinned `uv`, rather than duplicated in V6. `check`, `plan`,
`lock`, `export`, `diagnose`, `import` and the equivalent Python APIs all use
the same declarative kernel. The release gate builds an actual wheel, installs
it into an isolated interpreter with `[authoring,workflow]`, initializes both
templates, and performs CLI check, approval/restart and bundle
export/import/re-run. This prevents source-tree template files from masking a
missing package-data rule.
`import --as-project` and `import_bundle_project()` close the definition-to-run
gap for a compatible bundle: they materialize the checked project metadata,
Agent instruction files, target configuration and an empty evaluation set.
They do not install or copy executable tools. Tool registries and exact local
subworkflow definitions remain explicit target-owned bindings; a child that
contains its own Agents must be assembled through its own published project.
Both generic and project import first extract into operation-owned sibling
staging directories. Project assembly writes its generated metadata there and
runs the ordinary project checker before one atomic publication; a failed
import does not leave a half-project or modify an existing target. The archive
transport prefix `files/` is private to the bundle: an authored input named
`files` is still a valid project path after assembly.

`v6-execution-plan-v1` is a no-side-effect view of bindings, branches, bounded
parallel policy, approvals/actions, Agent tools and turns, and subworkflow
requirements. `v6-portability-report-v1` distinguishes `compatible`,
`binding_required`, `incompatible` and `invalid`; a v2 import extracts nothing
unless the target report is `compatible`. Model credentials are target-owned
and never appear in `PortableTarget`.

`diagnose` now reports three intentionally separate checks: the portable
contract match, whether `uv pip --dry-run` can install the PEP 751 lock for the
selected interpreter, and (only with `--check-environment`) whether that
interpreter is ready now. A successful dry-run is confirmed compatibility; a
solver conflict is confirmed incompatibility; timeout, network, launch and
otherwise ambiguous failures are stable, fail-closed `binding_required`
diagnostics rather than false incompatibility claims. No command output or
credentials is copied into those diagnostics.

The environment check has explicit evidence layers. Its default fixed metadata
probe imports only provider modules declared by frozen driver bindings and
checks a development delivery's **top-level** module. It intentionally does
not use a dotted `find_spec`, because Python may import a parent package while
locating a submodule ([`importlib.util.find_spec`](https://docs.python.org/3.12/library/importlib.html#importlib.util.find_spec)). Thus it cannot claim an
unverified `MODULE:CALLABLE` development delivery ready. An operator can pass
`--verify-development-entrypoints` together with `--check-environment` (or
`verify_development_entrypoints=True` to the Python API) for host-trusted code;
that opt-in first checks whether the full declared module can be located, then
imports it and verifies that the named attribute is callable, without
registering or invoking the tool. Import-time code can still run, which is why
this option is limited to host-trusted code. Reports keep top-level presence,
full-module location, callable loading, registration and business invocation
as separate facts. `inspect_target_environment()` is the equivalent Python API.
It infers AnyIO and JSON Schema requirements from a v3/v4 workflow schema,
then checks declared driver distributions/versions and optional entry points,
release-tool distributions/entry points and wheel SHA-256 values. A release
`ToolDelivery` must connect the exact `ToolReference` to its distribution,
entry point and wheel artifact. Installed `RECORD` metadata is reported as
additional inventory evidence, but neither a version nor an archived wheel
hash alone is claimed to prove arbitrary loaded source code. Credential names
can be checked through an injected resolver without exposing their values.
Discovering an entry point never authorizes it:
`bind_release_tool_delivery()` is itself an explicit host-binding operation.
It verifies the target-configured wheel and sidecar first, then gives the
loaded entry point a registrar that can bind only that exact `ToolReference`;
ordinary `RuntimeAssembly` routes remain explicit host authorization.

`WorkflowCompilationContext` is the single checked definition graph used by
project `check`, `plan`, and runtime assembly. It contains the root workflow and
the exact `(workflow_id, version, definition_digest)` child catalogue; callers
do not reconstruct the subworkflow set independently. The execution plan lists
each child definition and its own step plan, so nested approval boundaries and
tool/Agent requirements remain visible while `effects` stays `not_executed`.

`ScriptedAgentDriver` is the dependency-free offline V4 path. The versioned
`v6-scripted-decisions-v1` file keys decision lists by
`workflow@version:step`; durable `AgentContext.turn`, rather than a process
cursor, selects the decision after restart. Its async method has an AnyIO
cancellation checkpoint and reports zero-token, zero-network simulated usage.
The frozen driver binding and CLI result both mark the run simulated. This
tests contracts, tool routing, approval and recovery, not model quality or SDK
provider compatibility. Generated workflow projects execute that complete
SQLite pause/rebuild/resume path in their default tests.

CLI automation may opt into `--strict-exit`. Codes 10--16 distinguish blocked,
approval wait, reconciliation, cancellation requested, cancelled, still
running and recovery pending; the compatibility default remains zero when a
command successfully returns a snapshot. Errors and blocked-run sidecars use
`v6-cli-diagnostic-v1` with stable code, file/line/column when known, node,
JSON Pointer, validation keyword and a repair hint. Rejected values, prompts
and credentials are never copied into this diagnostic. YAML parser exceptions
are converted at the same boundary as JSON syntax errors, while unexpected
programming `RuntimeError` values are allowed to escape instead of being
mislabelled as missing optional dependencies.

A bound PydanticAI model also freezes its provider runtime dependency and an
actionable install hint. The provider-neutral `[pydantic-ai]` extra stays slim;
`[pydantic-ai-anthropic]`, `[pydantic-ai-openai]`, and
`[pydantic-ai-google]` opt into one provider family. Environment readiness
reports distribution metadata, an actual provider import, environment-aware
local driver/model construction, and `model_network: not_checked` separately;
local metadata is never presented as runtime or network readiness. The
construction probe creates the SDK adapter but sends no request.

The two built-in adapters remain the default project path. An embedding may
authorize another implementation with a `DriverFactory` mapping passed to
`load_project_runtime()` and `load_resume_runtime()`. A factory declares an
exact adapter version, capabilities and provider dependencies. The resulting
JSON execution binding, including frozen configuration, remains in the normal
run manifest and resume requires the identical target/factory version. Entry
points can locate code, but do not authorize it; the CLI requires an explicit
`--driver-factory MODULE:OBJECT`.

Local files cross a tool boundary as `FileReference` values
(`logical_id`, SHA-256 and byte size), never as ambient paths. A host-owned
`FileWorkspace` stages verified inputs under a dedicated root and records
regular outputs under a separate root; it rejects traversal and symlinks and
does not change the process working directory. File resolution is explicit in
the trusted handler, so the Agent kernel never interprets arbitrary paths.
For an imported bundle, `bundle_file_references(manifest.json)` plus
`FileWorkspace.stage_declared_inputs(project_root, references)` understands
both v1's file table and v2's signed archive entries, checks the archived
digest before staging, and returns resolvable references after the embedding
process has changed directories.

V7 remains unchanged in this delivery. A V7 target containing Agent or
subworkflow requirements always receives `TARGET_IMPORTER_SUPPORT_REQUIRED`
and `binding_required`; a report must not infer parity from a matching bundle
version. Exchange imports definitions for new runs only. Existing runs retain
their frozen workflow and driver versions and are never migrated.

Offline framework tests use PydanticAI `TestModel`/`FunctionModel`, Agents SDK
`agents.testing.ScriptedModel`, and real provider adapters over
`httpx.MockTransport`. Live evaluation is separately opt-in through
`--allow-model-requests`; the `[evaluation]` extra uses the version-matched
Pydantic Evals Dataset/Evaluator/report core and records completion, tool
selection/arguments, latency, token usage and estimated price in
`v6-agent-eval-report-v1`. Missing usage or catalogue prices are `unknown`,
never zero, and live reports are not release gates. The bundled cases cover a
direct answer, a distracting tool name, parallel multi-tool selection,
multi-round result feedback and an approval/restart boundary. Simulated tool
results cross that boundary only as neutral JSON; the evaluation never lets an
SDK handler execute a business tool.

## Journal cost controls and observability

SQLite stores the immutable workflow and execution manifest once by SHA-256.
Large inputs, Action results, node outputs and final Agent outputs use a
content-addressed value table, so the same validated JSON payload is written
once even when recovery state refers to it from several places. Public
snapshots are transparently hydrated and contain no storage references. Value
records, mutable state and the event still commit in one SQLite transaction, so
this normalization does not introduce dual-write recovery. Event reads are
bounded pages, `RunStatus` avoids parsing a full snapshot during idle polling,
and `RunControl` projects only cancellation, executor generation/owner and
lease for the 50 ms control monitor. Every short-lived SQLite connection is
closed deterministically with `contextlib.closing`; Python's connection context
manager is used neither as an assumed close operation nor as a connection
pool. Streams use cursor catch-up plus exponential idle backoff. Each event
includes a persisted timestamp and payload byte size; step completion and
dispatch events add duration and capacity-queue time.
The private `$v6_storage` envelope is interpreted only in those exact compact
snapshot slots. Journal code never recursively searches business JSON, so a
workflow schema and value may safely use the same property name or even an
object that resembles a private envelope.

A journal always retains its own defensive snapshot copy or serialized SQLite
row. After a successful write it may return the caller-owned, already validated
snapshot rather than creating an additional identical round-trip; mutating that
returned mapping cannot alter journal-owned state. This removes redundant work
without relaxing the durable copy, credential scan, or `DISPATCHED` boundary.
Changed mutable sections are checked once; unchanged inputs and driver bindings
reuse their validated canonical representation. Internal revisions carry the
immutable definition without decoding it; public workflow/manifest mappings are
detached and materialized only on demand. `scripts/measure_state_cost.py` now
measures 1–500 local v4 steps, the accumulated validated-state bytes, attempted
SQLite snapshot-row payload bytes, and an approval continuation through a fresh
engine (and reopened SQLite journal). It asserts that completed tools are not
replayed. The instrumented 500-step sample wrote about 667 MB of snapshot JSON
to a journal whose final database and WAL occupied about 2.49 MB. These are
attempted row payloads, not physical disk bytes or a release latency target.

That older measurement identified two separate costs: each transition
validates/copies the growing in-memory state, and the former SQLite row commit
rewrote it. Journal storage version 7 now removes the second cost for **new
v4 runs**: the run row freezes definition and compact metadata while
`lightweight_agent_state_cells` stores independently changed state values.
The coordinator's editor write set selects cells; an untrusted or section-wide
write takes a validated full-cell fallback. Revision CAS, changed cells and
the event commit in one SQLite transaction, including the `DISPATCHED` fact
before external execution. `get()` reassembles a public snapshot within one
read transaction; read-only operator inspection selects control cells and
extracts action metadata without hydrating retained business results. Old
v1–v4 runs already stored as snapshots remain on their original layout and
are never silently migrated. No second recovery engine or production
dependency was added.

The scaling issue is still open. `RunSnapshot` and several coordinator paths
continue validating and copying the complete mutable state each revision;
cell writes do not make that in-memory work linear. A single instrumented
500-step v4/SQLite continuation wrote about 0.58 MB of run-row JSON and
0.63 MB of cell JSON, but processed about 624 MB of validated state and took
about 74.7 seconds. These figures are attempted payloads and instrumented
wall time, not disk bytes or a release performance guarantee. The next
optimization must preserve the existing JSON/credential and recovery checks
while reducing full-state work in memory; caching alone or another recovery
system cannot meet that requirement.

The v4 coordinator now shares one detached, top-level read-only state view
across read checks of the same revision. The view is invalidated whenever the
owned snapshot changes; commits still decode their own mutable copy and pass
through the existing validation and journal CAS. Compatibility v3 retains
fresh detached reads because its Agent context may expose nested values to a
caller-owned driver. This removes redundant v4 read decodes, not the
full-state work of creating and validating each new revision. It is an
internal optimization, not a persistent state format or a public immutability
guarantee for nested JSON values.

The current v4 coordinator has begun producing process-local explicit write
sets through `state_edits.py` for the sequential tool path, approval decisions,
cancellation requests, parallel branch bookkeeping, lease ownership, workflow
output, Agent request reservation/dispatch, action proposal/result observation,
and typed final reply. Subworkflow deadlines and paused node timeouts also use
the same contract. Each write carries
an immutable canonical JSON value or a deletion fact. The editor detaches reads
and validates changed paths and values before capturing them. A development-only
RFC 6902 comparison checks that the write set reconstructs each inspected
transition. The offline SQLite cell replay now derives its cell changes from
that write set and uses the full diff only as a development oracle; neither
library is used to generate production writes.
`completed` and `approved_steps` additionally emit per-member addition facts,
so the offline cell layout does not rewrite the growing lists. This is limited
to those two monotonic lists; arbitrary JSON arrays remain ordinary values.
`RunSnapshot` never serializes the write-set hint. The SQLite journal uses an
editor-minted hint only for v4 cell-row updates; the in-memory journal and
legacy SQLite rows retain their original behavior. Remaining legacy mutable
callbacks take the validated full-cell fallback. This is a write-amplification
improvement, not a claim of lower end-to-end asymptotic cost.

Two upstream building blocks were checked before choosing that direction.
SQLite's [`json_set`](https://www.sqlite.org/json1.html) returns a new
JSON value; applying it to the existing monolithic snapshot would not provide
a per-node durable commit unit. [`immutables.Map`](https://github.com/MagicStack/immutables)
offers structural sharing for in-memory mappings, but by itself cannot turn
the current nested mutable transition callbacks into validated journal deltas.
SQLite `json_set` is not used for journal commits. `immutables` is pinned only
in `[workflow]` for the revision-specific v4 read view described above; it
does not own the durable commit or recovery protocol.
The development-only `scripts/probe_persistent_state.py` replays real v4 editor
cell writes into a pinned `immutables.Map` of canonical JSON strings and checks
every revision against the current engine. Its replay timing is isolated from
the expensive capture/verification path; it is not an alternate journal or a
claim that the production coordinator has stopped copying full state at commit.

The immutable v3/v4 preflight compiler has a bounded, content-digest-keyed
LRU cache. Within one cold compilation, identical JSON Schemas also share a
bounded `jsonschema` validator keyed by canonical schema content; workflow
visibility and binding checks still run for each definition. This reduces
repeated schema setup, not per-revision state validation.
`preflight_cache_metrics()` exposes only process-local entry/hit/miss
counts, and `scripts/measure_v3_v4_overhead.py` measures cold versus warm
compilation plus real 1/10/40/100-step local execution without starting an
external service. Its per-case `tracemalloc` probe is deliberately separate
from latency timing; the report is a baseline input for future thresholds, not
a portable release gate.

Pure action-settlement and cancellation transitions live in
`workflow_transitions.py` and are exercised by a development-only Hypothesis
state machine across result races, restarts, cancellation and terminal states.
A second Hypothesis reference-model state machine drives the real
`WorkflowEngine`, repeatedly reopens one SQLite journal, and randomizes
approval, cancellation, resume and reconciliation. This follows Hypothesis's
stateful testing model while retaining deterministic focused tests for true
concurrent late-result races.

These mechanisms reuse the smallest upstream owners that match the boundary:
[AnyIO task groups, cancellation and capacity limiting](https://anyio.readthedocs.io/en/stable/cancellation.html),
[Python's documented SQLite transaction/close distinction](https://docs.python.org/3/library/sqlite3.html#how-to-use-the-connection-context-manager),
and [Hypothesis rule-based state machines](https://hypothesis.readthedocs.io/en/latest/stateful.html).
No additional workflow scheduler, recovery owner, connection pool, or custom
property-testing framework is introduced.

External acceptance is a separate boundary from the offline developer gate.
Real provider checks require an explicit endpoint/model, local credential and
request/cost cap; a failed live receipt cannot be replaced by a mocked PASS.
The reviewed container smoke uses only the V6 rootless service and an immutable
image, without host mounts or network access. It does not qualify arbitrary
container plugins. Requested Docker HostConfig limits are not proof of resource
enforcement: the smoke rejects a daemon without cgroup support before creation
and checks actual cgroup values inside supported containers. Rootless operation
alone does not establish CPU, memory or process limits.
The delivery archive now freezes wheel/lock/input/golden digests plus separate
approval-waiting and tool-completed SQLite states. Its independent verifier
resumes the stable approval identity and reads the completed state with an
executor that fails if any dispatch or reconciliation is attempted. The first
archive is classified `BASELINE_ESTABLISHED`, not historical compatibility;
only a later, differently identified consumer can add a real version-pair row
to `v6-compatibility-matrix-v1`. Workflow-definition, runtime-record and driver
continuation claims remain separate. Cross-driver continuation is unsupported,
and provider continuation is exact-driver-binding only. A deliberate manifest
or schema refusal is reported as `INCOMPATIBLE_SAFE_REJECTION`, separately from
an unexpected verifier/infrastructure failure.

## Read-only operations and journal lifecycle

`WorkflowEngine.explain(run_id)` and `preview_recovery(run_id)` read a compact,
credential-safe journal projection without hydrating inputs or retained result
values. They never call a driver,
tool, credential resolver, or reconciliation endpoint. An explanation names
the frozen workflow/driver requirements, pending approvals, action state and a
stable blocker code; it deliberately omits input, argument, output and error
payload values. A recovery preview can say that an unknown action needs a
result query, or that a human decision is needed, but never performs either.
The local Web routes and `explain` / `preview-recovery` CLI commands expose the
same views.

`SQLiteRunJournal` has a versioned migration ledger
(`v6-sqlite-run-journal-v1`). Each migration is committed independently, so an
interrupted upgrade rolls back only that migration and a later open resumes
from the last recorded version. The database format is intentionally separate
from workflow schema versions: old runs retain their frozen workflow contract
while local storage upgrades in place.

There are four intentionally separate version axes: the installed package
version controls Python API availability; `WorkflowSpec.schema_version` freezes
workflow semantics; frozen driver/runtime bindings control whether an existing
run may resume; and `JOURNAL_STORAGE_VERSION` controls only the local SQLite
layout/migration ledger. None is inferred from another. A storage migration may
add an operator projection, for example, without changing an old workflow's
Agent, action, or approval behavior. Conversely, a later package/runtime
binding that cannot satisfy a frozen driver remains a recoverable binding
blocker rather than an automatic cross-driver migration.

Backup uses [SQLite's online backup API](https://www.sqlite.org/backup.html) into a sibling temporary file,
checks `PRAGMA integrity_check`, then atomically replaces the requested target.
That is preferable to copying an active SQLite file: SQLite documents that the
online API produces a snapshot while allowing brief-lock incremental reads.
Restore validates the backup first and then runs the same migration ledger.
Before the restored database is published, every nonterminal run is changed to
`RECOVERY_PENDING` and receives `v6-backup-recovery-v1` metadata; the copied
lease is cleared. It is neither scanned by a host nor resumable, reconcilable,
or cancellable through the legacy terminal path. Operators must stop the
original runtime owner, explicitly acknowledge that fact, and choose either
the default `query` recovery (every copied `READY` or `DISPATCHED` Action
becomes `RECONCILING`) or the narrow `idempotent-redispatch` route. A `READY`
record is not proof that the original owner did not dispatch it immediately
after the backup snapshot. The latter route is only available for a formerly
running Action whose frozen capability declares a stable downstream idempotency
key; it reuses the existing action ID. This keeps backup restore distinct from
ordinary data restore and never silently creates a second business dispatch.
`preview-recovery` surfaces the stable `DUPLICATE_EFFECT_POSSIBLE` risk code
and the safe Action IDs before an operator selects either route.
Retention is explicit: it may mark only terminal `SUCCEEDED`, `BLOCKED` or
`CANCELLED` runs as archived, and it may purge only already archived rows.
`RUNNING`, `WAITING_APPROVAL`, `CANCEL_REQUESTED`, and `RECONCILING` runs are
never retention candidates. `run_id` is a reusable caller-facing business
name after a purge, while each newly created snapshot has a fresh immutable
`execution_id`. `RunHandle(run_id, execution_id)` identifies one such instance;
mutating Python, CLI and Web controls accept `expected_execution_id`. The
Journal checks that precondition in its atomic transition, not merely in a Web
precheck. Compact status/inspection projections and every event expose the
same identity. Action, approval and model-invocation IDs derive from that
execution identity; resume preserves it. Snapshots from before this field keep
their original `run_id`-derived identity, so loading an old snapshot never
silently creates a new external action. Purge also removes no-longer-referenced
immutable definitions and large values in the same transaction.
That is logical reclamation: SQLite normally keeps deleted pages on its free
list for later reuse. `space_info()` reports the database and WAL sizes,
`freelist_count`, reusable bytes, and a compact-size estimate. `vacuum()` is an
explicit idle-journal maintenance operation; it refuses any nonterminal run and
checks for the temporary space SQLite may need. Retention never invokes it.
This follows SQLite's documented distinction between reusable free pages and
the file rebuild performed by [`VACUUM`](https://www.sqlite.org/lang_vacuum.html).

```bash
platform-agent-workflow journal-info runs.sqlite
platform-agent-workflow journal-space runs.sqlite
platform-agent-workflow journal-vacuum runs.sqlite
platform-agent-workflow journal-backup runs.sqlite runs.backup.sqlite
platform-agent-workflow journal-restore runs.backup.sqlite restored.sqlite
platform-agent-workflow journal-release-recovery RUN_ID --journal restored.sqlite \
  --source-owner-stopped --strategy query
platform-agent-workflow journal-retain runs.sqlite --archive-after-seconds 2592000
```

The lifecycle API and CLI are local maintenance tools, not an automatic
retention service. A host chooses its policy and should take a verified backup
before enabling destructive purge settings.

SQLite backup does not contain files outside the database. The separate
`v6-local-recovery-v1` package combines the online backup with one selected
`FileWorkspace`: it includes `inputs/` and immutable `artifacts/`, excludes
staging `outputs/`, hashes every archive entry, and verifies each
`FileReference` reachable from retained snapshots before packaging and after
restore. Missing artifacts fail closed with
`LOCAL_RECOVERY_ARTIFACT_MISSING`; completed actions are never replayed to
recreate files. `local-retention-preview` is read-only and protects identities
referenced by every retained live or archived run.

The file-reference enumerator is shared by backup, restore and retention. It
walks `RunSnapshot.inputs`, the frozen workflow, execution manifest and mutable
state, so an input needed only after an approval is protected before a tool
consumes it. The v1 package scope is explicitly `full_workspace`: it does not
silently crop unrelated immutable inputs or artifacts. Writer and reader share
entry-count, per-entry and total-uncompressed-size checks, and the writer runs
the reader's digest validator before publishing. `local-backup-plan` reports
count, payload size and conservative required space without publishing.

Restore is publish-to-new-directory only. Existing directories, active
runtimes and symlinked targets are rejected; `overwrite=True` produces
`LOCAL_RECOVERY_OVERWRITE_UNSAFE` before deletion. A failed final rename leaves
the source package intact and never removes an old runtime. This reflects the
documented `os.replace()` boundary: an atomic rename cannot make a preceding
recursive deletion part of the same atomic operation.

Each package also carries `v6-runtime-environment-v1`: core, actually used
driver/provider distributions and versions, Python implementation/version,
platform identity and dependency-lock digest. Core/driver changes are resume
errors; provider SDK, lock, Python and platform differences are warnings
because they are not automatically proof of incompatibility. Project runs
freeze the lock digest in their execution manifest. The checker never installs,
synchronizes or inventories unrelated host packages.

```bash
platform-agent-workflow local-backup runs.sqlite .v6-files local-recovery.zip
platform-agent-workflow local-backup-plan runs.sqlite .v6-files
platform-agent-workflow local-environment-check local-recovery.zip \
  --dependency-lock pylock.toml
platform-agent-workflow local-restore local-recovery.zip restored-runtime
platform-agent-workflow local-retention-preview runs.sqlite .v6-files
```

Bare JSON CLI, project CLI, Python embedding and local Web use the same
`FunctionToolExecutor` registration rule. `serve` owns a bounded `HostRuntime`
instead of exposing a raw engine, so foreground calls and restart recovery
share run/model/tool capacity and shutdown semantics. Files remain an explicit
`--file-workspace` or `ToolContextFactory` binding.

The isolated `scripts/delivery_baseline` gate builds the wheel, installs
only `[workflow]`, exercises the installed CLI and Python API, and measures
installation bytes, cold import, CLI check latency, idle RSS, and full local
backup/restore time. Reports are written to a requested temporary or external
path and are not source inputs. File/recovery maintenance sequences also run inside
the existing Hypothesis state-machine suite rather than a second framework.

`LocalToolExecutor` and the supplied external adapters keep only a bounded
in-process completion cache (1,024 durable results by default). The engine
acknowledges an entry only *after* its resolved or closed Action transition is
committed to the journal; before that acknowledgement, and for every
in-flight or `RECONCILING` action, eviction is prohibited. The journal is then
the idempotency authority for a resumed workflow, so cache pressure cannot
turn a persisted action into a second dispatch. Routed and host-limited
executors forward this acknowledgement without acquiring an execution token;
it is bookkeeping, never a tool call. Hosts that use a custom executor may
implement the same optional `acknowledge_durable_result(request)` hook, or
omit it and retain their own cache policy.
