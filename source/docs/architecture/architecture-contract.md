# Architecture Contract

These principles govern the product-neutral Kernel, independent capability
services and assembled products. The current protocol and storage versions are
defined by executable contracts and the V12 research acceptance policy. Retired
baselines are available from Git history rather than the current source tree.

## Principles

1. **Explicit ownership.** Gateway owns no business state. Kernel owns interaction
   and orchestration, including Session-scoped task intent and immutable revisions;
   Data owns datasets, immutable versions, bytes, semantics and lineage; Knowledge
   Next owns evidence; Executor owns execution state. Services communicate through declared
   interfaces and never import another service package or open its stores.
2. **Definitions and execution are separate.** Operator, Workflow and Executable
   Model definitions contain no runtime state. Assembly validates capabilities,
   bindings and identities before publication. Published identities and checksums
   remain immutable; a new definition has a new version.
3. **Execution requires authorization.** Before side effects begin, authorization
   binds explicit executable content, definition identities, project and arguments.
   Changes to that content cannot inherit approval of different content. Read-only
   replies, suggestions and workflow drafts do not authorize execution. The
   authorization and resulting execution remain attributable and auditable; the
   current object chain and revision rules belong to the release baseline.
4. **Durable state is authoritative.** State transitions and their persisted events
   commit atomically. Request replay is idempotent; changed-payload replay conflicts.
   Recovery fences stale actors and preserves committed progress. A new authorized
   execution attempt is distinguishable from replay or recovery of an existing
   attempt, with a traceable relationship to prior attempts.
5. **Results publish atomically.** Verified bytes and provenance precede success.
   A result group becomes publicly visible as one committed publication. Workspaces,
   partial outputs and transport acknowledgements are not proof of completion.
   Task completion checks declared requirements against these facts; persisted reply
   delivery proves existence, while business adequacy requires explicit user review.
6. **Trust boundaries are explicit.** Validate untrusted input at its owning boundary
   and trust validated internal types. Programming errors and impossible states fail
   visibly; they must not become fabricated business success or empty results.
7. **Evidence is attributable.** Data and Knowledge Next observations bind project,
   exact version, semantic identity and checksum. Changed or unavailable evidence cannot silently
   substitute for an earlier read. Dialogue memory is not authoritative data, and
   reported execution results come from persisted execution facts. Diagnostics do
   not expose secrets or observation bodies.
8. **Deployments protect existing data.** Changes preserve isolation, declared
   ownership and rollback evidence. Implementation work does not authorize deleting
   existing deployments, volumes or data.
9. **Context is a compiled view, not another authority.** Kernel reads Session,
   Task, execution and memory state in a consistent snapshot, then a pure,
   deterministic policy selects the Provider view. Pinned user requirements and
   active execution facts cannot be silently removed to fit a window. Historical
   facts may become retrievable references, but their owning tables remain
   authoritative. Context manifests contain hashes and measurements only, never
   message, prompt, parameter, observation or model-output bodies.
10. **Resource discovery stays with the owning service.** Data and Knowledge expose
    read-only projections over their own current state. Kernel may persist a compact
    TaskPreparation record whose entries bind exact identities, selection policies,
    parameter sources, checks and blockers for one AgentRun, then compile fresh facts into the Provider view and
    current or stale facts into the UI view.
    It must not copy service catalogs or evidence bodies into a shared resource table.
    A `READY` preparation proves that the declared contracts and identities were
    checked for the current Turn; it does not prove scientific suitability, data
    quality or business acceptance.
11. **Third-party execution is deployment-bound.** Operator, Workflow, Model and
    Agent parameters may name only a fixed `serviceId` and fixed capability; they
    cannot carry a URL, Bearer value, CA body, executable path or uploaded code.
    Product deployment owns HTTPS endpoints, exact host allowlists, credential/CA
    secret references, timeouts and service/model version allowlists. Executor owns
    idempotent coordination, durable remote evidence, checksum verification and the
    final atomic Data publication; a remote success response alone is never a
    published Workflow result.

## Product and domain boundary

Kernel, capability-service runtimes, shared protocols, Assembler and development
tooling are product-neutral. Their source and interface namespaces must not name or
branch on a product or domain identity. They consume only versioned Module, Product
and Domain declarations. The current Module Contract identity is
`platform.module/v9`; module manifests use new module versions because replacing the
former product-qualified contract is a breaking declaration change.

Product-owned facts live under `products/<product-id>/`; domain instructions,
workflows, models, knowledge and fixed cases live under
`domains/<domain-id>/<version>/`. A product-specific SDK, UI label, migration,
evaluation fixture or deployment evidence may name its product. An installed static
capability catalog may also contain domain operators, but neutral layers may select
them only by declared identity and version: no product-name condition may alter
validation, execution, persistence or recovery.

This boundary removes product knowledge from the reusable system while retaining one
repository and one static Operator distribution for the current release. Revisit the
distribution boundary when products need independently released operator catalogs or
different Worker dependency sets; that change would require a versioned capability-
package selection contract and new implementation identities, not runtime discovery.

## Evolving the architecture

Analysis, comparisons and design proposals may challenge both these principles
and the current baseline. Explain the problem, alternatives, tradeoffs and affected
contracts; an existing implementation choice is not a reason to omit alternatives.
A proposal or an earlier assistant recommendation is not implementation approval.

Changing a principle or release requirement requires an explicit user instruction.
An instruction to implement a specified architectural change authorizes its necessary
contract and baseline updates; do not request the same approval again. Clarify only
when the intended behavior or scope remains materially ambiguous. Ordinary fixes
and refactors within the agreed behavior need no additional architecture approval.

For an authorized change, update the affected baseline, scope, schemas, code and
acceptance checks together. Preserve unaffected guarantees and published identities;
do not weaken a gate just to conceal a failure.

When recording a material design decision, include its reason, applicable scope,
evidence or unresolved assumptions, and the condition that would justify revisiting
it. Release history belongs in Git tags and release artifacts, not current runtime
documentation.

## Data capability service

Data owns its dataset catalog, immutable versions and bytes, semantic revisions,
metric facts, query receipts, publication state and lineage. PostgreSQL and local
objects belong exclusively to this service; DuckDB is a restricted query engine.
Data is a required service in every current Public API v12 Product Manifest. Kernel
input preparation and result publication use its contract, and the Assembler rejects
a v12 product without Data. Making Data optional would require a new versioned
Kernel/Product capability contract. Data owns input and result data. Operational details and component contracts live in
[the Data Service documentation](../../services/data/README.md).

Data's inventory and structured discovery endpoints are projections, not a second
catalog. They query current Dataset/Version/Semantics/Object facts at request time,
return the basis and unknown metadata explicitly, and never become execution
authority. Execution still freezes a full `DataVersionRefV2` and rechecks it at the
Executor boundary.

## Task preparation projection

Kernel owns one durable `TaskPreparationV1` head and immutable revisions per AgentRun.
Each head contains one or more target entries because a single proposal can freeze
multiple actions with different arguments. Readiness joins a Session goal, an
AgentRun, selected capabilities, exact service identities and proposed parameters.
Data and Knowledge continue to own the underlying
facts. Kernel exposes the latest preparation through Session and history projections;
the initial/proposal Agent context includes the compact snapshot only while its goal
revision is current, so an old selection cannot become evidence for a new Turn.
Execution/rejection resume uses the already frozen AgentAction and RunState instead
of duplicating preparation facts. Kernel derives `ACTIVE`, `WAITING_CONFIRMATION`,
`EXECUTING`, `CONSUMED`, `REJECTED`, `FAILED`, `ABANDONED` or `STALE` from current
run/action state; services and callers do not write a second lifecycle flag.

The preferred Agent path discovers candidates, reads required Data and Knowledge,
and calls `prepare_task_inputs` before recording the plan. The proposal boundary also
derives the same snapshot from exact observations in the current Turn, so Provider
tool-order variation cannot bypass or disable the gate. Changed arguments after an
explicit preparation are rejected, including a second action that tries to reuse an
already consumed preparation entry. `CURRENT_REQUIRED` additionally rejects a
selected version when Data reports another current version; `PINNED` preserves an
explicit historical/frozen selection. Workflow drafts receive the same projection after
catalog compilation and remain `BLOCKED` while required inputs or unresolved items
exist. A later goal revision makes the prior snapshot stale and requires revalidation.

## Knowledge Next capability service

Knowledge Next owns prepared knowledge, immutable evidence units and versions,
lifecycle revisions, import operations and retrieval receipts in its independent
PostgreSQL/pgvector store. Uploading a validated JSON knowledge package requests
automatic atomic activation after indexing; no second publication approval is
required. Data remains authoritative for raw data and semantic revisions, referenced
through exact HTTP bindings. Knowledge Next is assembled into Groundwater v12 and is
the only current evidence service; Qdrant and the legacy Knowledge service are absent.
Its [component documentation](../../services/knowledge-next/README.md) owns interfaces,
limits and operational procedures.
