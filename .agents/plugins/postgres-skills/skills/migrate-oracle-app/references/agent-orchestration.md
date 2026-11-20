# Agent Orchestration

The primary agent is the migration orchestrator. It owns migration decisions, shared state,
database authorization/writes, cross-unit merge decisions, and final validation. Use child agents to
reduce serial execution and keep verbose work out of the primary context.

## Mandatory Planning Gate

At run start and after each milestone, build or refresh a dependency graph of:

- Read-only discovery scopes
- Conversion units and their owned paths
- Failure classes
- Unit/characterization waves
- Integration waves
- Build/test/package commands
- Report/diff generation

Record whether the client exposes child agents. When available and at least two ready units are
independent, launch at least two child agents in one parallel batch. Do not keep independent eligible
work serial merely because the primary agent can perform it.

Good parallel scopes include:

- Source/provider/SQL inventory versus test/build inventory
- Independent language/module discovery
- Disjoint conversion units with non-overlapping owned paths
- Independent failure-root-cause clusters
- Unit-wave implementation versus Docker harness preparation
- Verbose build/test/package execution and log summarization
- Final residual scan versus file-inventory/diff preparation

Do not delegate a single continuous trace that requires one evolving hypothesis. Do not manufacture
trivial tasks to satisfy parallelism.

## Agent Types

Use the client’s equivalent agents:

- **Explore/read-only agent**: bounded source/test/configuration discovery with no edits.
- **General implementation agent**: one self-contained conversion or test unit with exclusive path
  ownership.
- **Task/execution agent**: verbose build, test, coverage, package, or log-filtering commands;
  returns concise counters and exact failure diagnostics.
- **Review agent**: checks a completed bounded batch for meaningful logic or merge defects.

Use background execution for independent agents. After launching a background agent, immediately
continue independent primary work; do not poll or duplicate the delegated scope.

## Ownership and Safety

Every assignment records:

- Agent ID/type and bounded objective
- Read-only or write-enabled mode
- Owned paths and prohibited/shared paths
- Dependencies and completion checks
- Allowed commands and environment
- Database/Docker restrictions
- Start/completion timestamps and concise result

Write-enabled child agents must have disjoint owned paths. Child agents never own or directly edit:

- `.github/migrate-oracle-app/**`
- Shared dependency/build descriptors unless assigned exclusively in a serialized batch
- The same source/test/configuration path as another active agent

Only the primary agent may:

- Resolve user-visible architecture/scope decisions
- Authorize or execute persistent PostgreSQL DDL/DML
- Mutate shared Docker/database state concurrently
- Reconcile the conversion manifest and canonical state
- Integrate conflicting edits
- Render/finalize reports and record artifact hashes

Read-only database discovery may be delegated only when its exact object scope is bounded and it
cannot race a mutation. Database modifications always remain primary-agent work.

## Merge and Validation

The primary is the single owner of Maven preparation and the shared Docker test environment.
Initialize `migration-runtime.mjs` for that primary and route execution through its exclusive
workspace lease. The Maven runner refuses a child actor or concurrent setup, and reports its
ownership receipt. Children must not bypass the controlled runner with direct Maven/Docker calls.
Children prepare disjoint test sources and scenario/class selectors; they must not independently
reinstall dependencies, run clean/build lifecycles, or start/stop PostgreSQL per scenario. Route
test execution through the primary's verified reusable runner and retain each child result.

For each completed child assignment:

1. Inspect its concise result and changed-path list.
2. Reject ownership violations or unexplained out-of-scope changes.
3. Integrate the batch into the primary worktree/state.
4. Run the narrowest validation that covers the integrated batch.
5. Update conversion-unit, failure-class, wave, and agent ledgers atomically.

Do not ask one agent to reperform another agent’s completed scope. Reuse a multi-turn agent for
follow-up in its assigned scope when available.

## Capability Fallback

If child agents are unavailable, record the exact client/tool limitation and execute the same
dependency graph serially using focused commands. Agent unavailability is not a reason to omit
required migration work. A generic statement such as “not needed” is not a valid fallback when two
or more independent ready units existed.

## Efficiency Evidence

Persist:

- Eligible independent unit count
- Agent facility availability
- Assignments and path ownership
- Parallel batches and overlapping elapsed intervals
- Delegated command/test counts
- Child-agent failures/retries
- Serial fallback reason
- Estimated serial duration and observed elapsed duration when measurable
- Context/tool metrics when exposed

The purpose is convergence efficiency, not maximum agent count.
