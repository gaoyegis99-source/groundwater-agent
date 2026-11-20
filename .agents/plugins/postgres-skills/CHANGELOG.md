# Plugin changelog

Notable changes to the installable `postgres-skills` plugin, including its
skills, MCP integration, host manifests, and setup documentation, are documented
here. Marketplace metadata and discovery changes are tracked in the
[marketplace changelog](../CHANGELOG.md).

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-11-14

### Added
- Automatic Coding Notes Processing task lists across assessment, conversion and static early
  exits, with per-note dispositions distinguishing evaluation from implementation/runtime proof
- Bounded file/project-directory notes ingestion, private path provenance, exhaustive stable IDs,
  content-digest invalidation, synchronized runtime/report evidence and regression validation
- `migrate-oracle-app` skill for Oracle-dependent application assessment and conversion
  across Java, .NET, Python, C, and C++, including DDL reconciliation, build/test/coverage
  guidance, Docker validation, driver-packaging checks, resumable execution, and migration reports
- Supporting scripts in `skills/migrate-oracle-app/scripts/`, automated contract
  tests, Node.js package commands, and a [migration guide](MIGRATION.md)

## [0.2.0] - 2026-11-03

### Added
- `azure-postgresql-restore-deleted-server` reference: recover a deleted Flexible Server within the 5-day `ReviveDropped` window, VNet-integrated restores, Azure Backup vault fallback, and delete locks (HorizonDB deleted clusters are unrecoverable)

### Changed
- Document skills-only installation through skills.sh
- Clarify Postgres Skills positioning and onboarding

## [0.1.2] - 2026-09-17

### Added
- Agent Plugins v1.0 manifests for GitHub Copilot marketplace compatibility

### Changed
- Keep cross-skill routing references compatible with isolated skill validation

## [0.1.1] - 2026-09-16

### Added
- `pg-graph` skill for Apache AGE graph workloads
- Azure HorizonDB (Preview) routing and guidance
- Beginner installation guidance for Claude Code, Copilot CLI, and Codex CLI
- Plugin setup, permissions, data-access, and privacy documentation

### Changed
- Launch postgres-mcp via npx instead of the bundled launcher
- Upgrade postgres-mcp to `0.1.0-rc.9` and update MCP tool namespaces
- Disable optional postgres-mcp telemetry in the bundled plugin configuration
- Require confirmation before database, graph, file-import, and Azure resource changes
- Use "Postgres Skills" as the human-facing product name

### Fixed
- Correct skills-only guidance and routing issues found during the bug bash

## [0.1.0] - 2026-07-15

### Changed
- Migrate the plugin from pgsql-tools to postgres-mcp, including server registration, launcher, configuration paths, and environment variables
- Rename the plugin to `postgres-skills`

### Fixed
- Fix Windows launcher compatibility and guardrail and routing gaps

[Unreleased]: https://github.com/microsoft/postgres-skills/compare/main...HEAD
[0.3.0]: https://github.com/microsoft/postgres-skills/compare/8066083...f20d2cb7828745b2d45666f46011fa54a549773b
[0.2.0]: https://github.com/microsoft/postgres-skills/compare/3ea7816fd1e449916b9277cc2671bc1c4c34f8c9...8066083
[0.1.2]: https://github.com/microsoft/postgres-skills/compare/postgres-skills--v0.1.1...3ea7816fd1e449916b9277cc2671bc1c4c34f8c9
[0.1.1]: https://github.com/microsoft/postgres-skills/compare/0a4a57e1b5e3118793413efa6b5263dd7c80e5f8...postgres-skills--v0.1.1
[0.1.0]: https://github.com/microsoft/postgres-skills/compare/c4f1d197d1f2ed675c5e2b05df42c79efef702c5...0a4a57e1b5e3118793413efa6b5263dd7c80e5f8
