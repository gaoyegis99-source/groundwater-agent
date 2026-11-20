# Language and Toolchain Adapters

Apply every adapter detected in the codebase. Mixed-language repositories require one inventory,
build, test-discovery, integration, packaging, and coverage result per applicable adapter. Never
substitute one language's passing result for another.

## Shared Rules

For Java/Maven projects, run the primary runtime's `java-preflight` before preparing test sources.
Use its effective per-module test release and permitted-syntax result; draft validation is mandatory
before staging/compilation. The ordinary compiler remains the semantic/API compatibility gate.
See [runtime-coordinator.md](./runtime-coordinator.md).

For each project/module:

1. Detect manifests, build graphs, runtime entry points, database providers, test projects/targets,
   native test-list commands, result formats, coverage tools, and package/deployment outputs.
2. Inventory Oracle APIs and SQL from active code, generated sources, configuration, migrations,
   and packaging. Preserve architecture and public behavior; do not translate unrelated code.
3. Replace Oracle providers only at the selected database boundary. Preserve intentionally retained
   Oracle boundaries and package both providers when mixed operation is required.
4. Verify parameter binding, null/empty behavior, decimals, timestamps/time zones, generated keys,
   arrays, LOBs, refcursors/result sets, transaction boundaries, retries, pooling, and error mapping.
5. Run production paths against PostgreSQL in Docker with synthetic fixtures and cleanup proof.
6. Reconcile native discovery/list output with fresh native result files before coverage.

## .NET

### Detection

- Discover `.sln`, `.slnx`, `.csproj`, `.fsproj`, `Directory.Build.*`, central package management,
  NuGet lock files, publish profiles, Entity Framework migrations, and application configuration.
- Detect `Oracle.ManagedDataAccess`, `Oracle.ManagedDataAccess.Core`, `Oracle.DataAccess`,
  `System.Data.OracleClient`, Devart Oracle providers, Dapper, ADO.NET, EF6, and EF Core.

### Conversion

- Prefer `Npgsql` for ADO.NET and `Npgsql.EntityFrameworkCore.PostgreSQL` for EF Core. Derive a
  compatible version from the target framework and existing package-management policy.
- Convert Oracle connection/command/parameter/reader/transaction/provider-factory usage to the
  corresponding Npgsql or existing abstraction. Remove `BindByName` assumptions only after
  parameter behavior is tested.
- Map `OracleDbType`, numeric precision/scale, timestamps, intervals, GUID/raw values, arrays, LOBs,
  output parameters, refcursors, sequence access, bulk operations, and exception handling
  explicitly. Prefer PostgreSQL `RETURNING`, arrays, and `COPY` only when behavior is preserved.
- Convert EF provider registration, migrations, annotations, generated SQL, retry policy, and
  design-time factories. Inspect generated migrations before execution.
- Update dependency injection, `appsettings*.json`, environment bindings, health checks, publish
  outputs, trimming/AOT settings, and container images. Inspect the final package for Npgsql and
  unintended Oracle assemblies.

### Tests and Coverage

- Inventory xUnit `[Fact]`/`[Theory]`, NUnit `[Test]`/`[TestCase]`/`[TestCaseSource]`, and MSTest
  `[TestMethod]`/`[DataTestMethod]`.
- Detect the existing runner before selecting commands or dependencies: inspect `global.json`
  (`test.runner`), project/package references, build properties, entry points, and documented
  scripts. Native Microsoft.Testing.Platform (MTP) projects may use `xunit.v3.mtp-v2`,
  `Microsoft.Testing.Platform`, or framework-specific MTP integration and executable test hosts.
- Preserve native MTP projects. Do not require `Microsoft.NET.Test.Sdk` or a Visual Studio
  adapter, switch to VSTest, or add compatibility packages merely because this is a migration.
  Use the project's existing MTP discovery/execution commands and supported reporters. With
  .NET 10's MTP runner, `dotnet test --project <project> -- --list-tests` lists tests; run the
  same project with its configured reporters, such as `--report-trx` when the TRX extension is
  present. Use the existing coverage extension instead of injecting a VSTest data collector.
- For existing VSTest projects, verify their `Microsoft.NET.Test.Sdk` and matching framework
  adapter; use `dotnet test <project> --list-tests` and the existing VSTest result/coverage
  options. Do not add a different platform or replace a working runner.
- Reconcile discovered/executed test identities, counts, failures, and skips using fresh native
  results from the selected runner. TRX `UnitTestResult` and `TestMethod` records are one supported
  source when that runner emits TRX, not a requirement to install TRX for every platform. A
  buildable test assembly with no expected results is a failure, never proof of a missing
  VSTest adapter in an MTP project.
- Run PostgreSQL integration tests with an existing container harness or
  `Testcontainers.PostgreSql`; do not replace production Npgsql paths with mocks.
- Use the repository's existing MTP coverage extension, collector, Coverlet, `dotnet-coverage`,
  or Visual Studio coverage as appropriate to the existing platform.
  Persist framework lanes, integration, and overlap-safe merged coverage in Cobertura/OpenCover or
  native formats.

## Python

### Detection

- Discover `pyproject.toml`, `requirements*.txt`, constraints, lock files, Poetry/PDM/Pipenv/uv
  configuration, tox/nox, Django settings/migrations, Alembic, SQLAlchemy, and package entry points.
- Detect `cx_Oracle`, `oracledb`, SQLAlchemy Oracle dialects, Django Oracle backend, direct DB-API
  calls, and Oracle-specific migration/SQL files.

### Conversion

- Prefer Psycopg 3 for synchronous DB-API paths, `psycopg_pool` for pooling when required, and
  `asyncpg` only for existing async architectures. Use the PostgreSQL SQLAlchemy/Django backend for
  those frameworks.
- Convert connection factories, cursor/transaction management, parameter styles, executemany/bulk
  paths, row factories, generated keys, arrays, LOBs, refcursors, decimals, timestamps, and database
  exceptions explicitly.
- Preserve SQLAlchemy bound parameters rather than string interpolation. Convert engines, dialect
  options, Alembic environment/configuration, Django database settings, and migrations.
- Update dependencies and lock files through the repository's package manager. Inspect wheels,
  containers, serverless bundles, and deployment manifests for the PostgreSQL driver and unintended
  Oracle client requirements.

### Tests and Coverage

- Inventory pytest functions/classes/parameterization and `unittest.TestCase` methods. Detect tox,
  nox, and multiple supported Python environments.
- Run `pytest --collect-only` or `python -m unittest` discovery as applicable, save the exhaustive
  collected IDs, then run fresh JUnit XML per environment/framework. Reconcile every collected test
  container and minimum count.
- Treat missing plugins, import-time collection failures, deselection changes, unexpected skips,
  and changed markers as discovery failures.
- Use a real PostgreSQL driver with Docker/Testcontainers for database integration paths.
- Generate fresh coverage.py data per framework/environment, use parallel data files where needed,
  combine only after discovery passes, and report branch coverage when configured.

## C and C++

### Detection

- Discover CMake/CTest, Meson, Make, Ninja, Visual Studio solutions/projects, Conan, vcpkg, pkg-config,
  generated code, linker settings, runtime deployment, and platform-specific build presets.
- Detect OCI, OCCI, Pro*C/C++, Oracle ODBC, embedded SQL, direct client-library loading, and wrapper
  abstractions.

### Conversion

- Prefer the repository's database abstraction. Otherwise select `libpqxx` for idiomatic C++,
  `libpq` for C/low-level paths, or PostgreSQL ODBC when the architecture is already ODBC-based.
- Convert environment/connection/result/statement handles, prepared/bound parameters, status/error
  checks, transaction ownership, savepoints, pooling, binary/text formats, arrays, numerics,
  timestamps, LOBs, generated keys, bulk/COPY operations, and routine result sets explicitly.
- Preserve RAII, ownership, lifetime, thread-safety, exception/error-code policy, ABI, compiler
  matrix, and conditional compilation. Never mechanically replace OCI/OCCI calls without proving
  cleanup and result lifetimes.
- Update CMake/Meson/MSBuild/Make link dependencies, include paths, runtime library deployment,
  package manifests, container images, and install/export rules. Inspect final binaries and packages
  for PostgreSQL libraries and unintended Oracle client dependencies.

### Tests and Coverage

- Inventory GoogleTest/GoogleMock, Catch2, Boost.Test, CMocka, Unity, and CTest registrations.
- Reconcile source macros with runner discovery: `--gtest_list_tests`, Catch2 list reporters,
  Boost.Test listing, framework-specific C runners, and `ctest --show-only=json-v1`.
- Run fresh CTest JUnit output or framework JUnit reporters. Every registered executable and
  discovered test case must execute unless a reviewed platform constraint applies.
- Use CTest labels/configurations to run unit and PostgreSQL Docker integration tests separately.
  Building a test executable without registering/running it is not success.
- Use gcov/lcov/gcovr for GCC, `llvm-profdata`/`llvm-cov` for Clang, or the repository's MSVC
  collector. Record compiler/configuration-specific lanes and merge only compatible execution data.

## Unsupported or Custom Frameworks

Do not guess. Record the framework, source inventory, native list command, adapter/provider,
result format, and coverage format. Extend the baseline with generic `eligibleContainers`,
`representativeContainers`, and native report evidence. If exhaustive discovery cannot be proven,
report `ACTION NEEDED`/`PARTIAL` and do not generate or claim complete coverage.
