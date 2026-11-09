# Engineering instructions

Deliver the requested outcome for the product-neutral Kernel, capability services
and assembled products. Choose the simplest complete solution for the task.

- **Analysis and design:** evaluate alternatives, including changes to the current
  architecture. State tradeoffs and affected constraints; do not silently restrict
  an upgrade request to the existing prototype scope.
- **Implementation:** follow the current contract and release baseline unless the
  user explicitly directs a change. An already authorized change needs no repeated
  approval. Resolve routine implementation choices autonomously.
- **Reading:** use [the document guide](docs/README.md) to read the sections relevant
  to the task. There is no mandatory full-document reading sequence for every edit.
  For local inventory, configuration or repository facts, inspect the available
  local evidence first. Consult external sources when the answer depends on
  external product behavior or current documentation, or when requested.
- **Design:** justify new abstractions, dependencies and recovery behavior by the
  task or observed evidence. Preserve state ownership, validate at trust boundaries,
  and let programming errors fail visibly.
- **Verification:** run checks appropriate to the changed behavior using
  [the playbook](docs/operations/execution-playbook.md#per-change-checks). Report actual results;
  development checks do not establish release readiness.
- **Knowledge:** distinguish current facts, approved decisions and proposals.
  Recheck historical findings against the current code and record the inspected
  revision and verification scope. Earlier conversation and assistant suggestions
  are not new requirements. Update the owning document rather than duplicating
  rules across files.

Architecture changes follow [the contract](docs/architecture/architecture-contract.md#evolving-the-architecture).
