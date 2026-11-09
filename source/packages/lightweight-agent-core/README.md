# V6 lightweight Agent core

An embeddable workflow/Agent kernel for applications that need explicit tool
authorization, approval, cancellation, durable local recovery, and no required
model or web dependency. The base package uses only the Python standard
library; install optional capabilities only at the embedding edge.

New Python integrations can import the 40-name v4 surface from
`platform_lightweight_agent.api`. The package root keeps its older exports for
source compatibility, but loads their owning modules only when requested.
Advanced bundle, domain, inspection, and maintenance APIs remain in their
explicit submodules; importing the package root alone does not load them.

```bash
python -m pip install 'platform-lightweight-agent-core[workflow]'
```

Model providers are deliberately separate. For example, an Anthropic-backed
PydanticAI v4 workflow installs
`platform-lightweight-agent-core[workflow,pydantic-ai-anthropic]`; an Agents
SDK v4 workflow installs `[workflow,openai-agents]`. The SDK extras alone can
construct their drivers, but do not include the v4 state runtime. The core
`[pydantic-ai]` extra remains provider-neutral. Environment inspection reports
package metadata, provider importability, local driver/model construction, and
model-network reachability as separate facts (the last is never probed
implicitly).

Portable bundle export and import use sibling staging plus validation before a
single publish step, so a failed export preserves a prior archive and a failed
project import leaves no partial destination. `diagnose --check-environment`
checks only safe metadata for development deliveries by default. For
host-trusted development code, add `--verify-development-entrypoints` to load
the declared `MODULE:CALLABLE` and verify it is callable; this still does not
register or invoke the tool.

For a runnable authoring project, install the authoring extra and use the same
core APIs as the CLI:

```bash
platform-agent-workflow init workflow demo
cd demo
platform-agent-workflow check .
platform-agent-workflow plan .
```

## Declarative domain packs

`v6-domain-pack-v1` is an optional authoring boundary for a reusable business
recipe. A source pack may describe versioned entry workflows, exact tool
identities and permissions, business terms, required validation rules, pinned
resources/tests, deterministic context selection, Agent instructions, and
driver capability requirements. It cannot carry a tool registry, executable
code, credential reference/value, model endpoint, or runtime state.

Use the same optional authoring environment to check and compile a pack. The
compiler only emits the existing `v6-agent-project-v1` project shape; it never
installs dependencies, imports tool code, calls a model, or executes a tool.

```bash
platform-agent-workflow domain check examples/domain-packs/groundwater
platform-agent-workflow domain compile examples/domain-packs/groundwater /tmp/groundwater-project
```

The generated project carries a `v6-domain-lock-v1` record. New runs with that
record freeze it inside an explicit `v6-execution-manifest-v3`; ordinary
projects continue to create `v6-execution-manifest-v2` and are not migrated.
On every new assembly and resume, the host must still provide matching tool
implementations, model drivers, and current permission grants. A lock freezes
identity and requested scope; it never preserves a former authorization.
Project bundle v2 export carries the lock as its declared `domain-lock.json`
file. A v2 project import recognizes and revalidates that strict lock before
publishing the assembled project; an invalid ordinary file with that name is
not silently treated as a domain contract.

The checked examples in [`examples/domain-packs`](examples/domain-packs) use
the standard pattern: input → `ValidationReportV1` tool → explicit `PASS` gate
→ approval → calculation, with `FAIL` and `UNKNOWN` routed to a blocking
result. `ValidationReportV1` binds a rule identifier and digests of the
checked input/arguments, so a capability implementation can reject a stale
receipt after either changes. `groundwater` remains a declarative identity
example. `csv-statistics` ships a reproducibly built standard-library wheel,
three independently authorized entry points, compile-time parameter profiles,
and a clean-install restart/bundle test. The embedding host still owns every
binding and permission grant.

For a domain Agent, the opt-in `v6-context-selection-policy-v2` names exact
Agent addresses and a byte budget. Compilation replaces only that Agent's
reserved `/domain_context` literal with UTF-8 resources carrying their path,
role, and SHA-256. The payload then travels through the ordinary node input
contract and model prompt; an undeclared Agent, changed digest, oversized
selection, or unused sentinel fails before a run is created.

The generated project includes `scripted-decisions.json` and an executable
offline V4 test covering a tool request, approval pause, SQLite restart and
structured final output. Scripted results are marked simulated and are not a
model-quality or provider-compatibility claim. For shell/CI integration, add
`--strict-exit` to `run`, `resume`, `cancel` or `reconcile`; approval wait and
reconciliation then have distinct exit codes instead of being confused with a
failed or successful workflow. CLI errors use the stable
`v6-cli-diagnostic-v1` shape with safe location, path, keyword and repair-hint
fields.

Trusted Python tools are registered by exact immutable `ToolReference`, never
by model output or entry-point discovery. The standard project runtime accepts
both a normal function and `async def` through `FunctionToolExecutor`; changing
a handler to native async does not change its schema, permissions, version or
release-delivery contract.

```python
from platform_lightweight_agent.api import FunctionToolExecutor, ToolReference

tool = ToolReference("example.echo", "1", "sha256:" + "a" * 64)
tools = FunctionToolExecutor()

async def echo(arguments: dict[str, object]) -> dict[str, object]:
    return {"value": arguments["value"]}

tools.register(tool, echo, implementation_version="example-wheel-1.0.0")
```

In 0.2.x, `workflow()` and direct `WorkflowSpec(...)` construction default to
the v4 reliability contract. New definitions must provide strict workflow
input/output schemas and complete node contracts. To preserve 0.1.x Python
code that implicitly created v1, pass
`schema_version="v6-light-workflow-v1"` explicitly; the old definition is not
upgraded behind your back.

| Workflow schema | Current support | New projects |
| --- | --- | --- |
| v4 | Current reliability contract, including model invocation accounting and bounded execution | Default |
| v3 | Compatibility execution and recovery | Only when an existing definition requires it |
| v1/v2 | Legacy compatibility; no automatic migration to v4 | Avoid |

The older schemas remain accepted for existing integrations, and a run always
resumes with its frozen definition and driver binding. JSON/YAML and bundle
definitions carry their own schema version; the Python default does not rewrite
them. New definitions can use the small modern entry point:

```python
from platform_lightweight_agent.api import (
    NodeContract, ValueBinding, approval_step, workflow,
)

empty = {"type": "object", "properties": {}, "additionalProperties": False}
approved = {
    "type": "object",
    "properties": {"approved": {"type": "boolean"}},
    "required": ["approved"],
    "additionalProperties": False,
}
spec = workflow(
    "example.review",
    "1",
    (approval_step("review", "Approve?", contract=NodeContract(empty, approved)),),
    input_schema=empty,
    output_schema=approved,
    output_bindings={
        "/approved": ValueBinding("step_output", "/approved", "review")
    },
)
```

Every new run freezes a credential-free execution binding for each tool it can
actually use. Built-in HTTP, MCP and container adapters describe their exact
service/tool/image target automatically. Python tools use the package digest by
default and accept the explicit implementation version shown above. A resumed
approval refuses a changed target; importing the workflow and creating a new
run may intentionally bind a different environment.

A container tool that requires strong CPU, memory and process limits declares
`ContainerResourceLimits`. Its application-owned `ContainerRunner` must expose
an `ensure_resource_limits()` probe; registration and dispatch both fail closed
with `RESOURCE_LIMIT_ENFORCEMENT_UNAVAILABLE` when the daemon/cgroup boundary
cannot enforce every limit. Tools that omit this contract make no resource-quota
claim, preserving the small optional adapter surface.

An opt-in `ToolContext` can give a trusted handler only its stable `action_id`,
cooperative cancellation token, and a host-owned `FileWorkspace`. Publish files
with `context.files.record_output(..., action_id=context.action_id)`; the
returned `FileReference` becomes a verified input to the next step.
The CLI opts into the same boundary with `--file-workspace PATH`.

The same live cancellation token is injected into `def` and `async def`
handlers. A synchronous handler must stop cooperatively—Python cannot terminate
its worker thread—and an unproven dispatched result remains `RECONCILING`.
`FileWorkspace.published_manifest()` lists only indexed immutable artifacts;
`staging_manifest()` is deliberately separate and is only for diagnostics and
cleanup of partial work.

Tool authors can use `assert_sync_action_replay` or
`assert_async_action_replay` with an `effect_observer`. The helper verifies the
exact result `action_id`, stable replay result, no repeated external effect,
and rejection of the same action identity with conflicting arguments.
`assert_sync_outcome_classification`, `assert_sync_reconcile_query`,
`assert_execution_bindings_distinct` and `assert_credential_free_contract`
cover the remaining external-adapter contract without adding a test framework
to the installed package.

`McpToolExecutor` accepts an application-owned client/session and translates
standard MCP `content`, `structuredContent` and `isError` results. Tool errors
remain uncertain by default because they may follow a side effect; opt into
`known_failure` only for a reviewed tool contract. This bridge does not install
an MCP SDK, and passing an action ID does not itself promise remote idempotency.

For long-lived embedding, separate `HostRuntime` instances may safely share a
process-scoped `mutex_resource` even though each host owns a different event
loop. `host.diagnostics()` reports the bounded process registry and incomplete
cleanup count. `host.close()` returns a `HostRuntimeCloseReport`; cleanup
timeouts and aggregate hook failures carry that report and keep unfinished
resources owned for a later retry instead of claiming they stopped.

See the generated workflow README and the public API docstrings for the full
v3/v4 async and recovery contracts.

V3/V4 nodes default to 1 MiB JSON input and 1 MiB JSON output limits, and a
run defaults to 8 MiB of retained input/state. These are independent workflow
contract values (`NodeContract.max_input_bytes`, `max_output_bytes`, and
`WorkflowExecutionPolicy.max_retained_state_bytes`), not the Agent tool
contract limit. Oversized JSON is rejected rather than silently converted to a
file; use an explicit `FileReference` when the workflow is designed to pass a
large artifact. The CLI and local Web request boundary also reject inputs over
1 MiB before workflow execution.

`journal-backup` intentionally covers SQLite only. Use `local-backup` and
`local-restore` when a run must move together with staged inputs and immutable
published artifacts; partial `outputs/` are excluded and every referenced
file is checked before restore. `local-retention-preview` shows which
publications are protected by retained runs without deleting anything.
`local-restore` publishes only to a new directory; it refuses overwrite even
when the legacy flag is supplied, so a failed restore cannot delete the only
usable environment. Run `local-backup-plan` first for exact scope, file count,
payload size, limits, and estimated space. `local-environment-check` compares
the package's core/driver/provider, Python/platform, and dependency-lock
identity without installing or changing the host.

For SQLite operations, `journal-space` reports database/WAL size, free pages,
reusable bytes and a compact-size estimate. Logical retention does not promise
that the database file shrinks. `journal-vacuum` is a separate explicit command
that refuses nonterminal runs and checks temporary disk space; it is never run
automatically by retention.

## Local run inventory and console

SQLite journals expose an optional, bounded `RunQuery` projection. It lists
only run identity, frozen workflow identity, status, timestamps, pending
approval count, and a short failure code; it does not hydrate workflow inputs
or outputs. Custom journals may omit this capability.

```bash
platform-agent-workflow runs --journal .v6-agent-runs.sqlite --pending-only
platform-agent-workflow serve --journal .v6-agent-runs.sqlite \
  --session-token "$V6_LOCAL_WEB_SESSION_TOKEN"
```

With the `[web]` extra, open `/console` on the loopback server. The bundled
HTML/JS has no Node/CDN dependency. It keeps the control token only in the
current page's memory, sends it only in the existing control header, and
renders model/tool text with text nodes rather than HTML. The console delegates
approval, cancellation, and reconciliation to the same persisted API used by
the CLI; it does not infer or execute recovery actions itself. SSE invalidates
the read view; node progress is read from durable state, not reconstructed from
an incomplete event page. `/v2/runs/{run_id}/view` returns one revision's
execution status, explanation, approvals, progress and final output. SQLite
hydrates only that output, not every retained tool result. Raw `/detail` remains
an explicitly bounded diagnostic endpoint.

Each selection owns its fetches, timers, stream callbacks and control identity.
Refresh sequence plus revision checks reject stale replies even after A→B→A;
controls stay disabled until the selected view is validated. The business-result
display convention requires the output schema to require `business_status`
(a declared string enum) and `reason_code` (string/null). It reads those fields
only from a successfully validated final output. Otherwise business status is
`Not reported`; it never substitutes the execution status.

For a developer-delivery report covering source identity, lock identity, full
tests, and a clean wheel's real local execution/recovery probes, run:

```bash
uv run python scripts/verify_developer_delivery.py
```

The gate first rebuilds the ignored `csv-statistics` capability wheel with a
fixed `SOURCE_DATE_EPOCH`, regenerates its frozen tool identities, and verifies
that the wheel, sidecars, and release declarations share one digest. A fresh
checkout therefore needs no manually prepared binary artifact.

`verification-profile.json` declares the `verification` dependency group,
required suites and build/test inputs. Missing dependencies, collection gaps,
skips/xfails and failed teardown cannot pass. Checks and wheel builds use the
same captured input set; caches are not inputs, untracked matching source is.
`--skip-*` produces a nonzero `incomplete` report. Evidence aggregation separately
requires every schema/variant/window/repetition declared in the defect manifest;
one PASS cannot fill another scenario's missing evidence.

Use `--archive-artifacts /new/absolute/directory` to retain the exact wheel,
lock, input hashes, an approval-waiting state, and a tool-completed state. The
archive includes `compatibility-verification.json` and
`compatibility-matrix.json`; the verifier resumes the original approval and
proves that the completed action is not replayed. The first archive is still a
**current producer baseline**, not evidence about an older release. A future
consumer must run `scripts/delivery_baseline/verify_compatibility.py`
against that immutable archive before a version pair can be claimed.
Cross-driver continuation remains unsupported.

Live model evaluation remains an explicit separate operation through
`platform-agent-workflow evaluate --allow-model-requests`. Install `[evaluation]`
plus the selected SDK extra; `[evaluation]` includes the v3/v4 workflow runtime.
For DeepSeek, set `DEEPSEEK_API_KEY` in the process environment and pass
`--model deepseek:deepseek-flash`. The PydanticAI path uses Chat Completions with
thinking disabled; the Agents SDK path uses Responses and allows up to 1024
output tokens so parallel-tool continuations are not truncated at 256. Both
paths retain the workflow's per-case model-request and token budgets. The
evaluator never runs as part of the default developer gate and has no global
spend cap: review the project's evaluation cases and workflow budget first.
Its JSON report records every expected case, including stable failure codes
for cases that could not execute; a failed assertion, missing case, or case
exception makes the CLI exit nonzero. Provider error bodies and credentials
are not copied into case-failure entries. `estimated_cost: unknown` means the
installed price catalogue lacks a usable price, not that usage was free.

The tightly bounded DeepSeek smoke in
`tests/defect_discovery/external_live/deepseek_smoke.py` is separate from that
general evaluator. It permits only the approved endpoint/model, three physical
attempts, 256 output tokens per attempt and a USD 0.10 reservation cap; it
requires explicit opt-in and a local credential. It does not certify model
quality or other providers. `external_live/container_smoke.py` verifies one
reviewed, digest-pinned BusyBox computation with a non-root user, read-only root,
no network/mounts, dropped capabilities and resource limits against the dedicated
V6 rootless service. Resource acceptance requires daemon support and in-container
cgroup values; merely finding requested flags in HostConfig cannot pass. A
rootless service without cgroup enforcement is rejected by the same public
resource-capability gate before container creation.
It does not certify arbitrary hostile images or recovery of
unknown container outcomes. If that runtime is unavailable, the check stays
unassessed; no host/V7 socket fallback is allowed. Neither live probe runs as part
of the default developer gate.

## Instance-safe controls and event replay

`run_id` is a reusable business name after retention purges an old record;
`execution_id` identifies one concrete execution. Keep the returned
`RunHandle` (or pass `expected_execution_id`) when a delayed cancel, approval,
reconcile, or event read must address exactly the run the caller previously
saw. A stale identity raises `RUN_EXECUTION_MISMATCH` rather than operating on
a newer same-name run.

Events include an `EventCursor(execution_id, sequence)`. The CLI accepts
`events RUN_ID --cursor TOKEN`; its `resume`, `cancel`, and `reconcile`
commands accept `--expected-execution-id`. Python can pass the cursor object to
`engine.events()` or `engine.stream_events()`. The optional `[web]` surface
encodes that cursor in SSE IDs, honors `Last-Event-ID`, sends a transport-only
heartbeat, and returns HTTP `204` for an already caught-up terminal stream.
