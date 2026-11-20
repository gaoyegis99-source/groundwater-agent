# Test Discovery Baseline Schema

Persist the reviewed test-discovery baseline at:

```text
.github/migrate-oracle-app/assessment/test-discovery-baseline.json
```

This file is source-derived evidence. A successful build command is not a substitute for
runner/adapter availability or container/case-level execution reconciliation.

`assessment-data.json` records `testDiscovery.status`, the canonical baseline path, and
`baselineSha256` (SHA-256 over `JSON.stringify` of the parsed baseline). Conversion copies and
verifies the same digest; do not silently rewrite the baseline to match a lower result.

```json
{
  "schemaVersion": 2,
  "capturedAt": "<ISO-8601>",
  "buildSystems": ["dotnet"],
  "frameworks": [
    {
      "id": "dotnet-xunit",
      "sourceFrameworkId": "dotnet-xunit",
      "language": "C#",
      "sourceFramework": "xUnit",
      "requiredAdapter": "xunit.runner.visualstudio",
      "adapterVersion": "<effective version>",
      "adapterAvailable": true,
      "reportFormat": "trx",
      "coverageLaneId": "dotnet-xunit",
      "sourceFiles": 1,
      "testCases": 1,
      "eligibleContainers": ["Example.Tests.CurrentTests"],
      "representativeContainers": ["Example.Tests.CurrentTests"],
      "minimumExecutedContainers": 1,
      "minimumExecutedTests": 1
    }
  ],
  "adapterEvidence": {
    "commands": ["dotnet list package", "dotnet test --list-tests"],
    "availableAdapters": [
      "Microsoft.NET.Test.Sdk:<version>",
      "xunit.runner.visualstudio:<version>"
    ]
  },
  "runnerPolicy": {
    "failIfNoTests": true,
    "failIfNoSpecifiedTests": true,
    "skipTests": false,
    "testFailureIgnore": false,
    "newExclusions": [],
    "unexplainedDisabledTests": []
  },
  "intentionalExclusions": [],
  "review": {
    "status": "approved",
    "reviewedAt": "<ISO-8601>",
    "reason": "Authoritative enabled baseline after engine reconciliation."
  }
}
```

Rules:

- Inventory source files, test cases, and stable eligible containers before running tests. A
  container is a Java/.NET test class, Python module/class, C/C++ suite/case, or equivalent native
  discovery identity.
- Map source frameworks independently from execution runners/adapters. Under JUnit Platform, map
  JUnit 3/4 sources to `junit-vintage` and JUnit 5 sources to `junit-jupiter`. A project using
  Surefire's non-platform JUnit 4 provider may record that provider instead of Vintage when fresh
  report reconciliation proves execution. Record .NET, Python, and C/C++ frameworks with their
  actual adapters/runners.
- Inspect the effective dependency/build graph and native list/collect output. A source framework
  with no compatible runner/adapter is `ACTION NEEDED`, even when the build exits successfully.
- Add a missing adapter as the smallest baseline-enablement dependency/configuration change when
  `establish-baseline` is on. Use dependency management already present in the project; do not
  hardcode a version when the parent/BOM manages it.
- `eligibleContainers` is exhaustive. Every container/case must appear in fresh native test reports
  unless `intentionalExclusions` contains its `containerId`, exact reason, and explicit review
  timestamp. New exclusions and unexplained disabled tests are prohibited.
- Minimum container/test counts come from the first complete passing reconciled suite. Reductions
  require an intentional reviewed baseline update, never an automatic rewrite.
- Keep `failIfNoTests` and `failIfNoSpecifiedTests` enabled. `skipTests`, failure-ignore settings,
  unexplained disabled tests, and new runner exclusions invalidate the baseline.

The runner-policy fields are normalized semantics, not necessarily literal option names. For
.NET, Python, and C/C++, record `failIfNoTests`/`failIfNoSpecifiedTests` as true only when wrapper or
CI checks reject empty full-suite and filtered discovery results; record skip/failure-ignore as
false only after inspecting the effective command/configuration.

Supported source inventory IDs include `junit4`, `junit5`, `dotnet-xunit`, `dotnet-nunit`,
`dotnet-mstest`, `python-pytest`, `python-unittest`, `cpp-gtest`, `cpp-catch2`,
`cpp-boost-test`, `c-cmocka`, and `c-unity`. Native report formats include JUnit-compatible XML
(Surefire, pytest, CTest, and framework reporters) and .NET TRX.
