# Canonical Conversion Evidence Schema

`conversion-state.json` is sanitized machine evidence, not a free-form log. Persist these canonical
keys so report generation and `validate-migration-artifacts.mjs` use one contract.

Both assessment and conversion state also require `codingNotes`, including static early exits and
omitted inputs. Use the ingestion helper's exact provenance/digest/item inventory and the evaluation
fields defined in [coding-notes.md](./coding-notes.md). Never persist raw notes or private locators.
The report checklist is generated from this same object and verified against the current file.

```json
{
  "schemaVersion": 1,
  "runtimeExecution": {
    "schemaVersion": 1,
    "primaryActorId": "primary",
    "runId": "<run-id>",
    "checkpointSequence": 1,
    "journalDigest": "<SHA-256>",
    "boundary": "combined-final"
  },
  "scopeDecision": {
    "schemaVersion": 1,
    "status": "MIGRATION_REQUIRED",
    "reason": "<scope reason>",
    "scanLimits": {
      "maxFiles": 50000,
      "maxTextBytes": 100000000,
      "maxDurationMs": 30000,
      "candidateFiles": 1,
      "candidateTextBytes": 1
    },
    "scannedFiles": 1,
    "scannedAt": "<ISO-8601>",
    "inventoryDigest": "<SHA-256>",
    "gitBaselineAvailable": true,
    "baselineGitStatus": [],
    "executionArtifactBaseline": [
      { "path": "target", "exists": false, "mtimeMs": null, "treeDigest": null },
      { "path": "node_modules", "exists": false, "mtimeMs": null, "treeDigest": null }
    ],
    "detectedTechnologies": [
      { "name": "Java", "evidencePaths": ["pom.xml"] }
    ],
    "activeDatabaseFindings": [],
    "activeOracleFindings": [],
    "passiveArtifacts": [],
    "skippedFiles": [],
    "scannedFileDigests": [
      { "path": "pom.xml", "sha256": "<SHA-256>" }
    ],
    "executionSuppression": null
  },
  "plugin": {
    "name": "migrate-oracle-app",
    "version": "<plugin-version>"
  },
  "runMetrics": {
    "timeZone": "America/Los_Angeles",
    "assessment": {
      "status": "completed",
      "startedAt": "2026-06-10T23:10:00.000Z",
      "completedAt": "2026-06-10T23:13:00.000Z",
      "wallClockElapsedMs": 180000
    },
    "conversion": {
      "status": "completed",
      "startedAt": "2026-06-10T23:13:00.000Z",
      "completedAt": "2026-06-11T01:07:30.000Z",
      "wallClockElapsedMs": 6870000
    },
    "total": {
      "status": "completed",
      "startedAt": "2026-06-10T23:10:00.000Z",
      "completedAt": "2026-06-11T01:07:30.000Z",
      "wallClockElapsedMs": 7050000
    }
  },
  "postgresProvider": {
    "harness": "vscode-copilot",
    "provider": "vscode-postgresql-extension",
    "serverId": "PostgreSQL MCP",
    "toolFamily": "pgsql",
    "available": true,
    "fallbackAllowed": false,
    "fallbackAttempted": false,
    "profileStore": "vscode-postgresql-extension",
    "discovery": {
      "strategy": "exact-operation-names",
      "status": "available",
      "initialTools": [],
      "searches": [
        {
          "query": "pgsql_list_connection_profiles pgsql_connect pgsql_list_databases pgsql_get_server_capabilities pgsql_query pgsql_modify pgsql_disconnect",
          "status": "completed",
          "returnedTools": [
            "PostgreSQL MCP-pgsql_list_connection_profiles",
            "PostgreSQL MCP-pgsql_connect",
            "PostgreSQL MCP-pgsql_list_databases",
            "PostgreSQL MCP-pgsql_get_server_capabilities",
            "PostgreSQL MCP-pgsql_query"
          ]
        },
        {
          "query": "pgsql_modify",
          "status": "completed",
          "returnedTools": ["PostgreSQL MCP-pgsql_modify"]
        },
        {
          "query": "pgsql_disconnect",
          "status": "completed",
          "returnedTools": ["PostgreSQL MCP-pgsql_disconnect"]
        }
      ]
    },
    "selectedTools": {
      "listConnectionProfiles": "PostgreSQL MCP-pgsql_list_connection_profiles",
      "connect": "PostgreSQL MCP-pgsql_connect",
      "listDatabases": "PostgreSQL MCP-pgsql_list_databases",
      "getServerCapabilities": "PostgreSQL MCP-pgsql_get_server_capabilities",
      "query": "PostgreSQL MCP-pgsql_query",
      "modify": "PostgreSQL MCP-pgsql_modify",
      "disconnect": "PostgreSQL MCP-pgsql_disconnect"
    },
    "prohibitedOperations": [
      "postgres_mcp_db_context",
      "pgsql_db_context",
      "pgsql_visualize_schema"
    ],
    "mixedProviderOperationCount": 0,
    "reason": null
  },
  "targetMetadataAcquisition": {
    "strategy": "code-derived-bounded-catalog-queries",
    "dbContextToolsProhibited": [
      "postgres_mcp_db_context",
      "pgsql_db_context"
    ],
    "dbContextCalls": 0,
    "schemaWideCatalogQueries": 0,
    "broadInformationSchemaQueries": 0,
    "catalogSelectStarQueries": 0,
    "manifestObjectCount": 1,
    "identityBatchLimit": 20,
    "detailBatchLimit": 10,
    "queryCalls": 2,
    "toolCallCounts": {
      "postgres_mcp_db_context": 0,
      "pgsql_db_context": 0,
      "pgsql_query": 2
    },
    "queries": [
      {
        "id": "target-scalar-001",
        "purpose": "connection-identity-and-server-state",
        "boundedBy": "scalar",
        "objectCount": 0,
        "oidCount": 0,
        "maxExpectedRows": 1,
        "usesSelectStar": false,
        "usesInformationSchema": false,
        "queryDigest": "<SHA-256>",
        "startedAt": "<ISO-8601>",
        "completedAt": "<ISO-8601>",
        "elapsedMs": 1,
        "status": "passed"
      },
      {
        "id": "target-identity-001",
        "purpose": "manifest-object-identity",
        "boundedBy": "exact-name",
        "objectCount": 1,
        "oidCount": 0,
        "maxExpectedRows": 25,
        "usesSelectStar": false,
        "usesInformationSchema": false,
        "queryDigest": "<SHA-256>",
        "startedAt": "<ISO-8601>",
        "completedAt": "<ISO-8601>",
        "elapsedMs": 1,
        "status": "passed"
      }
    ],
    "timeouts": [],
    "policyViolations": []
  },
  "oracleDdlSource": {
    "supplied": true,
    "folderPath": "C:\\dev\\example-migrations\\Migration Projects",
    "schema": "CWF",
    "resolvedSchemaPath": "C:\\dev\\example-migrations\\Migration Projects\\.github\\postgres-migrations\\<user-provided-name>\\artifacts\\oracle\\CWF",
    "relativeSchemaPath": ".github/postgres-migrations/<user-provided-name>/artifacts/oracle/CWF",
    "layout": "canonical-postgres-migrations",
    "migrationName": "<user-provided-name>",
    "objectTypeDirectories": [
      {
        "name": "PROCEDURE",
        "relativePath": ".github/postgres-migrations/<user-provided-name>/artifacts/oracle/CWF/PROCEDURE",
        "expectedObjectKinds": ["procedure"]
      }
    ],
    "discoveredSchemaPaths": [
      ".github/postgres-migrations/<user-provided-name>/artifacts/oracle/CWF"
    ],
    "limits": {
      "maxFiles": 10000,
      "maxFileBytes": 10000000,
      "maxTotalBytes": 250000000,
      "maxLayoutDepth": 12,
      "maxLayoutDirectories": 50000
    },
    "totalBytes": 1,
    "status": "valid",
    "folderDigest": "<SHA-256 from oracle-ddl-index.json>",
    "indexPath": ".github/migrate-oracle-app/assessment/oracle-ddl-index.json",
    "indexSha256": "<SHA-256>",
    "candidateFiles": 1,
    "indexedObjects": 1,
    "files": [
      {
        "relativePath": ".github/postgres-migrations/<user-provided-name>/artifacts/oracle/CWF/PROCEDURE/p_update_status.sql",
        "objectTypeDirectory": "PROCEDURE",
        "expectedObjectKinds": ["procedure"],
        "identitySource": "ddl-primary-definition",
        "objectKind": "procedure",
        "oracleSchema": "CWF",
        "oracleObjectName": "P_UPDATE_STATUS",
        "displayIdentity": "CWF.P_UPDATE_STATUS",
        "identityKey": "CWF.P_UPDATE_STATUS",
        "objectKey": "procedure:CWF.P_UPDATE_STATUS",
        "bytes": 1,
        "sha256": "<SHA-256>",
        "statementKinds": ["procedure"],
        "packageMembers": [],
        "auxiliaryDefinitions": []
      }
    ],
    "errors": [],
    "warnings": [],
    "missingTargetObjects": [
      {
        "targetObject": "cwf.p_update_status(bigint)",
        "targetKind": "procedure",
        "oracleSourceIdentity": "CWF.P_UPDATE_STATUS",
        "oracleMemberName": null,
        "applicationCallSites": ["src/main/java/example/TaskDao.java"]
      }
    ],
    "missingObjectResolutions": [
      {
        "targetObject": "cwf.p_update_status(bigint)",
        "targetKind": "procedure",
        "resolutionStatus": "converted",
        "semanticsSource": "oracle-ddl",
        "oracleSourceIdentity": "CWF.P_UPDATE_STATUS",
        "sourceRelativePath": ".github/postgres-migrations/<user-provided-name>/artifacts/oracle/CWF/PROCEDURE/p_update_status.sql",
        "sourceSha256": "<SHA-256>",
        "matchMethod": "exact-schema-kind-name-and-signature",
        "signatureReconciled": true,
        "behaviorEvidence": [
          "Matched Oracle procedure body and application call contract."
        ],
        "sourceBodyAvailable": true,
        "bodyCompleteness": "complete",
        "semanticConfidence": "high",
        "businessSemanticsProven": true,
        "generatedFromInference": false,
        "assumptions": [],
        "limitations": [],
        "generatedDeployPath": "database-ddl/01-p-update-status/deploy.sql",
        "generatedDeploySha256": "<SHA-256>",
        "generatedUndoPath": "database-ddl/01-p-update-status/undo.sql",
        "generatedUndoSha256": "<SHA-256>",
        "applicationCallSitesUpdated": ["src/main/java/example/TaskDao.java"],
        "dockerValidated": true,
        "syntheticValidation": {
          "syntheticInputsCreated": 1,
          "fixtureRowsCreated": 1,
          "fixtureRowsRemaining": 0,
          "productionPathInvoked": true,
          "assertions": [
            "Expected status transition and zero marker rows after cleanup."
          ],
          "status": "passed",
          "limitation": null
        },
        "customerValidationRequired": false,
        "customerValidationAction": null,
        "persistentCreateRequested": false,
        "persistentCreateAttempted": false,
        "persistentCreateVerified": false,
        "persistentCreate": {
          "status": "not-requested",
          "authorization": null,
          "execution": null,
          "verification": null,
          "reason": "Option was off.",
          "nextAction": null
        },
        "unresolvedReason": null
      }
    ]
  },
  "assessmentBaseline": {
    "unitTests": 1,
    "noTestsReason": null
  },
  "options": {
    "establish-baseline": true,
    "fix-broken-tests": true,
    "create-persistent-ddl": false,
    "create-unit-tests": true,
    "create-integration-tests": true,
    "start-docker-desktop": false,
    "remove-unused-artifacts": false,
    "test-target-writes": false
  },
  "optionResults": {
    "create-persistent-ddl": {
      "requested": false,
      "status": "not-requested",
      "optionSatisfied": true,
      "applicableMissingObjects": 1,
      "reason": "Option was off.",
      "nextAction": null
    },
    "test-target-writes": {
      "requested": false,
      "status": "not-requested",
      "optionSatisfied": true,
      "applicableMutationScenarios": 0,
      "reason": "Option was off.",
      "nextAction": null
    }
  },
  "executiveSummary": {
    "conversionResult": "<plain-text answer describing what converted and how it went>",
    "customerActionSummary": "<plain-text answer describing what the customer must still do>",
    "scorecard": [
      {
        "area": "Application conversion",
        "result": "<selected boundary and conversion result>",
        "status": "passed"
      },
      {
        "area": "Build & package",
        "result": "<compile and deployable artifact result>",
        "status": "passed"
      },
      {
        "area": "Unit tests",
        "result": "<fresh reconciled unit-test result>",
        "status": "passed"
      },
      {
        "area": "PostgreSQL integration tests",
        "result": "<real PostgreSQL scenario result>",
        "status": "passed"
      },
      {
        "area": "Target validation",
        "result": "<contract, DDL, and authorized write result>",
        "status": "passed"
      },
      {
        "area": "Production readiness",
        "result": "<remaining customer-action boundary or ready statement>",
        "status": "action-needed"
      }
    ],
    "nextSteps": [
      {
        "priority": "Required",
        "area": "<customer-facing area/object>",
        "actionNeeded": "<specific executable customer action>",
        "owner": "<customer role/team>",
        "customerAction": true,
        "selectionReason": "production-blocker",
        "sourceEvidence": "<detailed report section or canonical evidence path>"
      }
    ],
    "recommendedProductionReadinessPath": [
      "<first required action>",
      "<deployment/runtime validation>",
      "<external integration validation>",
      "<remaining high-value test gaps>"
    ]
  },
  "orchestration": {
    "agentFacilityAvailable": true,
    "eligibleIndependentUnits": [
      "source-oracle-inventory",
      "test-build-inventory"
    ],
    "parallelRequired": true,
    "fallbackReason": null,
    "sharedPrimaryOnlyPaths": [
      ".github/migrate-oracle-app/**"
    ],
    "assignments": [
      {
        "id": "agent-source",
        "type": "explore",
        "objective": "Inventory active Oracle source/configuration findings.",
        "mode": "read-only",
        "unitIds": ["source-oracle-inventory"],
        "ownedPaths": [],
        "prohibitedPaths": [".github/migrate-oracle-app/**"],
        "databaseMutationAllowed": false,
        "startedAt": "<ISO-8601>",
        "completedAt": "<ISO-8601>",
        "status": "completed",
        "resultSummary": "Returned a bounded path/finding inventory.",
        "changedPaths": [],
        "commandsExecuted": 0,
        "testRuns": 0
      },
      {
        "id": "agent-tests",
        "type": "explore",
        "objective": "Inventory build graph, test frameworks, and native reports.",
        "mode": "read-only",
        "unitIds": ["test-build-inventory"],
        "ownedPaths": [],
        "prohibitedPaths": [".github/migrate-oracle-app/**"],
        "databaseMutationAllowed": false,
        "startedAt": "<ISO-8601>",
        "completedAt": "<ISO-8601>",
        "status": "completed",
        "resultSummary": "Returned test/build discovery evidence.",
        "changedPaths": [],
        "commandsExecuted": 0,
        "testRuns": 0
      }
    ],
    "parallelBatches": [
      {
        "id": "batch-discovery",
        "assignmentIds": ["agent-source", "agent-tests"],
        "startedAt": "<ISO-8601>",
        "completedAt": "<ISO-8601>"
      }
    ],
    "metrics": {
      "delegatedTasks": 2,
      "parallelBatches": 1,
      "delegatedCommands": 0,
      "delegatedTestRuns": 0,
      "ownershipViolations": 0
    }
  },
  "performanceExecution": {
    "buildSystem": "maven",
    "strategy": "precompiled-waves",
    "runnerScript": "<skill-directory>/scripts/run-maven-waves.mjs",
    "dependencyWarmup": {
      "phaseCount": 1,
      "commands": [
        "mvn dependency:go-offline",
        "mvn dependency:get -Dartifact=org.jacoco:org.jacoco.cli:<version>:jar:nodeps"
      ],
      "status": "passed"
    },
    "testCompile": {
      "count": 1,
      "command": "mvn -o test-compile -DskipTests",
      "compiledInputDigest": "<digest>",
      "invalidations": []
    },
    "waveRunner": {
      "directPluginGoals": true,
      "offline": true,
      "lifecycleCompileInvocations": 0,
      "uniqueReportDirectories": 4,
      "uniqueExecutionDataFiles": 4,
      "measurementStartedAt": "<first-current-generation-wave-ISO-8601>"
    },
    "dockerEnvironment": {
      "scope": "migration-run",
      "containerStartCount": 1,
      "invalidations": [],
      "containerIdDigest": "<SHA-256>",
      "sharedAcrossTestProcesses": true,
      "ownershipVerified": true,
      "readinessVerified": true,
      "endpointBindingsVerified": true,
      "fixtureIsolationVerified": true,
      "cleanupVerified": true
    },
    "fullSuite": {
      "generationCount": 1,
      "runnerExecutions": 1,
      "directPrecompiledExecution": true,
      "status": "passed"
    },
    "coverage": {
      "mergeTool": "jacoco-cli",
      "execPerWave": true,
      "nativeTestXmlPerWave": true,
      "htmlGenerationCount": 1,
      "combinedXml": ".github/migrate-oracle-app/conversions/coverage/waves/combined.xml"
    },
    "packaging": {
      "prePackageCheck": "passed",
      "cleanPackageCount": 1,
      "testsSkippedBecauseFullSuitePassed": true,
      "archivesInspected": 2,
      "status": "passed"
    },
    "agentScheduling": {
      "backgroundNotifications": true,
      "blockingWaitSecondsWhileIndependentWorkReady": 0
    },
    "targetMetadata": {
      "batchedReadOnlyChecks": true,
      "parallelBatches": 1,
      "checks": ["identity", "privileges", "locks", "recovery"],
      "databaseMutationsParallelized": false
    }
  },
  "validation": {
    "unitTests": {
      "status": "passed",
      "tests": 1,
      "failures": 0,
      "errors": 0,
      "skipped": 0
    },
    "testReconciliation": {
      "baseline": 1,
      "final": 1,
      "missing": [],
      "explanation": "All baseline tests were retained."
    },
    "testDiscovery": {
      "status": "passed",
      "baselinePath": ".github/migrate-oracle-app/assessment/test-discovery-baseline.json",
      "baselineSha256": "<SHA-256 of canonical JSON>",
      "fullSuite": {
        "command": "mvn test",
        "startedAt": "<ISO-8601>",
        "completedAt": "<ISO-8601>",
        "reportFiles": [
          { "path": "TestResults/complete.trx", "format": "trx" }
        ],
        "tests": 1,
        "failures": 0,
        "errors": 0,
        "skipped": 0
      },
      "frameworks": [
        {
          "id": "dotnet-xunit",
          "sourceFrameworkId": "dotnet-xunit",
          "language": "C#",
          "adapterAvailable": true,
          "adapterVersion": "<effective version>",
          "sourceFiles": 1,
          "testCases": 1,
          "eligibleContainers": 1,
          "executedContainers": 1,
          "executedTests": 1,
          "representativeContainersExecuted": ["Example.Tests.CurrentTests"],
          "missingContainers": []
        }
      ],
      "runnerPolicy": {
        "failIfNoTests": true,
        "failIfNoSpecifiedTests": true,
        "skipTests": false,
        "testFailureIgnore": false,
        "newExclusions": [],
        "unexplainedDisabledTests": []
      },
      "failureRepair": {
        "fixBrokenTestsEnabled": true,
        "exposedFailures": 0,
        "exposedErrors": 0,
        "exposedFailureClasses": [],
        "repairedTests": [],
        "repairAttempts": [],
        "unresolvedFailureClasses": [],
        "remainingFailures": 0,
        "remainingErrors": 0,
        "status": "passed"
      },
      "coverageEligibility": {
        "discoveryReconciled": true,
        "reconciledAt": "<ISO-8601>",
        "coverageGeneratedAfterReconciliation": true,
        "coverageArtifactsFresh": true
      }
    },
    "dockerPostgreSqlRuntime": {
      "status": "passed",
      "tests": 1
    },
    "dockerEngineAvailability": {
      "optionEnabled": false,
      "engineRunningAtPreflight": true,
      "desktopDetected": null,
      "startAttempted": false,
      "desktopStateChanged": false,
      "startupCommand": null,
      "engineResponsive": true,
      "status": "already-running",
      "reason": "Docker engine was already running; Docker Desktop was not launched."
    },
    "unusedArtifactDisposition": {
      "optionEnabled": false,
      "candidates": [
        {
          "path": "lib/DealOutlookUI.jar",
          "kind": "jar",
          "classification": "unused",
          "beforeDigest": "<SHA-256>",
          "unusedEvidence": [
            "No active source references.",
            "Not present in the required runtime classpath."
          ],
          "activeReferences": 0,
          "requiredByBuild": false,
          "requiredByPackage": false,
          "action": "retained",
          "removed": false,
          "rollback": null,
          "postRemovalValidation": null
        }
      ],
      "deletedPaths": []
    },
    "runtimeDriverPackaging": {
      "required": true,
      "status": "passed",
      "planPath": ".github/migrate-oracle-app/conversions/driver-packaging-plan.json",
      "descriptorSourcePaths": [
        "src/assembly/wlp/prod/server.xml"
      ],
      "archivesInspected": 1,
      "warOnlyDriverDetected": false,
      "reason": null
    },
    "targetWriteTests": {
      "requested": false,
      "status": "not-requested",
      "optionSatisfied": true,
      "applicableMutationScenarios": 0,
      "writeAttempted": false,
      "contextRefreshedAt": null,
      "savedProfile": null,
      "profileAccessMode": null,
      "modifyToolAvailable": null,
      "defaultTransactionReadOnly": null,
      "inspectionTransactionReadOnly": null,
      "serverInRecovery": null,
      "safetyChecks": {
        "identity": null,
        "role": null,
        "privileges": null,
        "contracts": null,
        "locks": null
      },
      "dockerPrerequisitesPassed": null,
      "mcpModificationScenarios": 0,
      "markerRowsInserted": 0,
      "markerRowsDeleted": 0,
      "remainingMarkerRows": 0,
      "customerRowsUpdated": 0,
      "customerRowsDeleted": 0,
      "sequenceValuesConsumed": [],
      "mcpReads": 0,
      "mcpWrites": 0,
      "reason": "Option was off.",
      "nextAction": null
    },
    "artifactValidation": {
      "command": "node <skill-directory>/scripts/validate-migration-artifacts.mjs <codebase> conversion",
      "exitCode": 0,
      "validatedAt": "<ISO-8601>"
    }
  },
  "coverage": {
    "unavailableReason": null,
    "lanes": [
      {
        "id": "dotnet-xunit",
        "status": "passed",
        "executionDataPath": "TestResults/xunit.coverage",
        "reportPath": "TestResults/xunit/coverage.cobertura.xml",
        "generatedAt": "<ISO-8601>"
      },
      {
        "id": "integration",
        "status": "passed",
        "executionDataPath": "target/coverage-reports/jacoco-integration.exec",
        "reportPath": "target/coverage-reports/integration/jacoco.xml",
        "generatedAt": "<ISO-8601>"
      },
      {
        "id": "combined",
        "status": "passed",
        "executionDataPath": "target/coverage-reports/jacoco-combined.exec",
        "reportPath": "target/coverage-reports/combined/jacoco.xml",
        "generatedAt": "<ISO-8601>"
      }
    ],
    "wavePolicy": {
      "unitMinimum": 2,
      "integrationMinimum": 2,
      "recommended": 2,
      "maximum": 10,
      "aspirationalCombinedLinePercentage": 80,
      "readinessGate": false
    },
    "baseline": {
      "combined": {
        "line": { "covered": 1, "total": 10 },
        "branch": { "covered": 1, "total": 2 }
      }
    },
    "final": {
      "combined": {
        "line": { "covered": 2, "total": 10 },
        "branch": { "covered": 1, "total": 2 }
      }
    },
    "unitWaves": [
      { "wave": 1, "plannedBehaviors": ["<behavior>"], "testsAddedOrRepaired": ["<test>"], "result": "passed" },
      { "wave": 2, "kind": "validation", "plannedBehaviors": ["<behavior>"], "testsAddedOrRepaired": [], "validatedTests": ["<executed test>"], "result": "passed" }
    ],
    "integrationWaves": [
      { "wave": 1, "plannedBehaviors": ["<behavior>"], "testsAddedOrRepaired": ["<test>"], "result": "passed" },
      { "wave": 2, "kind": "validation", "plannedBehaviors": ["<behavior>"], "testsAddedOrRepaired": [], "validatedTests": ["<executed test>"], "result": "passed" }
    ],
    "rankedBacklogFinal": [
      {
        "area": "<area>",
        "classification": "external-system",
        "safelyTestable": false,
        "nextAction": "<exact action>"
      }
    ],
    "stopReason": "<exact stop reason>"
  },
  "fixtures": {
    "noRowsReason": null,
    "ledger": [
      {
        "environment": "Docker PostgreSQL",
        "table": "schema.table",
        "created": 1,
        "cleaned": 1,
        "remaining": 0
      }
    ],
    "databaseObjectTestMatrix": [
      {
        "kind": "function",
        "active": 0,
        "exercised": 0,
        "evidence": "No active code-referenced function."
      },
      {
        "kind": "procedure",
        "active": 0,
        "exercised": 0,
        "evidence": "No active code-referenced procedure."
      },
      {
        "kind": "sequence",
        "active": 1,
        "exercised": 1,
        "identity": "schema.sequence",
        "entryPoint": "ProductionClass.method",
        "invocation": "SELECT nextval through production driver",
        "fixtures": "none",
        "assertions": "<assertions>",
        "sideEffect": "<side effect and cleanup>",
        "environment": "Docker PostgreSQL",
        "result": "passed"
      },
      {
        "kind": "table",
        "active": 1,
        "exercised": 1,
        "evidence": "<fixture/query evidence>"
      },
      {
        "kind": "view",
        "active": 0,
        "exercised": 0,
        "evidence": "No active view in tested scope."
      },
      {
        "kind": "trigger",
        "active": 0,
        "exercised": 0,
        "evidence": "No active trigger in tested scope."
      },
      {
        "kind": "native-query-path",
        "active": 1,
        "exercised": 1,
        "identity": "Repository.method(args)",
        "entryPoint": "Repository.method",
        "invocation": "Spring Data/Hibernate native SQL",
        "fixtures": "schema.table=1",
        "assertions": "<assertions>",
        "sideEffect": "<cleanup proof>",
        "environment": "Docker PostgreSQL",
        "result": "passed"
      }
    ]
  },
  "conversionScope": {
    "manifestDigest": "<SHA-256 of code-derived Oracle finding manifest>",
    "categories": [
      {
        "category": "runtime-datasource",
        "discovered": 1,
        "converted": 1,
        "retained": 0,
        "deferred": 0,
        "safeConvertibleRemaining": 0,
        "evidence": "Selected CWF datasource converted to PostgreSQL environment bindings."
      }
    ],
    "findings": [
      {
        "id": "runtime-datasource:server.xml:jdbc/cwf",
        "path": "server.xml",
        "category": "runtime-datasource",
        "status": "converted",
        "safeToConvert": true,
        "reason": "Selected CWF boundary uses PostgreSQL placeholders."
      }
    ]
  },
  "fileInventory": {
    "noModifiedTrackedFilesReason": null
  }
}
```

Rules:

- Every option field is an explicit boolean. Persist option outcomes separately if needed.
- Run `detect-migration-scope.mjs` before dependency installation, build, tests, target discovery,
  Docker, or edits. Persist its result as `scopeDecision`.
- When `scopeDecision.status` is `NO_CONVERSION_REQUIRED`, stop after reporting detected
  technologies and the zero active database/Oracle findings. Every execution-suppression field is
  false, passive artifacts are retained, no application file changes exist, and no test-discovery,
  coverage, file-diff, Docker, target, or DDL evidence is produced.
- `NO_CONVERSION_REQUIRED` requires a detected technology, zero `skippedFiles`, an inventory digest
  reproducible by a fresh scan, and a Git status baseline. Unreadable, oversized, symbolic-link, or
  unknown non-inert files produce `SCOPE_REVIEW_REQUIRED` and stop execution for focused review.
- `options.start-docker-desktop` and `validation.dockerEngineAvailability` are mandatory. An
  already-running engine is usable without the option and must not be relaunched.
- When `start-docker-desktop` is false and the engine was not running, `startAttempted`,
  `desktopStateChanged`, and `startupCommand` remain false/false/null. Docker runtime validation
  cannot be passed and the report records the exact limitation.
- When `start-docker-desktop` is true, a start is valid only for a detected installed Desktop,
  exactly one bounded attempt, a recorded action/command, an observed state change, and a responsive
  engine. The option never authorizes installation, update, settings/autostart changes, or restart
  loops.
- `options.remove-unused-artifacts` and `validation.unusedArtifactDisposition` are mandatory.
  Every unused candidate records path, kind, digest, reference/build/package evidence, action, and
  removal state.
- When `remove-unused-artifacts` is false, every unused candidate is retained and
  `deletedPaths` is empty. When true, a removed candidate requires zero active references, no
  build/package requirement, unchanged digest, exact rollback, and passing post-removal
  build/tests/package/archive checks.
- `validation.runtimeDriverPackaging` is mandatory. When source runtime descriptors reference
  PostgreSQL, `required=true` and `inspect-driver-packaging.mjs` must prove every packaged descriptor
  has at least one driver at every configured runtime archive pattern.
- A PostgreSQL JAR found only inside `WEB-INF/lib` or `WEB-INF/lib-provided` does not satisfy an
  application-server descriptor that loads an external library path. Missing archives, missing
  descriptors, wrong filenames/paths, and WAR-only copies fail packaging readiness and prevent a
  successful conversion report.
- `orchestration` is mandatory. When the client exposes child agents and at least two
  `eligibleIndependentUnits` exist, `parallelRequired` is true and at least two assignments must
  overlap in a recorded parallel batch. Every eligible unit is assigned exactly once.
- Write-enabled child assignments require exclusive non-overlapping `ownedPaths`. Child agents
  never mutate PostgreSQL or own `.github/migrate-oracle-app/**`; the primary agent owns database
  writes, canonical state, reports, merge decisions, and artifact hashes.
- When child agents are unavailable, record the exact client/tool limitation in `fallbackReason`.
  "Not needed" is invalid when independent eligible units existed.
- `performanceExecution` is mandatory. Maven runs use `precompiled-waves`: one dependency warmup
  phase, one `test-compile` per changed input/output generation, direct offline Surefire/Failsafe
  execution, unique XML/exec evidence per logical wave, JaCoCo CLI merge, one complete suite per
  validated generation, one pre-package check, and one final clean package with archive inspection.
  Compilation count equals one plus the documented invalidations. Already passed unrestricted
  validation waves can satisfy the complete-suite gate without duplicate launches.
- Passed Docker runtime tests require `performanceExecution.dockerEnvironment` with one creation
  per configuration generation, exact identity/ownership, shared-process binding, fixture isolation,
  and final cleanup proof. Primary-owned setup persists only within the same migration invocation.
- Retain actual per-step elapsed milliseconds and `waveRunner.measurementStartedAt` for current
  coverage execution freshness. The normal Maven cache survives invocations; compiled preparation
  can be reused only with matching same-run input/output/build-environment proof.
- Per-wave Maven commands must not invoke compile/test/package lifecycles. Generate HTML zero or one
  times only. When source changes invalidate compiled inputs, record the digest invalidation and
  restart the compile-once generation rather than executing stale classes.
- Background agents use notification-driven completion with zero blocking wait seconds while
  independent primary work is ready. Independent target metadata reads may run in parallel;
  database mutations never do.
- `optionResults.test-target-writes` and `validation.targetWriteTests` are mandatory. When the option
  is on and at least one code-derived production write path applies, the option is satisfied only by
  a passed target-write execution through the production PostgreSQL driver with a recorded database
  write and zero-row cleanup.
- A requested target-write gate that cannot run is `not-executed`, not `deferred` or `passed`.
  Record the exact reason and next action, set conversion status to `PARTIAL`, and show
  `⚠️ Not executed` in the report. Do not use `RUNTIME_VALIDATION_NEEDED` for an explicitly requested
  but unexecuted write option.
- `not-applicable` is valid only when the code-derived manifest contains zero state-mutating target
  paths and the exact evidence is recorded. Read-only access is not `not-applicable`.
- A passed target-write result requires a saved `rw` MCP profile, available
  `postgres_mcp_modify`, `default_transaction_read_only=off`, a primary server, every safety check,
  Docker proof, all applicable modification scenarios, positive recorded write-operation count, no
  customer-row changes, every sequence value, and zero remaining marker rows.
- `postgres_mcp_query` deliberately runs inside a read-only inspection transaction. Record
  `inspectionTransactionReadOnly=true` separately; never use it to classify the saved profile or
  server as read-only.
- `assessmentBaseline.unitTests` is copied from the authoritative enabled baseline. Use
  `noTestsReason` only when the repository legitimately has no executable tests.
- Final test totals must reconcile with the baseline. Missing tests require names and explanations.
- `validation.testDiscovery` must reconcile with the reviewed
  `assessment/test-discovery-baseline.json`. Every detected framework requires an available
  runner/adapter, fresh native reports, representative-test execution, no missing eligible
  containers, and no unexplained count decrease.
- `validation.testDiscovery.baselineSha256` is SHA-256 over `JSON.stringify` of the parsed baseline;
  conversion must fail if the reviewed baseline changes without an intentional review/update.
- `targetMetadataAcquisition` is mandatory for target-backed assessment and conversion. The only
  valid active strategy is `code-derived-bounded-catalog-queries`; code-only runs use
  `not-requested` with an exact reason and zero queries.
- `dbContextCalls`, every `_db_context` entry in `toolCallCounts`,
  `schemaWideCatalogQueries`, `broadInformationSchemaQueries`, and `catalogSelectStarQueries` must
  all be zero. Any `_db_context` attempt is a policy violation and prevents artifact validation
  even when the call timed out or its response was discarded.
- Identity batches contain at most 20 exact same-kind manifest names. Detail batches contain at
  most 10 returned OIDs. Every bounded query records its scope, row ceiling, digest, timestamps,
  elapsed time, and whether it used prohibited broad patterns.
- `postgresProvider` is mandatory before target-backed work. `vscode-copilot` selects only
  `vscode-postgresql-extension` / `PostgreSQL MCP` / `pgsql_*`; `copilot-cli` selects only
  `bundled-postgres-mcp` / `postgres-mcp` / `postgres_mcp_*`.
- `fallbackAllowed` and `fallbackAttempted` remain false and `mixedProviderOperationCount` remains
  zero. Provider unavailability requires an exact reason and makes authoritative target evidence
  unavailable; it never triggers the other provider.
- Provider/profile-store changes invalidate every target contract, DDL authorization, target-write
  authorization, query/modify count, and cleanup result. Start a fresh run rather than switching
  provider in place.
- `oracleDdlSource` is mandatory state even when `oracle-ddl-folder` is not supplied. The
  not-supplied shape uses `supplied=false`, `status=not-supplied`, null folder/index digests, empty
  files/errors/warnings, and a complete `missingObjectResolutions` list for any missing target
  objects.
- When supplied, `oracleDdlSource` mirrors the sanitized `oracle-ddl-index.json`; never persist DDL
  bodies. `folderPath` is the user-supplied migration-project/ancestor root. Persist the selected
  schema, absolute/relative resolved schema path, canonical/flexible layout, user-provided migration
  name when present, every discovered object-type directory (without requiring a fixed set),
  discovered schema candidates, primary identities, filename-fallback warnings, auxiliary
  definitions, package members, and folder/index/file digests. All evidence must match the current
  read-only folder.
- Every code-derived missing target object is reconciled exactly once. Valid semantic sources are
  `oracle-ddl`, `code-and-tests`, `best-effort-reconstruction`, and
  `signature-only-fail-closed`.
- Every converted resolution records source-body availability, body completeness
  (`complete`/`inferred`/`missing-fail-closed`), semantic confidence, whether business semantics are
  proven, inference assumptions/limitations, deploy/undo hashes, all application call sites,
  synthetic validation, persistent result, and customer-validation action.
- `options.create-persistent-ddl` is an explicit boolean and `optionResults.create-persistent-ddl`
  is mandatory. Its `requested` value matches the option and `applicableMissingObjects` equals the
  complete `oracleDdlSource.missingTargetObjects` count. Valid aggregate results are `not-requested`,
  `not-applicable` only for zero missing objects, `completed`, `not-executed`, and `failed`.
  `optionSatisfied=true` requires the option to be off, zero missing objects, or every missing
  object to be created and verified or already present with a freshly verified matching contract.
- Every missing-object resolution has `persistentCreateRequested` matching the option and a
  `persistentCreate` object with `status`, `authorization`, `execution`, `verification`, `reason`,
  and `nextAction`. Valid per-object statuses are `not-requested`, `not-executed`, `created`,
  `already-present`, and `failed`. Only `created` and `failed` have
  `persistentCreateAttempted=true`; only `created` has `persistentCreateVerified=true`.
  An `already-present` result proves the target contract without claiming this run created it.
- A created/failed attempt records `persistentCreate.authorization` with `source=current-request`,
  `sourceDigest` (SHA-256), `contextRefreshedAt` (ISO-8601), matching `authorizedTargetDigest` and
  `refreshedTargetDigest` (SHA-256), exact `database` and `schema`, `savedProfile=true`,
  `profileAccessMode=rw`, `modifyToolAvailable=true`, `defaultTransactionReadOnly=false`, and
  `serverInRecovery=false`. Its `safetyChecks` has true `identity`, `role`, `privileges`, `absence`,
  `dependencies`, `locks`, `createOnly`, and `dockerValidation` values. Do not record a validated
  writable missing object as `not-executed` merely because deploy/undo files were generated.
- `persistentCreate.execution` records `tool` equal to `postgresProvider.selectedTools.modify`,
  `targetDigest` matching the authorized target, `ddlSha256` matching the current
  `generatedDeploySha256`, `executedAt` no earlier than context refresh, `succeeded`, and `error`.
  Successful creation requires `succeeded=true` and `error=null`; failed or indeterminate execution
  uses `succeeded=false` or `null` with an explicit error.
- `persistentCreate.verification` for `created`/`already-present` records `tool` equal to
  `postgresProvider.selectedTools.query`, matching `targetDigest`, `expectedDdlSha256` equal to
  `generatedDeploySha256`, exact `objectIdentity`/`objectKind`, `contractMatches=true`, and
  `verifiedAt` no earlier than execution or, for an existing object, context refresh. An
  `already-present` result still records current-request/context/profile identity authorization,
  but needs no write capability, execution, or absence proof.
- When off, per-object authorization/execution/verification are null. Unattempted creation has
  `execution=null`; unverified/failed creation has `verification=null`. Requested but incomplete
  creation requires exact per-object and aggregate `reason`/`nextAction` and makes conversion
  `PARTIAL` (or exceptionally `BLOCKED` for a failed write), never `RUNTIME_VALIDATION_NEEDED`.
  Report the matching `Created and verified`, `Already present and verified`, `Not executed`,
  `Failed`, or `Option off` result in each Oracle DDL Source Reconciliation row.
- `best-effort-reconstruction` and `signature-only-fail-closed` require at least one synthetic
  input, production-path Docker execution, assertions, zero remaining fixture rows, nonempty
  limitations, `businessSemanticsProven=false`, and `customerValidationRequired=true`.
- A `signature-only-fail-closed` deploy body raises an explicit error. Silent no-op,
  unconditional-success, fabricated mutation, and fabricated result bodies are invalid.
- The overall conversion cannot be `READY` while any resolution requires customer validation.
  Persistent creation is still permitted by `create-persistent-ddl`, but it does not remove the
  warning or production-readiness action.
- Keep the four runner safety fields at their fail-closed values. New exclusions and unexplained
  disabled tests must remain empty.
- Broken tests block test readiness. When `fix-broken-tests` is enabled, record exposed failures,
  errors, exact failure classes, per-class repair attempts, repairs, unresolved classes, and a
  complete-suite rerun. Pre-existing classification does not remove a discovered failure from
  repair scope.
- Every exposed failure class requires a recorded repair attempt. An unresolved class requires three
  materially different attempts or an exact `unsafeToRepair` record with classification, reason,
  and next action. Conversion discovery passes only with zero remaining failures/errors and no
  unresolved classes.
- When `create-unit-tests` is on, persist at least two passed `unitWaves`. When
  `create-integration-tests` is on, independently persist at least two passed `integrationWaves`.
  Each lane has a ten-wave maximum; waves in one lane never count toward the other.
  Waves above two need `continuationEvidence` with `uncoveredBehavior`, `reason`,
  `expectedNewCoveredLines`, and `expectedNewCoveredBranches`; the expected counters must be
  nonnegative integers with a positive sum.
  A safely testable `deferred-low-yield` gap also needs `priority=low`, source/measurement `evidence`,
  those expected counters, positive `estimatedAdditionalRuntimeMs`, and an exact `nextAction`.
  Persist `coverage.stopDecision.kind=low-marginal-value` and its reason. High-risk gaps cannot use
  this value-based deferral.
- Coverage is valid only after discovery reconciliation. Record separate applicable
  framework/toolchain, integration, and combined lanes; every successful lane needs current
  execution data and a report generated after reconciliation. An unavailable lane requires an
  exact reason.
- Persist all seven database object kinds. Zero counts are valid and mandatory.
- Persist an exhaustive `conversionScope` manifest for active Oracle provider, datasource,
  configuration, SQL/query, routine, sequence, mapping, migration-script, packaging, and deployment
  findings. Category counts must reconcile exactly to findings.
- `safeConvertibleRemaining` must be zero. A deferred finding with `safeToConvert=true` is invalid;
  test failures, missing secrets, or missing database routines do not justify leaving independent
  source/configuration conversion undone.
- Modified-file count is evidence, not a target. A small diff is valid only when every discovered
  finding is converted, intentionally retained, or deferred with an exact unsafe/external reason.
- `executiveSummary` is canonical report-opening evidence. Keep its scorecard at exactly six rows in
  the documented order. Keep `nextSteps` to one through five `Required`/`Recommended` customer
  actions; each action needs a customer owner, selection reason, and source evidence. Do not promote
  internal report maintenance, file inventory, or every technical warning into the handoff.
- `recommendedProductionReadinessPath` contains two through five ordered, executable customer steps.
  The report's two opening paragraphs, scorecard, action table, and path must match these canonical
  values.
- Use `fixtures.noRowsReason` only when the tested production paths truly require no rows.
- Use `coverage.unavailableReason` instead of invented counters.
- Every generation wave records distinct planned behavior, tests added/repaired, and a passed result.
  A `kind=validation` wave instead records its `validatedTests` and may have no added/repaired tests.
  Do not stop either enabled lane before wave two.
- Assemble state/report at a phase/final boundary through `migration-runtime.mjs finalize`.
  `runtimeExecution` is generated from actual primary ownership, checkpoint and assembly receipts,
  never hand-written. The coordinator runs artifact record and independent verification with
  `--require-runtime`; the validator reconstructs checkpoint state and checks report/state receipts.
  Its own `validation.artifactValidation` bookkeeping is the only permitted post-assembly mutation.
