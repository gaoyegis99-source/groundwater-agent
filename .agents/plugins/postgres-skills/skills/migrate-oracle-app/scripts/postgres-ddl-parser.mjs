import { integrationError, sha256 } from "./migration-common.mjs";

const TYPE_STARTS = new Set([
  "bigint", "bigserial", "bit", "boolean", "bool", "box", "bytea", "char", "character",
  "cidr", "circle", "date", "decimal", "double", "float", "float4", "float8", "inet",
  "int", "int2", "int4", "int8", "integer", "interval", "json", "jsonb", "line",
  "lseg", "macaddr", "money", "numeric", "path", "point", "polygon", "real", "record",
  "serial", "smallint", "smallserial", "text", "time", "timestamp", "timestamptz",
  "timetz", "tsquery", "tsvector", "uuid", "varchar", "void", "xml",
]);
const COLUMN_CLAUSES = new Set([
  "collate", "compression", "constraint", "default", "generated", "not", "null",
  "primary", "references", "unique", "check", "storage",
]);
const CONSTRAINT_STARTS = new Set(["constraint", "primary", "foreign", "unique", "check", "exclude"]);
const TOKEN_LIMIT = 500_000;

export function tokenizeSql(text, { budget, maxTokens = TOKEN_LIMIT } = {}) {
  const tokens = [];
  let index = 0;
  let line = 1;
  const append = (kind, value, start, startLine) => {
    if (tokens.length >= maxTokens) throw integrationError("SQL token budget exhausted.");
    tokens.push({ kind, value, start, end: index, line: startLine });
  };
  const consume = (end) => {
    for (; index < end; index += 1) {
      if (index % 4096 === 0) budget?.check();
      if (text[index] === "\n") line += 1;
    }
  };
  while (index < text.length) {
    if (index % 4096 === 0) budget?.check();
    const start = index;
    const startLine = line;
    const character = text[index];
    if (/\s/u.test(character)) {
      consume(index + 1);
      continue;
    }
    if (text.startsWith("--", index)) {
      const end = text.indexOf("\n", index + 2);
      consume(end < 0 ? text.length : end);
      continue;
    }
    if (text.startsWith("/*", index)) {
      let depth = 1;
      index += 2;
      while (index < text.length && depth) {
        if (index % 4096 === 0) budget?.check();
        if (text.startsWith("/*", index)) { depth += 1; index += 2; }
        else if (text.startsWith("*/", index)) { depth -= 1; index += 2; }
        else consume(index + 1);
      }
      if (depth) throw integrationError("Unterminated SQL block comment.");
      continue;
    }
    if (character === "\\") {
      const end = text.indexOf("\n", index);
      const command = /^\\([A-Za-z!]+)/.exec(text.slice(index, index + 40))?.[1] ?? "unknown";
      consume(end < 0 ? text.length : end);
      append("meta", command.toLowerCase(), start, startLine);
      continue;
    }
    if (character === '"' || character === "'") {
      const quote = character;
      const escaped = quote === "'" && start > 0 && /[eE]/.test(text[start - 1]) &&
        (start === 1 || !/[\p{L}\p{N}_$]/u.test(text[start - 2]));
      let value = "";
      let closed = false;
      index += 1;
      while (index < text.length) {
        if (index % 4096 === 0) budget?.check();
        if (text[index] === quote) {
          if (text[index + 1] === quote) { value += quote; index += 2; continue; }
          index += 1;
          closed = true;
          break;
        }
        if (escaped && text[index] === "\\") {
          value += text.slice(index, index + 2);
          consume(Math.min(index + 2, text.length));
        } else {
          value += text[index];
          consume(index + 1);
        }
      }
      if (!closed) throw integrationError("Unterminated SQL quoted value.");
      append(quote === '"' ? "identifier" : "string", value, start, startLine);
      continue;
    }
    if (character === "$") {
      const delimiter = /^\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$/.exec(text.slice(index, index + 256))?.[0];
      if (delimiter) {
        const end = text.indexOf(delimiter, index + delimiter.length);
        if (end < 0) throw integrationError("Unterminated SQL dollar-quoted body.");
        consume(end + delimiter.length);
        append("body", "", start, startLine);
        continue;
      }
    }
    if (/[\p{L}_]/u.test(character)) {
      index += 1;
      while (index < text.length && /[\p{L}\p{N}_$]/u.test(text[index])) {
        if (index % 4096 === 0) budget?.check();
        index += 1;
      }
      append("word", text.slice(start, index).toLowerCase(), start, startLine);
      continue;
    }
    if (/[0-9]/.test(character)) {
      index += 1;
      while (index < text.length && /[0-9.]/.test(text[index])) index += 1;
      append("number", text.slice(start, index), start, startLine);
      continue;
    }
    index += 1;
    append("symbol", character, start, startLine);
  }
  budget?.check();
  return tokens;
}

export function readSqlName(tokens, start = 0) {
  const parts = [];
  let index = start;
  while (tokens[index] && ["word", "identifier"].includes(tokens[index].kind)) {
    if (!tokens[index].value) throw integrationError("A SQL identifier cannot be empty.");
    parts.push(tokens[index].value);
    index += 1;
    if (!symbol(tokens[index], ".")) break;
    index += 1;
  }
  if (!parts.length || symbol(tokens[index - 1], ".")) return null;
  return { parts, next: index };
}

function keyword(token, word) {
  return token?.kind === "word" && token.value === word;
}

function symbol(token, value) {
  return token?.kind === "symbol" && token.value === value;
}

function findWord(tokens, word, start = 0) {
  return tokens.findIndex((token, index) => index >= start && keyword(token, word));
}

function splitGroups(tokens) {
  const groups = [];
  let current = [];
  let depth = 0;
  for (const token of tokens) {
    if (token.kind === "symbol" && ["(", "["].includes(token.value)) depth += 1;
    if (token.kind === "symbol" && [")", "]"].includes(token.value)) depth -= 1;
    if (symbol(token, ",") && depth === 0) { groups.push(current); current = []; }
    else current.push(token);
  }
  if (current.length) groups.push(current);
  return groups;
}

function parentheses(tokens, start) {
  if (!symbol(tokens[start], "(")) return null;
  let depth = 1;
  for (let index = start + 1; index < tokens.length; index += 1) {
    if (symbol(tokens[index], "(")) depth += 1;
    if (symbol(tokens[index], ")")) depth -= 1;
    if (!depth) return { tokens: tokens.slice(start + 1, index), next: index + 1 };
  }
  throw integrationError("Unbalanced SQL declaration parentheses.");
}

function typeText(tokens) {
  if (tokens.some((token) => ["string", "body", "meta"].includes(token.kind))) {
    throw integrationError("A SQL type declaration contains an unsupported literal.");
  }
  let text = "";
  let previous;
  for (const token of tokens) {
    const compact = token.kind === "symbol" && [".", "(", ")", "[", "]", ","].includes(token.value);
    const afterCompact = previous?.kind === "symbol" && [".", "(", "[", ","].includes(previous.value);
    if (text && !compact && !afterCompact) text += " ";
    text += token.kind === "identifier" ? `"${token.value.replaceAll('"', '""')}"` : token.value;
    previous = token;
  }
  return text;
}

export function normalizePgType(type) {
  const tokens = tokenizeSql(type);
  const normalized = [];
  let array = "";
  for (let index = 0; index < tokens.length; index += 1) {
    if (symbol(tokens[index], "(")) {
      const group = parentheses(tokens, index);
      if (group.tokens.every((token) => token.kind === "number" || symbol(token, ","))) {
        index = group.next - 1;
        continue;
      }
    }
    if (symbol(tokens[index], "[")) {
      const end = tokens.findIndex((token, position) => position > index && symbol(token, "]"));
      if (end < 0) throw integrationError("Unbalanced SQL array type.");
      array = "[]";
      index = end;
      continue;
    }
    normalized.push(tokens[index]);
  }
  if (keyword(normalized[0], "pg_catalog") && symbol(normalized[1], ".")) normalized.splice(0, 2);
  const base = typeText(normalized);
  const aliases = {
    int: "integer", int4: "integer", int2: "smallint", int8: "bigint",
    float4: "real", float8: "double precision", bool: "boolean",
    varchar: "character varying", char: "character",
    timestamptz: "timestamp with time zone", timetz: "time with time zone",
    "timestamp without time zone": "timestamp", "time without time zone": "time",
  };
  return `${aliases[base] ?? base}${array}`;
}

function objectName(name, schema) {
  if (name.parts.length > 2) throw integrationError("A DDL object name has too many qualifiers.");
  return {
    schema: name.parts.length === 2 ? name.parts[0] : schema,
    name: name.parts.at(-1),
  };
}

function constraint(tokens) {
  let index = 0;
  let name = null;
  if (keyword(tokens[index], "constraint")) {
    name = tokens[index + 1]?.value;
    index += 2;
  }
  const kind = tokens[index]?.value;
  if (!["primary", "foreign", "unique", "check", "exclude"].includes(kind)) return null;
  const open = tokens.findIndex((token, position) => position > index && symbol(token, "("));
  const group = open < 0 ? null : parentheses(tokens, open);
  const columns = ["check", "exclude"].includes(kind) ? [] :
    splitGroups(group?.tokens ?? []).map((entry) => entry[0]?.value).filter(Boolean);
  const reference = findWord(tokens, "references");
  const target = reference < 0 ? null : readSqlName(tokens, reference + 1);
  return { name, kind: kind === "primary" || kind === "foreign" ? `${kind}-key` : kind,
    columns, references: target?.parts ?? null };
}

function columnsAndConstraints(tokens) {
  const columns = [];
  const constraints = [];
  const limitations = [];
  for (const group of splitGroups(tokens)) {
    if (!group.length) continue;
    if (group[0].kind === "word" && CONSTRAINT_STARTS.has(group[0].value)) {
      const entry = constraint(group);
      if (entry) constraints.push(entry);
      continue;
    }
    if (keyword(group[0], "like")) {
      limitations.push("Table LIKE inheritance is not expanded.");
      continue;
    }
    const name = readSqlName(group);
    if (!name || name.parts.length !== 1) throw integrationError("Unsupported SQL column declaration.");
    let depth = 0;
    let end = name.next;
    for (; end < group.length; end += 1) {
      if (symbol(group[end], "(")) depth += 1;
      if (symbol(group[end], ")")) depth -= 1;
      if (depth === 0 && group[end].kind === "word" && COLUMN_CLAUSES.has(group[end].value)) break;
    }
    const type = typeText(group.slice(name.next, end));
    if (!type) throw integrationError("A SQL column declaration has no type.");
    if (columns.some((column) => column.name === name.parts[0])) {
      throw integrationError("The DDL snapshot contains a duplicate column declaration.");
    }
    const not = findWord(group, "not", end);
    const primary = findWord(group, "primary", end) >= 0;
    const generated = findWord(group, "generated", end);
    const identity = generated >= 0 &&
      ((keyword(group[generated + 1], "always") && keyword(group[generated + 2], "as") &&
        keyword(group[generated + 3], "identity")) ||
       (keyword(group[generated + 1], "by") && keyword(group[generated + 2], "default") &&
        keyword(group[generated + 3], "as") && keyword(group[generated + 4], "identity")));
    columns.push({
      name: name.parts[0], type,
      nullable: !(not >= 0 && keyword(group[not + 1], "null")) && !primary && !identity,
      hasDefault: findWord(group, "default", end) >= 0 || findWord(group, "generated", end) >= 0,
    });
    if (primary) constraints.push({ name: null, kind: "primary-key", columns: name.parts, references: null });
    if (findWord(group, "unique", end) >= 0) {
      constraints.push({ name: null, kind: "unique", columns: name.parts, references: null });
    }
    const reference = findWord(group, "references", end);
    if (reference >= 0) {
      constraints.push({ name: null, kind: "foreign-key", columns: name.parts,
        references: readSqlName(group, reference + 1)?.parts ?? null });
    }
  }
  for (const key of constraints.filter((entry) => entry.kind === "primary-key")) {
    for (const column of columns) if (key.columns.includes(column.name)) column.nullable = false;
  }
  return { columns, constraints, limitations };
}

function routineArguments(tokens) {
  return splitGroups(tokens).map((original) => {
    let group = original;
    const defaultIndex = group.findIndex((token) => keyword(token, "default") || symbol(token, "="));
    if (defaultIndex >= 0) group = group.slice(0, defaultIndex);
    let mode = "in";
    const modeIndex = group.findIndex((token) => token.kind === "word" &&
      ["in", "out", "inout", "variadic"].includes(token.value));
    if (modeIndex >= 0) {
      mode = group[modeIndex].value;
      group = group.filter((_, index) => index !== modeIndex);
    }
    let name = null;
    if (group.length > 1 && ["word", "identifier"].includes(group[1].kind) &&
      !(group[0].kind === "word" && TYPE_STARTS.has(group[0].value))) {
      name = group[0].value;
      group = group.slice(1);
    }
    const type = typeText(group);
    if (!type) throw integrationError("A routine argument has no supported type.");
    return { name, mode, type, identityType: normalizePgType(type), hasDefault: defaultIndex >= 0 };
  });
}

function parseCreate(tokens, defaultSchema) {
  let index = 1;
  if (keyword(tokens[index], "or") && keyword(tokens[index + 1], "replace")) index += 2;
  while (tokens[index]?.kind === "word" &&
    ["temporary", "temp", "unlogged", "unique", "recursive"].includes(tokens[index].value)) index += 1;
  let kind = tokens[index++]?.value;
  if (kind === "materialized" && keyword(tokens[index], "view")) { kind = "materialized-view"; index += 1; }
  if (kind === "foreign" && keyword(tokens[index], "table")) { kind = "table"; index += 1; }
  if (!["table", "view", "materialized-view", "sequence", "function", "procedure",
    "type", "domain", "extension", "schema", "index", "trigger"].includes(kind)) {
    return { limitation: "An unsupported CREATE declaration was not indexed." };
  }
  if (kind === "index" && keyword(tokens[index], "concurrently")) index += 1;
  if (keyword(tokens[index], "if") && keyword(tokens[index + 1], "not") &&
    keyword(tokens[index + 2], "exists")) index += 3;
  const parsed = readSqlName(tokens, index);
  if (!parsed) throw integrationError("A supported CREATE declaration has no object name.");
  const object = { kind, ...objectName(parsed, defaultSchema), limitations: [] };
  index = parsed.next;
  if (["schema", "extension"].includes(kind)) object.schema = null;
  if (["function", "procedure"].includes(kind)) {
    const argumentsGroup = parentheses(tokens, index);
    if (!argumentsGroup) throw integrationError("A routine declaration has no argument list.");
    object.arguments = routineArguments(argumentsGroup.tokens);
    object.identityArguments = object.arguments.filter((argument) => argument.mode !== "out")
      .map((argument) => argument.identityType);
    const returns = findWord(tokens, "returns", argumentsGroup.next);
    if (returns >= 0) {
      const stop = tokens.findIndex((token, position) => position > returns &&
        token.kind === "word" && ["as", "language", "immutable", "stable", "volatile",
          "security", "cost", "rows", "set", "begin"].includes(token.value));
      object.returns = typeText(tokens.slice(returns + 1, stop < 0 ? tokens.length : stop));
    } else object.returns = null;
    const as = findWord(tokens, "as", argumentsGroup.next);
    object.bodyPresent = as >= 0 && (["body", "string"].includes(tokens[as + 1]?.kind) ||
      (keyword(tokens[as + 1], "e") && tokens[as + 2]?.kind === "string"));
  } else if (kind === "table") {
    const group = parentheses(tokens, index);
    if (group) Object.assign(object, columnsAndConstraints(group.tokens));
    else {
      object.columns = [];
      object.constraints = [];
      object.limitations.push("Table AS/PARTITION/inherited columns are not expanded.");
    }
    if (findWord(tokens, "inherits", index) >= 0) object.limitations.push("Inherited columns are not expanded.");
  } else if (["view", "materialized-view"].includes(kind)) {
    object.limitations.push("View output types and query dependencies require review of the matched DDL.");
  } else if (kind === "type") {
    const as = findWord(tokens, "as", index);
    if (as >= 0 && symbol(tokens[as + 1], "(")) {
      Object.assign(object, columnsAndConstraints(parentheses(tokens, as + 1).tokens));
    }
  } else if (kind === "domain") {
    const as = keyword(tokens[index], "as") ? index + 1 : index;
    const end = tokens.findIndex((token, position) => position > as &&
      token.kind === "word" && COLUMN_CLAUSES.has(token.value));
    object.baseType = typeText(tokens.slice(as, end < 0 ? tokens.length : end));
  } else if (["index", "trigger"].includes(kind)) {
    const on = findWord(tokens, "on", index);
    const target = on < 0 ? null : readSqlName(tokens, on + (keyword(tokens[on + 1], "only") ? 2 : 1));
    if (!target) throw integrationError("An index or trigger has no supported target relation.");
    object.relation = objectName(target, defaultSchema);
    if (!object.schema) object.schema = object.relation.schema;
  }
  return { object };
}

function parseAlter(tokens, defaultSchema) {
  if (!keyword(tokens[1], "table")) {
    const owner = findWord(tokens, "owner", 2);
    if (owner >= 0 && keyword(tokens[owner + 1], "to")) return {};
    return { limitation: "A non-table ALTER declaration was not structurally interpreted." };
  }
  let index = 2;
  if (keyword(tokens[index], "if") && keyword(tokens[index + 1], "exists")) index += 2;
  if (keyword(tokens[index], "only")) index += 1;
  const name = readSqlName(tokens, index);
  if (!name) throw integrationError("ALTER TABLE has no supported relation name.");
  const changes = [];
  for (const group of splitGroups(tokens.slice(name.next))) {
    if (keyword(group[0], "owner")) continue;
    if (keyword(group[0], "add")) {
      const start = keyword(group[1], "column") ? 2 : 1;
      const entry = constraint(group.slice(start));
      if (entry) changes.push({ constraint: entry });
      else if (!keyword(group[start], "if")) {
        const parsed = columnsAndConstraints(group.slice(start));
        changes.push({ column: parsed.columns[0] });
      } else changes.push({ limitation: "Conditional ALTER TABLE ADD was not resolved." });
    } else changes.push({ limitation: "A table alteration requires manual contract review." });
  }
  return { alteration: { ...objectName(name, defaultSchema), changes } };
}

export function postgresObjectKey(object) {
  return JSON.stringify([object.kind, object.schema, object.name,
    object.kind === "trigger" ? object.relation : object.identityArguments ?? null]);
}

export function parsePostgresDdl(text, { budget, maxTokens } = {}) {
  const tokens = tokenizeSql(text, { budget, maxTokens });
  const objects = [];
  const alterations = [];
  const limitations = [];
  let statement = [];
  let defaultSchema = null;
  const consume = () => {
    if (!statement.length) return;
    budget?.check();
    const first = statement[0];
    if (keyword(first, "copy") || keyword(first, "insert") ||
      keyword(first, "update") || keyword(first, "delete")) {
      throw integrationError("Use schema-only PostgreSQL DDL exports; data statements are not accepted.");
    }
    if (keyword(first, "set") && keyword(statement[1], "search_path")) {
      const schemas = statement.slice(3).filter((token) => ["word", "identifier"].includes(token.kind));
      defaultSchema = schemas[0]?.value ?? null;
      if (["pg_catalog", "$user"].includes(schemas[0]?.value)) defaultSchema = null;
    } else if (keyword(first, "select") && statement.some((token) => keyword(token, "set_config"))) {
      defaultSchema = null;
    } else if (keyword(first, "create") || keyword(first, "alter")) {
      const result = keyword(first, "create") ? parseCreate(statement, defaultSchema) :
        parseAlter(statement, defaultSchema);
      if (result.object) {
        result.object.sourceLine = first.line;
        const declaration = text.slice(first.start, statement.at(-1).end);
        result.object.sourceEndLine = first.line + (declaration.match(/\n/g)?.length ?? 0);
        result.object.statementSha256 = sha256(declaration);
        objects.push(result.object);
      }
      if (result.alteration) alterations.push(result.alteration);
      if (result.limitation) limitations.push({ line: first.line, reason: result.limitation });
    } else if (!["set", "select", "comment", "grant", "revoke", "reset"].includes(first.value)) {
      limitations.push({ line: first.line, reason: "A statement was not structurally interpreted." });
    }
    statement = [];
  };
  for (const token of tokens) {
    if (token.kind === "meta") {
      consume();
      if (!["restrict", "unrestrict"].includes(token.value)) {
        throw integrationError("SQL client commands are not accepted in DDL snapshots.");
      }
    } else if (token.kind === "symbol" && token.value === ";") consume();
    else statement.push(token);
  }
  consume();
  return { objects, alterations, limitations };
}
