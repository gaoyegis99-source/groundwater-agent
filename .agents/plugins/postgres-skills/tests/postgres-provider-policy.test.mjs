import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  planPostgresToolDiscovery,
  resolvePostgresProvider,
  validatePostgresProviderUse,
} from "../skills/migrate-oracle-app/scripts/postgres-provider-policy.mjs";

const extensionTools = [
  "PostgreSQL MCP-pgsql_list_connection_profiles",
  "PostgreSQL MCP-pgsql_connect",
  "PostgreSQL MCP-pgsql_list_databases",
  "PostgreSQL MCP-pgsql_get_server_capabilities",
  "PostgreSQL MCP-pgsql_query",
  "PostgreSQL MCP-pgsql_modify",
  "PostgreSQL MCP-pgsql_disconnect",
  "PostgreSQL MCP-pgsql_db_context",
];

const bundledTools = [
  "postgres-mcp-postgres_mcp_list_connection_profiles",
  "postgres-mcp-postgres_mcp_connect",
  "postgres-mcp-postgres_mcp_list_databases",
  "postgres-mcp-postgres_mcp_get_server_capabilities",
  "postgres-mcp-postgres_mcp_query",
  "postgres-mcp-postgres_mcp_modify",
  "postgres-mcp-postgres_mcp_disconnect",
  "postgres-mcp-postgres_mcp_db_context",
];

test("VS Code Copilot selects extension tools even when bundled tools are visible", () => {
  const route = resolvePostgresProvider({
    harness: "vscode-copilot",
    availableTools: [...bundledTools, ...extensionTools],
  });

  assert.equal(route.available, true);
  assert.equal(route.provider, "vscode-postgresql-extension");
  assert.equal(
    route.selectedTools.query,
    "PostgreSQL MCP-pgsql_query",
  );
  assert.equal(route.fallbackAttempted, false);
});

test("VS Code Copilot does not fall back to bundled tools", () => {
  const route = resolvePostgresProvider({
    harness: "vscode-copilot",
    availableTools: bundledTools,
  });

  assert.equal(route.available, false);
  assert.equal(route.provider, "vscode-postgresql-extension");
  assert.match(route.error, /requires the Microsoft PostgreSQL extension/);
  assert.match(route.error, /do not fall back to the bundled NPM server/);
});

test("Copilot CLI selects bundled tools even when extension tools are visible", () => {
  const route = resolvePostgresProvider({
    harness: "copilot-cli",
    availableTools: [...extensionTools, ...bundledTools],
  });

  assert.equal(route.available, true);
  assert.equal(route.provider, "bundled-postgres-mcp");
  assert.equal(
    route.selectedTools.modify,
    "postgres-mcp-postgres_mcp_modify",
  );
});

test("Copilot CLI does not fall back to extension tools", () => {
  const route = resolvePostgresProvider({
    harness: "copilot-cli",
    availableTools: extensionTools,
  });

  assert.equal(route.available, false);
  assert.match(route.error, /requires the bundled postgres-mcp/);
  assert.match(route.error, /do not fall back to VS Code extension tools/);
});

test("provider validation rejects mixed-provider and db-context operations", () => {
  const route = resolvePostgresProvider({
    harness: "vscode-copilot",
    availableTools: [...extensionTools, ...bundledTools],
  });
  const errors = validatePostgresProviderUse(route, [
    "PostgreSQL MCP-pgsql_query",
    "postgres-mcp-postgres_mcp_modify",
    "PostgreSQL MCP-pgsql_db_context",
  ]);

  assert.equal(errors.length, 2);
  assert.match(errors[0], /does not belong to selected provider/);
  assert.match(errors[1], /prohibited PostgreSQL context operation/);
});

test("wrong-provider broad results require exact-name discovery, not an unavailable verdict", () => {
  const plan = planPostgresToolDiscovery({
    harness: "vscode-copilot",
    searches: [{
      query: "PostgreSQL MCP VS Code extension pgsql list connection profiles connect list databases server capabilities query modify disconnect",
      status: "completed",
      returnedTools: bundledTools.slice(0, 5),
    }],
  });

  assert.equal(plan.status, "search-required");
  assert.equal(plan.route.available, false);
  assert.equal(plan.nextQueries.length, 1);
  assert.equal(plan.nextQueries[0], [
    "pgsql_list_connection_profiles",
    "pgsql_connect",
    "pgsql_list_databases",
    "pgsql_get_server_capabilities",
    "pgsql_query",
    "pgsql_modify",
    "pgsql_disconnect",
  ].join(" "));
  assert.deepEqual(plan.rejectedTools, bundledTools.slice(0, 5));
});

test("bounded five-result searches require individual missing-operation probes in either harness", () => {
  for (const [harness, tools] of [
    ["vscode-copilot", extensionTools],
    ["copilot-cli", bundledTools],
  ]) {
    const initial = planPostgresToolDiscovery({ harness });
    const searches = [{
      query: initial.nextQueries[0],
      status: "completed",
      returnedTools: tools.slice(0, 5),
    }];
    const partial = planPostgresToolDiscovery({ harness, searches });
    assert.equal(partial.status, "search-required");
    assert.deepEqual(partial.nextQueries, [
      `${partial.route.toolFamily}_modify`,
      `${partial.route.toolFamily}_disconnect`,
    ]);

    for (const query of partial.nextQueries) {
      searches.push({
        query,
        status: "completed",
        returnedTools: [tools.find((tool) => tool.endsWith(query))],
      });
    }
    const complete = planPostgresToolDiscovery({ harness, searches });
    assert.equal(complete.status, "available");
    assert.equal(complete.route.available, true);
    assert.deepEqual(complete.nextQueries, []);
  }
});

test("exact-name recovery rejects wrong-family results and caches previously resolved tools", () => {
  const harness = "vscode-copilot";
  const initial = planPostgresToolDiscovery({ harness });
  const searches = [{
    query: initial.nextQueries[0],
    status: "completed",
    returnedTools: [...bundledTools.slice(0, 3), ...extensionTools.slice(0, 3)],
  }];
  const partial = planPostgresToolDiscovery({ harness, searches });
  for (const query of partial.nextQueries) {
    searches.push({
      query,
      status: "completed",
      returnedTools: [extensionTools.find((tool) => tool.endsWith(query)), extensionTools[0]],
    });
  }
  const recovered = planPostgresToolDiscovery({ harness, searches });
  assert.equal(recovered.status, "available");
  assert.equal(Object.keys(recovered.route.selectedTools).length, 7);
  assert.deepEqual(recovered.rejectedTools, bundledTools.slice(0, 3));
  assert.deepEqual(validatePostgresProviderUse(recovered.route, [
    recovered.route.selectedTools.listConnectionProfiles,
    recovered.route.selectedTools.connect,
    recovered.route.selectedTools.getServerCapabilities,
    recovered.route.selectedTools.query,
  ]), []);
});

test("a missing provider is confirmed only after individual exact-name probes", () => {
  const harness = "vscode-copilot";
  const initial = planPostgresToolDiscovery({ harness });
  const searches = [{
    query: initial.nextQueries[0],
    status: "completed",
    returnedTools: bundledTools,
  }];
  const pending = planPostgresToolDiscovery({ harness, searches });
  assert.equal(pending.status, "search-required");
  assert.equal(pending.nextQueries.length, 7);
  for (const query of pending.nextQueries) {
    searches.push({ query, status: "completed", returnedTools: [] });
  }
  const final = planPostgresToolDiscovery({ harness, searches });
  assert.equal(final.status, "unavailable");
  assert.equal(final.route.fallbackAttempted, false);
});

test("failed exact-name searches are reported as lookup failures, not provider absence", () => {
  const harness = "vscode-copilot";
  const initialTools = extensionTools.filter((tool) => !tool.endsWith("pgsql_query"));
  const plan = planPostgresToolDiscovery({
    harness,
    initialTools,
    searches: [{
      query: "pgsql_query",
      status: "failed",
      returnedTools: [],
      error: "Tool lookup failed.",
    }],
  });
  assert.equal(plan.status, "search-failed");
  assert.deepEqual(plan.nextQueries, []);
});

test("repeated results are deduplicated but genuinely ambiguous tools require an exact probe", () => {
  const duplicate = resolvePostgresProvider({
    harness: "vscode-copilot",
    availableTools: [...extensionTools, ...extensionTools],
  });
  assert.equal(duplicate.available, true);

  const initialTools = [
    ...extensionTools,
    { name: "mcp__PostgreSQL_MCP__pgsql_query", serverId: "PostgreSQL MCP" },
  ];
  const first = planPostgresToolDiscovery({ harness: "vscode-copilot", initialTools });
  const searches = [{
    query: first.nextQueries[0],
    status: "completed",
    returnedTools: [],
  }];
  const pending = planPostgresToolDiscovery({ harness: "vscode-copilot", initialTools, searches });
  assert.deepEqual(pending.nextQueries, ["pgsql_query"]);
  searches.push({ query: "pgsql_query", status: "completed", returnedTools: [] });
  assert.equal(
    planPostgresToolDiscovery({ harness: "vscode-copilot", initialTools, searches }).status,
    "ambiguous",
  );
});

test("explicit wrong-server metadata and misleading namespace prefixes are rejected", () => {
  for (const wrongTool of [
    { name: "pgsql_query", serverId: "postgres-mcp" },
    "foreign-PostgreSQL MCP-pgsql_query",
    "PostgreSQL MCP-notpgsql_query",
  ]) {
    const route = resolvePostgresProvider({
      harness: "vscode-copilot",
      availableTools: [
        ...extensionTools.filter((tool) => !tool.endsWith("pgsql_query")),
        wrongTool,
      ],
    });
    assert.equal(route.available, false);
    assert.ok(route.missingOperations.includes("pgsql_query"));
  }
});

test("namespace-qualified extension tools retain their actual identifiers", () => {
  const tools = extensionTools.slice(0, 7).map(
    (tool) => `mcp__PostgreSQL_MCP__${tool.slice("PostgreSQL MCP-".length)}`,
  );
  const plan = planPostgresToolDiscovery({ harness: "vscode-copilot", initialTools: tools });
  assert.equal(plan.status, "available");
  assert.equal(plan.route.selectedTools.query, "mcp__PostgreSQL_MCP__pgsql_query");
});

test("discovery rejects mixed-provider queries, malformed evidence, and unbounded retries", () => {
  const harness = "vscode-copilot";
  for (const search of [
    { query: "postgres_mcp_query", status: "completed", returnedTools: [] },
    { query: "pgsql_db_context", status: "completed", returnedTools: [] },
    { query: "pgsql_query", returnedTools: [] },
    { query: "pgsql_query", status: "failed", returnedTools: [] },
    { query: "pgsql_query", status: "completed", returnedTools: [null] },
  ]) {
    assert.throws(() => planPostgresToolDiscovery({ harness, searches: [search] }), TypeError);
  }
  assert.throws(() => planPostgresToolDiscovery({
    harness,
    searches: Array.from({ length: 9 }, () => ({
      query: "pgsql_query",
      status: "completed",
      returnedTools: [],
    })),
  }), /one exact-name batch plus one probe per operation/);
});

test("provider validation also prohibits schema visualization", () => {
  const route = resolvePostgresProvider({ harness: "vscode-copilot", availableTools: extensionTools });
  assert.match(
    validatePostgresProviderUse(route, ["PostgreSQL MCP-pgsql_visualize_schema"])[0],
    /prohibited PostgreSQL schema visualization/,
  );
});

test("installed and local-development MCP manifests remain synchronized", async () => {
  const installed = JSON.parse(
    await readFile(new URL("../mcp.json", import.meta.url), "utf8"),
  );
  const local = JSON.parse(
    await readFile(new URL("../.mcp.json", import.meta.url), "utf8"),
  );
  const installedServer =
    installed.mcpServers["postgres-mcp"];
  const localServer = local.mcpServers["postgres-mcp"];

  assert.ok(installedServer);
  assert.ok(localServer);
  assert.equal(localServer.type, installedServer.type);
  assert.equal(localServer.command, installedServer.command);
  assert.deepEqual(localServer.args, installedServer.args);
  assert.deepEqual(localServer.env, installedServer.env);
  assert.equal(localServer.timeout, 90000);
  assert.deepEqual(Object.keys(installed.mcpServers), ["postgres-mcp"]);
  assert.deepEqual(Object.keys(local.mcpServers), ["postgres-mcp"]);
});
