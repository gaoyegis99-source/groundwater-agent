# Agent Framework and Orchestration Kernel Comparison

## Boundary

The Agents SDK owns model turns, tool interruptions and RunState serialization. The platform Kernel owns durable approval, ordered dispatch, WorkflowRun truth, event replay and recovery. Neither replaces the other.

| Concern | Agents SDK | Platform Kernel |
|---|---|---|
| model/tool loop | authoritative | invokes SDK |
| structured plan/final parsing | runtime mechanism | validates and persists AgentPlanV2 and AgentFinalV5 |
| human approval | interruption primitive | durable Confirmation protocol |
| side-effect execution | must not own | durable Workflow dispatch |
| remote Job idempotency | none | WorkflowRun ID request key |
| result truth | tool return only | WorkflowRun and DataVersionRefV2 |
| restart recovery | serialized RunState | dispatch, poll, projection and replay state |
| client state stream | none | persisted Session Event v20 SSE; separate ephemeral preview |
| persistent task intent | tool invocation only | immutable task revisions and evidence-derived completion |

## Why WorkflowRun remains separate

Model text and SDK tool results are conversational evidence. A WorkflowRun is durable operational evidence with canonical arguments, product definition checksum, executor identity, remote Job identity, timing, terminal output references and error. This lets clients audit execution without interpreting a transcript.

## Why AgentPlan is not a scheduler

AgentPlanV2 records what the model proposes and binds approval to the latest revision. Position and lifecycle live in AgentAction/WorkflowRun/dispatch state. Adding DAG nodes or retries to AgentPlan would create two competing execution state machines.

## Why asynchronous confirmation returns 202

Approval commits permission and dispatch intent. Remote execution may outlive the HTTP request, lose connectivity temporarily or cross a process restart. `202` makes that distinction explicit; Session SSE and query APIs expose eventual terminal state.

## Why products remain static

Tools and executors influence what the model may request and what code may execute. Compiling them from the Product Manifest and module contracts into an immutable Kernel Config and image makes the trust boundary auditable. Runtime installation of new executable capabilities would require additional authorization, lifecycle and compatibility contracts. Read-only Data and Knowledge discovery already operates within the installed service contracts.

## Practical audit path

For an execution issue, inspect in order:

1. Confirmation and canonical AgentAction arguments.
2. WorkflowRun and dispatch state.
3. Executor Job status/stage/error.
4. Named Data versions referenced by the Job, including ordinary PNG results.
5. Agent replay and final projection.
6. Ordered Session events and trace spans.

This ordering starts from authoritative state and uses model prose only as explanation.
