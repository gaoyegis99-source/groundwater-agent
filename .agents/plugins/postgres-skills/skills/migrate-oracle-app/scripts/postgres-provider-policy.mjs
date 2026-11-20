const PROVIDERS = Object.freeze({
  "vscode-copilot": Object.freeze({
    id: "vscode-postgresql-extension",
    serverId: "PostgreSQL MCP",
    toolFamily: "pgsql",
    operations: Object.freeze({
      listConnectionProfiles: "pgsql_list_connection_profiles",
      connect: "pgsql_connect",
      listDatabases: "pgsql_list_databases",
      getServerCapabilities: "pgsql_get_server_capabilities",
      query: "pgsql_query",
      modify: "pgsql_modify",
      disconnect: "pgsql_disconnect",
    }),
  }),
  "copilot-cli": Object.freeze({
    id: "bundled-postgres-mcp",
    serverId: "postgres-mcp",
    toolFamily: "postgres_mcp",
    operations: Object.freeze({
      listConnectionProfiles: "postgres_mcp_list_connection_profiles",
      connect: "postgres_mcp_connect",
      listDatabases: "postgres_mcp_list_databases",
      getServerCapabilities: "postgres_mcp_get_server_capabilities",
      query: "postgres_mcp_query",
      modify: "postgres_mcp_modify",
      disconnect: "postgres_mcp_disconnect",
    }),
  }),
});

export const SUPPORTED_POSTGRES_HARNESSES = Object.freeze(
  Object.keys(PROVIDERS),
);

export function resolvePostgresProvider({
  harness,
  availableTools = [],
}) {
  const provider = providerForHarness(harness);
  const tools = availableTools.map(normalizeTool);
  const selectedTools = {};
  const missingOperations = [];
  const ambiguousOperations = [];

  for (const [logicalOperation, operationName] of Object.entries(
    provider.operations,
  )) {
    const candidates = [...new Map(
      tools
        .filter((tool) => matchesOperation(tool, provider, operationName))
        .map((tool) => [tool.name, tool]),
    ).values()];
    if (candidates.length === 0) {
      missingOperations.push(operationName);
      continue;
    }
    if (candidates.length > 1) {
      ambiguousOperations.push({
        operationName,
        candidates: candidates.map((candidate) => candidate.name),
      });
      continue;
    }
    selectedTools[logicalOperation] = candidates[0].name;
  }

  const available =
    missingOperations.length === 0 && ambiguousOperations.length === 0;
  return {
    harness,
    provider: provider.id,
    serverId: provider.serverId,
    toolFamily: provider.toolFamily,
    available,
    fallbackAllowed: false,
    fallbackAttempted: false,
    selectedTools,
    missingOperations,
    ambiguousOperations,
    prohibitedOperations: [
      "postgres_mcp_db_context",
      "pgsql_db_context",
      "pgsql_visualize_schema",
    ],
    error: available
      ? null
      : providerUnavailableMessage(
          harness,
          missingOperations,
          ambiguousOperations,
        ),
  };
}

export function planPostgresToolDiscovery({
  harness,
  initialTools = [],
  searches = [],
}) {
  const provider = providerForHarness(harness);
  const operations = Object.values(provider.operations);
  if (!Array.isArray(initialTools) || !Array.isArray(searches)) {
    throw new TypeError("PostgreSQL discovery initialTools and searches must be arrays");
  }
  const tools = [...initialTools];
  const individualProbes = new Map();
  let exactSearchCount = 0;

  for (const search of searches) {
    if (
      typeof search?.query !== "string" ||
      !search.query.trim() ||
      !["completed", "failed"].includes(search.status) ||
      !Array.isArray(search.returnedTools)
    ) {
      throw new TypeError("PostgreSQL discovery searches require query, status, and returnedTools");
    }
    const queriedOperations = [...new Set(
      search.query.match(/\b(?:pgsql|postgres_mcp)_[a-z0-9_]+\b/g) ?? [],
    )];
    if (queriedOperations.some((operation) => !operations.includes(operation))) {
      throw new TypeError("PostgreSQL discovery must search only exact mapped functions of the selected provider");
    }
    if (queriedOperations.length > 0) exactSearchCount += 1;
    if (queriedOperations.length === 1) {
      individualProbes.set(queriedOperations[0], search.status);
    }
    if (search.status === "failed") {
      if (
        typeof search.error !== "string" ||
        !search.error.trim() ||
        search.returnedTools.length !== 0
      ) {
        throw new TypeError("Failed PostgreSQL discovery requires an explicit error and no returned tools");
      }
    } else {
      tools.push(...search.returnedTools);
    }
  }
  if (exactSearchCount > operations.length + 1) {
    throw new RangeError("PostgreSQL discovery exceeds one exact-name batch plus one probe per operation");
  }

  const route = resolvePostgresProvider({ harness, availableTools: tools });
  const unresolved = operations.filter(
    (operation) =>
      route.missingOperations.includes(operation) ||
      route.ambiguousOperations.some((entry) => entry.operationName === operation),
  );
  const pending = unresolved.filter((operation) => !individualProbes.has(operation));
  let status;
  let nextQueries = [];
  if (route.available) {
    status = "available";
  } else if (exactSearchCount === 0) {
    status = "search-required";
    nextQueries = [operations.join(" ")];
  } else if (pending.length > 0) {
    status = "search-required";
    nextQueries = pending;
  } else if (unresolved.some((operation) => individualProbes.get(operation) === "failed")) {
    status = "search-failed";
  } else if (route.ambiguousOperations.length > 0) {
    status = "ambiguous";
  } else {
    status = "unavailable";
  }

  return {
    status,
    nextQueries,
    route,
    rejectedTools: [...new Set(
      tools
        .map(normalizeTool)
        .filter((tool) => !operations.some((operation) => matchesOperation(tool, provider, operation)))
        .map((tool) => tool.name),
    )],
  };
}

export function validatePostgresProviderUse(route, invokedTools = []) {
  const errors = [];
  if (!route?.available) {
    errors.push(route?.error ?? "PostgreSQL provider is unavailable");
    return errors;
  }

  const selected = new Set(Object.values(route.selectedTools));
  for (const invoked of invokedTools.map(normalizeTool)) {
    if (invoked.name.endsWith("_db_context")) {
      errors.push(`prohibited PostgreSQL context operation: ${invoked.name}`);
      continue;
    }
    if (invoked.name.endsWith("pgsql_visualize_schema")) {
      errors.push(`prohibited PostgreSQL schema visualization operation: ${invoked.name}`);
      continue;
    }
    if (!selected.has(invoked.name)) {
      errors.push(
        `PostgreSQL operation does not belong to selected provider ${route.provider}: ${invoked.name}`,
      );
    }
  }
  return errors;
}

function normalizeTool(tool) {
  if (typeof tool === "string") {
    if (!tool.trim()) throw new TypeError("PostgreSQL tool names must be nonempty strings");
    return {
      name: tool,
      serverId: null,
    };
  }
  if (
    !tool ||
    typeof tool.name !== "string" ||
    !tool.name.trim() ||
    (tool.serverId != null &&
      (typeof tool.serverId !== "string" || !tool.serverId.trim()))
  ) {
    throw new TypeError("PostgreSQL tools require a nonempty name and valid serverId when supplied");
  }
  return {
    name: tool.name,
    serverId:
      typeof tool?.serverId === "string" ? tool.serverId : null,
  };
}

function providerForHarness(harness) {
  if (!Object.hasOwn(PROVIDERS, harness)) {
    throw new Error(
      `Unsupported PostgreSQL harness: ${harness}; expected ${SUPPORTED_POSTGRES_HARNESSES.join(" or ")}`,
    );
  }
  return PROVIDERS[harness];
}

function matchesOperation(tool, provider, operationName) {
  if (tool.name === operationName) {
    return tool.serverId === null || belongsToProvider(tool, provider);
  }
  return (
    tool.name.endsWith(operationName) &&
    /[-_.:/]$/.test(tool.name.slice(0, -operationName.length)) &&
    belongsToProvider(tool, provider)
  );
}

function belongsToProvider(tool, provider) {
  if (tool.serverId) {
    return tool.serverId.toLowerCase() === provider.serverId.toLowerCase();
  }
  const prefix = tool.name
    .slice(0, tool.name.lastIndexOf(`${provider.toolFamily}_`))
    .replace(/^mcp(?:__|[_\-.])/i, "");
  const canonical = (value) => value.toLowerCase().replace(/[^a-z0-9]/g, "");
  return canonical(prefix) === canonical(provider.serverId);
}

function providerUnavailableMessage(
  harness,
  missingOperations,
  ambiguousOperations,
) {
  const details = [];
  if (missingOperations.length > 0) {
    details.push(`missing ${missingOperations.join(", ")}`);
  }
  if (ambiguousOperations.length > 0) {
    details.push(
      `ambiguous ${ambiguousOperations
        .map((entry) => entry.operationName)
        .join(", ")}`,
    );
  }
  if (harness === "vscode-copilot") {
    return `VS Code Copilot requires the Microsoft PostgreSQL extension MCP tools; ${details.join("; ")}. Complete exact-name discovery before declaring the provider unavailable. Verify the extension and its tools are enabled for this session and do not fall back to the bundled NPM server.`;
  }
  return `Copilot CLI requires the bundled postgres-mcp server; ${details.join("; ")}. Complete exact-name discovery before declaring the provider unavailable. Verify Node.js 22+ with npx and the bundled MCP startup; do not fall back to VS Code extension tools.`;
}
