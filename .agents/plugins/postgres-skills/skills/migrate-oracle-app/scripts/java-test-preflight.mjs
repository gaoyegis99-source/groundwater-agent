import { constants } from "node:fs";
import {
  lstat,
  mkdir,
  open,
  realpath,
  rm,
  writeFile,
} from "node:fs/promises";
import { createHash, randomUUID } from "node:crypto";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const MAX_XML_BYTES = 8 * 1024 * 1024;
const MAX_INPUT_BYTES = 128 * 1024 * 1024;
const MAX_SOURCE_BYTES = 4 * 1024 * 1024;
const MAX_TOTAL_SOURCE_BYTES = 64 * 1024 * 1024;
const MAX_FILES = 10_000;
const MAX_XML_NODES = 200_000;
const MAX_DEPTH = 128;
const FEATURE_RELEASES = Object.freeze({
  textBlocks: 15,
  localVariableInference: 10,
  records: 16,
  sealedTypes: 17,
  switchExpressions: 14,
});
const COMPILER_FIELDS = [
  "release",
  "testRelease",
  "source",
  "testSource",
  "target",
  "testTarget",
];

function fail(message, code = "JAVA_TEST_PREFLIGHT_INVALID") {
  const error = new Error(message);
  error.code = code;
  return error;
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function isDigest(value) {
  return typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
}

function requireString(value, label, maximum = 16_384) {
  if (
    typeof value !== "string" ||
    !value.trim() ||
    value.length > maximum ||
    /[\u0000-\u001f\u007f]/u.test(value)
  ) {
    throw fail(`Invalid ${label}.`);
  }
  return value;
}

function releaseNumber(value, label) {
  const text = typeof value === "string" ? value.trim() : String(value);
  const legacy = /^1\.([1-8])$/.exec(text);
  if (legacy) return Number(legacy[1]);
  if (!/^[1-9]\d{0,2}$/.test(text)) {
    throw fail(`Invalid or unresolved Java release in ${label}.`, "JAVA_RELEASE_UNRESOLVED");
  }
  return Number(text);
}

function requireMajor(value, label) {
  if (!Number.isInteger(value) || value < 1 || value > 999) {
    throw fail(`Invalid ${label}; an integer Java major version is required.`);
  }
  return value;
}

function featuresFor(release) {
  return Object.fromEntries(
    Object.entries(FEATURE_RELEASES).map(([feature, minimum]) => [
      feature,
      release >= minimum,
    ]),
  );
}

function pathKey(value) {
  return process.platform === "win32" ? value.toLowerCase() : value;
}

function within(root, candidate) {
  const relative = path.relative(root, candidate);
  return (
    relative === "" ||
    (!path.isAbsolute(relative) &&
      relative !== ".." &&
      !relative.startsWith(`..${path.sep}`))
  );
}

function relativePath(root, candidate) {
  return path.relative(root, candidate).split(path.sep).join("\\") || ".";
}

function resolveInside(root, value, label) {
  requireString(value, label);
  if (path.win32.isAbsolute(value) && !path.isAbsolute(value)) {
    throw fail(`${label} is outside the explicit root.`, "JAVA_PATH_ESCAPE");
  }
  const candidate = path.resolve(root, value.replace(/[\\/]/g, path.sep));
  if (!within(root, candidate)) {
    throw fail(`${label} is outside the explicit root.`, "JAVA_PATH_ESCAPE");
  }
  return candidate;
}

async function inspectPath(absolute, { allowMissing = false, create = false } = {}) {
  const base = path.parse(absolute).root;
  const parts = absolute.slice(base.length).split(path.sep).filter(Boolean);
  if (parts.length > MAX_DEPTH) throw fail("Filesystem path exceeds the depth limit.");
  let current = base;
  for (let index = 0; index <= parts.length; index += 1) {
    if (index) current = path.join(current, parts[index - 1]);
    let metadata;
    try {
      metadata = await lstat(current);
    } catch (error) {
      if (error.code !== "ENOENT") throw fail("Cannot inspect a preflight filesystem path.");
      if (create) {
        await mkdir(current, { mode: 0o700 });
        metadata = await lstat(current);
      } else if (allowMissing) {
        return null;
      } else {
        throw fail("Required preflight filesystem path does not exist.");
      }
    }
    if (metadata.isSymbolicLink()) {
      throw fail("Symbolic links and junctions are not allowed in preflight paths.", "JAVA_PATH_SYMLINK");
    }
    if (index < parts.length && !metadata.isDirectory()) {
      throw fail("A preflight path ancestor is not a directory.");
    }
    if (index === parts.length) return metadata;
  }
}

async function canonicalRoot(value) {
  requireString(value, "root");
  const root = path.resolve(value);
  const metadata = await inspectPath(root);
  if (!metadata.isDirectory()) throw fail("The explicit root must be a regular directory.");
  const canonical = await realpath(root);
  if (pathKey(canonical) !== pathKey(root)) {
    throw fail("The explicit root must not traverse a symbolic link.", "JAVA_PATH_SYMLINK");
  }
  return canonical;
}

async function readBounded(absolute, maximum) {
  const metadata = await inspectPath(absolute);
  if (!metadata.isFile() || metadata.size > maximum) {
    throw fail("Preflight input is not a regular file or exceeds its size limit.");
  }
  let handle;
  try {
    handle = await open(absolute, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
    const before = await handle.stat();
    if (
      !before.isFile() ||
      before.size > maximum ||
      (before.dev !== 0 && metadata.dev !== 0 && before.dev !== metadata.dev) ||
      before.ino !== metadata.ino
    ) {
      throw fail("Preflight input changed while it was being opened.");
    }
    const bytes = await handle.readFile();
    const after = await handle.stat();
    if (
      bytes.length > maximum ||
      before.size !== after.size ||
      before.mtimeMs !== after.mtimeMs ||
      before.ctimeMs !== after.ctimeMs
    ) {
      throw fail("Preflight input changed while it was being read.");
    }
    await inspectPath(absolute);
    return bytes;
  } catch (error) {
    if (error.code?.startsWith("JAVA_")) throw error;
    throw fail("Cannot read a regular preflight input file.");
  } finally {
    await handle?.close();
  }
}

function decodeUtf8(bytes, label) {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw fail(`Invalid UTF-8 in ${label}.`);
  }
}

function location(source, offset) {
  let line = 1;
  let column = 1;
  for (let index = 0; index < offset; index += 1) {
    if (source[index] === "\r") {
      if (source[index + 1] === "\n" && index + 1 < offset) index += 1;
      line += 1;
      column = 1;
    } else if (source[index] === "\n") {
      line += 1;
      column = 1;
    } else {
      column += 1;
    }
  }
  return { line, column };
}

export function parseXmlDocument(source, budget = { nodes: MAX_XML_NODES, namespaceEntries: 1_000_000 }) {
  if (typeof source !== "string" || Buffer.byteLength(source) > MAX_XML_BYTES) {
    throw fail("Effective POM XML must be bounded UTF-8 text.");
  }
  let index = source.charCodeAt(0) === 0xfeff ? 1 : 0;
  let nodes = 0;
  let root;
  const stack = [];
  const xmlError = (reason, offset = index) => {
    const { line, column } = location(source, offset);
    throw fail(
      `Malformed effective POM XML at ${line}:${column}: ${reason}.`,
      "JAVA_POM_XML_INVALID",
    );
  };
  if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f\ud800-\udfff\ufffe\uffff]/u.test(source)) {
    xmlError("invalid XML character");
  }
  const namePattern = /[\p{L}_][\p{L}\p{N}_.-]*(?::[\p{L}_][\p{L}\p{N}_.-]*)?/uy;
  const readName = () => {
    namePattern.lastIndex = index;
    const match = namePattern.exec(source);
    if (!match) xmlError("invalid element or attribute name");
    index += match[0].length;
    return match[0];
  };
  const space = () => {
    while (/[ \t\r\n]/.test(source[index] ?? "") && index < source.length) index += 1;
  };
  const entities = (value, offset) => {
    if (value.includes("<")) xmlError("unescaped markup in a value", offset);
    return value.replace(/&([^;]*);|&/g, (whole, entity) => {
      const predefined = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };
      if (Object.hasOwn(predefined, entity)) return predefined[entity];
      let codePoint;
      if (/^#x[0-9a-f]+$/i.test(entity ?? "")) {
        codePoint = Number.parseInt(entity.slice(2), 16);
      } else if (/^#\d+$/.test(entity ?? "")) {
        codePoint = Number(entity.slice(1));
      }
      if (
        !Number.isInteger(codePoint) ||
        !(
          codePoint === 9 ||
          codePoint === 10 ||
          codePoint === 13 ||
          (codePoint >= 0x20 && codePoint <= 0xd7ff) ||
          (codePoint >= 0xe000 && codePoint <= 0xfffd) ||
          (codePoint >= 0x10000 && codePoint <= 0x10ffff)
        )
      ) {
        xmlError("unsupported or invalid entity reference", offset);
      }
      return String.fromCodePoint(codePoint);
    });
  };
  while (index < source.length) {
    if (source.startsWith("<!--", index)) {
      const end = source.indexOf("-->", index + 4);
      if (end < 0 || source.slice(index + 4, end).includes("--")) xmlError("invalid comment");
      index = end + 3;
      continue;
    }
    if (source.startsWith("<![CDATA[", index)) {
      if (!stack.length) xmlError("CDATA outside an element");
      const end = source.indexOf("]]>", index + 9);
      if (end < 0) xmlError("unterminated CDATA");
      stack.at(-1).text += source.slice(index + 9, end);
      index = end + 3;
      continue;
    }
    if (source.startsWith("<?", index)) {
      const end = source.indexOf("?>", index + 2);
      if (end < 0) xmlError("unterminated processing instruction");
      const instruction = source.slice(index + 2, end);
      if (!/^[\p{L}_][\p{L}\p{N}_.:-]*(?:\s|$)/u.test(instruction)) {
        xmlError("invalid processing instruction");
      }
      if (/^xml(?:\s|$)/i.test(instruction)) {
        if (root || stack.length || source.slice(0, index).replace(/^\ufeff/, "").trim()) {
          xmlError("misplaced XML declaration");
        }
        if (!/^xml\s+version\s*=\s*(['"])1\.[01]\1(?:\s+encoding\s*=\s*(['"])UTF-8\2)?(?:\s+standalone\s*=\s*(['"])(?:yes|no)\3)?\s*$/i.test(instruction)) {
          xmlError("unsupported XML declaration");
        }
      }
      index = end + 2;
      continue;
    }
    if (source.startsWith("<!", index)) {
      xmlError("DOCTYPE and entity declarations are prohibited");
    }
    if (source[index] !== "<") {
      const end = source.indexOf("<", index);
      const stop = end < 0 ? source.length : end;
      const text = source.slice(index, stop);
      if (text.includes("]]>")) xmlError("CDATA terminator outside CDATA");
      const decoded = entities(text, index);
      if (!stack.length && decoded.trim()) xmlError("text outside the root element");
      if (stack.length) stack.at(-1).text += decoded;
      index = stop;
      continue;
    }
    const start = index;
    index += 1;
    if (source[index] === "/") {
      index += 1;
      const name = readName();
      space();
      if (source[index++] !== ">") xmlError("invalid closing tag", start);
      if (!stack.length || stack.at(-1).qualifiedName !== name) {
        xmlError("mismatched closing tag", start);
      }
      stack.pop();
      continue;
    }
    const qualifiedName = readName();
    const attributes = new Map();
    let selfClosing = false;
    while (index < source.length) {
      const beforeSpace = index;
      space();
      if (source[index] === ">") {
        index += 1;
        break;
      }
      if (source.startsWith("/>", index)) {
        index += 2;
        selfClosing = true;
        break;
      }
      if (index === beforeSpace) xmlError("missing whitespace before an attribute");
      const name = readName();
      if (attributes.has(name)) xmlError("duplicate attribute");
      if (attributes.size >= 256) xmlError("XML attribute budget exceeded");
      space();
      if (source[index++] !== "=") xmlError("missing attribute equals sign");
      space();
      const quote = source[index++];
      if (quote !== '"' && quote !== "'") xmlError("unquoted attribute value");
      const end = source.indexOf(quote, index);
      if (end < 0) xmlError("unterminated attribute value");
      attributes.set(name, entities(source.slice(index, end), index));
      index = end + 1;
    }
    if (source[index - 1] !== ">") xmlError("unterminated opening tag", start);
    const namespaces = new Map(stack.at(-1)?.namespaces ?? [["xml", "http://www.w3.org/XML/1998/namespace"]]);
    for (const [name, value] of attributes) {
      if (name === "xmlns") namespaces.set("", value);
      if (name.startsWith("xmlns:")) namespaces.set(name.slice(6), value);
    }
    budget.namespaceEntries -= namespaces.size;
    if (namespaces.size > 64 || budget.namespaceEntries < 0) xmlError("XML namespace budget exceeded", start);
    const checkPrefix = (name) => {
      if (name.includes(":") && !namespaces.get(name.split(":")[0])) {
        xmlError("undeclared namespace prefix", start);
      }
    };
    checkPrefix(qualifiedName);
    const expandedAttributes = new Set();
    for (const name of attributes.keys()) {
      if (name === "xmlns" || name.startsWith("xmlns:")) continue;
      checkPrefix(name);
      const parts = name.split(":");
      const expanded = `${parts.length === 2 ? namespaces.get(parts[0]) : ""}|${parts.at(-1)}`;
      if (expandedAttributes.has(expanded)) xmlError("duplicate namespaced attribute", start);
      expandedAttributes.add(expanded);
    }
    const node = {
      name: qualifiedName.split(":").at(-1),
      qualifiedName,
      text: "",
      children: [],
      attributes,
      namespaces,
    };
    nodes += 1;
    budget.nodes -= 1;
    if (nodes > MAX_XML_NODES || budget.nodes < 0 || stack.length >= MAX_DEPTH) xmlError("XML budget exceeded", start);
    if (stack.length) stack.at(-1).children.push(node);
    else if (root) xmlError("multiple root elements", start);
    else root = node;
    if (!selfClosing) stack.push(node);
  }
  if (!root || stack.length) xmlError("missing root or unclosed element");
  return root;
}

function children(node, name) {
  return node?.children.filter((child) => child.name === name) ?? [];
}

function single(node, name) {
  const matches = children(node, name);
  if (matches.length > 1) {
    throw fail(`Ambiguous effective POM: duplicate ${name} elements.`, "JAVA_RELEASE_AMBIGUOUS");
  }
  return matches[0];
}

function scalar(node, label) {
  if (!node) return undefined;
  if (node.children.length) throw fail(`Malformed scalar effective POM field: ${label}.`);
  return node.text.trim();
}

function propertyMap(project) {
  const result = new Map();
  for (const property of single(project, "properties")?.children ?? []) {
    if (result.has(property.name)) {
      throw fail("Ambiguous duplicate effective POM property.", "JAVA_RELEASE_AMBIGUOUS");
    }
    result.set(property.name, scalar(property, "property"));
  }
  return result;
}

function substitute(value, properties, label, trail = [], budget = { references: 4096, bytes: 1024 * 1024 }) {
  if (typeof value !== "string" || value.length > 65_536 || trail.length > 32) {
    throw fail(`Invalid or cyclic property substitution in ${label}.`, "JAVA_RELEASE_UNRESOLVED");
  }
  let replacements = 0;
  const resolved = value.replace(/\$\{([^{}]+)\}/g, (whole, key) => {
    replacements += 1;
    budget.references -= 1;
    if (replacements > 512 || budget.references < 0 || trail.includes(key) || !properties.has(key)) {
      throw fail(`Unresolved or cyclic effective POM property in ${label}.`, "JAVA_RELEASE_UNRESOLVED");
    }
    const replacement = substitute(properties.get(key), properties, label, [...trail, key], budget);
    budget.bytes -= replacement.length;
    if (budget.bytes < 0) throw fail(`Property substitution budget exceeded in ${label}.`, "JAVA_RELEASE_UNRESOLVED");
    return replacement;
  });
  if (resolved.includes("${") || resolved.length > 65_536) {
    throw fail(`Unresolved property substitution in ${label}.`, "JAVA_RELEASE_UNRESOLVED");
  }
  return resolved.trim();
}

function optionalSubstitute(value, properties) {
  if (value === undefined) return undefined;
  try {
    return substitute(value, properties, "project identity");
  } catch (error) {
    if (error.code === "JAVA_RELEASE_UNRESOLVED") return undefined;
    throw error;
  }
}

function identity(project, properties) {
  const parent = single(project, "parent");
  return Object.fromEntries(
    ["groupId", "artifactId", "version"].map((field) => [
      field,
      optionalSubstitute(
        scalar(single(project, field), field) ??
          (field !== "artifactId" ? scalar(single(parent, field), field) : undefined),
        properties,
      ),
    ]),
  );
}

function identityMatches(left, right) {
  if (!left.artifactId || !right.artifactId || left.artifactId !== right.artifactId) return false;
  return ["groupId", "version"].every(
    (field) => !left[field] || !right[field] || left[field] === right[field],
  );
}

function parseMavenArgs(args) {
  if (!Array.isArray(args) || args.length > 512) throw fail("Invalid mavenArgs array.");
  const properties = new Map();
  const files = [];
  let pom = "pom.xml";
  let explicitlyOffline = false;
  const switches = new Set([
    "-B", "--batch-mode", "-e", "--errors", "-X", "--debug", "-q", "--quiet",
    "-ntp", "--no-transfer-progress", "-o", "--offline", "-U", "--update-snapshots",
    "-nsu", "--no-snapshot-updates", "-N", "--non-recursive", "-am", "--also-make",
    "-amd", "--also-make-dependents", "-fae", "--fail-at-end", "-ff", "--fail-fast",
    "-fn", "--fail-never", "-C", "--strict-checksums", "-c", "--lax-checksums",
    "-V", "--show-version", "-itr", "--ignore-transitive-repositories",
  ]);
  const valued = new Map([
    ["-P", "profiles"], ["--activate-profiles", "profiles"],
    ["-f", "pom"], ["--file", "pom"],
    ["-s", "file"], ["--settings", "file"],
    ["-gs", "file"], ["--global-settings", "file"],
    ["-t", "file"], ["--toolchains", "file"],
    ["-gt", "file"], ["--global-toolchains", "file"],
    ["-pl", "projects"], ["--projects", "projects"],
    ["-rf", "resume"], ["--resume-from", "resume"],
    ["-T", "threads"], ["--threads", "threads"], ["--color", "color"],
    ["-D", "property"], ["--define", "property"],
  ]);
  for (let index = 0; index < args.length; index += 1) {
    const arg = requireString(args[index], "Maven argument");
    if (switches.has(arg)) {
      if (arg === "-o" || arg === "--offline") explicitlyOffline = true;
      continue;
    }
    let option = arg;
    let value;
    if (arg.startsWith("--") && arg.includes("=")) {
      [option, ...value] = arg.split("=");
      value = value.join("=");
    } else if (!valued.has(arg)) {
      option = [...valued.keys()].find(
        (key) => key.startsWith("-") && !key.startsWith("--") && arg.startsWith(key),
      );
      if (option) value = arg.slice(option.length).replace(/^=/, "");
    }
    if (!valued.has(option)) {
      throw fail("Maven arguments must contain options, not goals or lifecycle commands.");
    }
    value ??= args[++index];
    requireString(value, "Maven option value");
    const type = valued.get(option);
    if (type === "property") {
      const equals = value.indexOf("=");
      const name = equals < 0 ? value : value.slice(0, equals);
      if (!name || name === "output" || name === "forceStdout") {
        throw fail("Maven arguments must not override the private effective-POM output.");
      }
      properties.set(name, equals < 0 ? "true" : value.slice(equals + 1));
    } else if (type === "pom") pom = value;
    else if (type === "file") files.push(value);
  }
  return { properties, files, pom, explicitlyOffline };
}

async function collectLayout(root, options) {
  const inputs = new Map();
  const layout = new Map();
  const xmlBudget = { nodes: MAX_XML_NODES, namespaceEntries: 1_000_000 };
  let totalBytes = 0;
  const addInput = async (absolute, optional = false) => {
    const relative = relativePath(root, absolute);
    if (inputs.has(relative)) return;
    if (inputs.size >= MAX_FILES) throw fail("Preflight build input-file budget exceeded.");
    if (!(await inspectPath(absolute, { allowMissing: optional }))) {
      inputs.set(relative, { path: relative, sha256: null });
      return;
    }
    const bytes = await readBounded(absolute, MAX_XML_BYTES);
    totalBytes += bytes.length;
    if (totalBytes > MAX_INPUT_BYTES) throw fail("Preflight build inputs exceed the total size limit.");
    inputs.set(relative, { path: relative, sha256: sha256(bytes) });
    return bytes;
  };
  const addPom = async (absolute, optional = false) => {
    if (!within(root, absolute)) throw fail("Maven module is outside the explicit root.", "JAVA_PATH_ESCAPE");
    const relative = relativePath(root, absolute);
    if (layout.has(relative)) return;
    if (layout.size >= MAX_FILES) throw fail("Maven module budget exceeded.");
    const bytes = await addInput(absolute, optional);
    if (!bytes) return;
    const project = parseXmlDocument(decodeUtf8(bytes, "POM input"), xmlBudget);
    if (project.name !== "project") throw fail("A Maven input POM must have a project root.");
    const properties = new Map([...propertyMap(project), ...options.properties]);
    properties.set("basedir", path.dirname(absolute));
    properties.set("project.basedir", path.dirname(absolute));
    const entry = {
      absolute,
      directory: path.dirname(absolute),
      modulePath: relativePath(root, path.dirname(absolute)),
      identity: identity(project, properties),
    };
    layout.set(relative, entry);
    for (const module of children(single(project, "modules"), "module")) {
      const declared = optionalSubstitute(scalar(module, "module"), properties);
      if (declared) await addModule(entry.directory, declared, true);
    }
    const parent = single(project, "parent");
    if (parent) {
      const declared = scalar(single(parent, "relativePath"), "parent relativePath") ?? "../pom.xml";
      if (declared) {
        const parentPath = path.resolve(entry.directory, declared.replace(/[\\/]/g, path.sep));
        if (within(root, parentPath)) {
          const metadata = await inspectPath(parentPath, { allowMissing: true });
          await addPom(metadata?.isDirectory() ? path.join(parentPath, "pom.xml") : parentPath, true);
        }
      }
    }
  };
  const addModule = async (directory, declared, optional = false) => {
    const absolute = resolveInside(root, path.relative(root, path.resolve(directory, declared.replace(/[\\/]/g, path.sep))), "Maven module");
    const metadata = await inspectPath(absolute, { allowMissing: optional });
    if (!metadata) return;
    await addPom(metadata.isDirectory() ? path.join(absolute, "pom.xml") : absolute, optional);
  };
  let selected = resolveInside(root, options.pom, "Maven POM selection");
  const metadata = await inspectPath(selected);
  if (metadata.isDirectory()) selected = path.join(selected, "pom.xml");
  await addPom(selected);
  if (pathKey(selected) !== pathKey(path.join(root, "pom.xml"))) {
    await addPom(path.join(root, "pom.xml"), true);
  }
  for (const file of [
    ".mvn\\maven.config",
    ".mvn\\jvm.config",
    ".mvn\\wrapper\\maven-wrapper.properties",
    ...options.files,
  ]) {
    const absolute = path.resolve(root, file.replace(/[\\/]/g, path.sep));
    if (within(root, absolute)) await addInput(absolute, true);
  }
  return { inputs, layout, selected, addModule, addInput };
}

async function mapProjects(root, projects, state, userProperties) {
  const mapped = new Map();
  const pending = new Set(projects);
  while (pending.size) {
    let progress = false;
    for (const project of pending) {
      const properties = new Map([...propertyMap(project), ...userProperties]);
      const projectIdentity = identity(project, properties);
      let matches = [...state.layout.values()].filter((entry) =>
        identityMatches(entry.identity, projectIdentity),
      );
      if (projects.length === 1 && matches.length === 0) {
        const selected = state.layout.get(relativePath(root, state.selected));
        if (!projectIdentity.artifactId || !selected.identity.artifactId) matches = [selected];
      }
      if (matches.length > 1) {
        throw fail("Ambiguous Maven module identity in the effective POM.", "JAVA_RELEASE_AMBIGUOUS");
      }
      if (!matches.length) continue;
      const entry = matches[0];
      if ([...mapped.values()].some((other) => other.modulePath === entry.modulePath)) {
        throw fail("Duplicate effective POM module identity.", "JAVA_RELEASE_AMBIGUOUS");
      }
      mapped.set(project, entry);
      pending.delete(project);
      progress = true;
      properties.set("basedir", entry.directory);
      properties.set("project.basedir", entry.directory);
      for (const module of children(single(project, "modules"), "module")) {
        await state.addModule(entry.directory, substitute(scalar(module, "module"), properties, "module"));
      }
    }
    if (!progress) {
      throw fail("Unknown Maven module: effective POM projects cannot be mapped unambiguously to input POMs.", "JAVA_MODULE_UNKNOWN");
    }
  }
  return mapped;
}

function projectProperties(project, projects, mapped, userProperties, trail = []) {
  if (trail.includes(project)) throw fail("Cyclic effective POM parent inheritance.");
  const own = propertyMap(project);
  const parent = single(project, "parent");
  let inherited = new Map();
  if (parent) {
    const parentIdentity = identity(parent, new Map([...own, ...userProperties]));
    const matches = projects.filter(
      (candidate) =>
        candidate !== project &&
        identityMatches(identity(candidate, new Map([...propertyMap(candidate), ...userProperties])), parentIdentity),
    );
    if (matches.length > 1) throw fail("Ambiguous effective parent POM.", "JAVA_RELEASE_AMBIGUOUS");
    if (matches.length) {
      inherited = projectProperties(matches[0], projects, mapped, userProperties, [...trail, project]);
    }
  }
  const properties = new Map([...inherited, ...own]);
  const entry = mapped.get(project);
  properties.set("basedir", entry.directory);
  properties.set("project.basedir", entry.directory);
  properties.set("pom.basedir", entry.directory);
  for (const field of ["groupId", "artifactId", "version"]) {
    const value = scalar(single(project, field), field) ??
      (field !== "artifactId" ? scalar(single(parent, field), field) : undefined);
    if (value !== undefined) {
      properties.set(`project.${field}`, value);
      properties.set(`pom.${field}`, value);
    }
    const parentValue = scalar(single(parent, field), field);
    if (parentValue !== undefined) properties.set(`project.parent.${field}`, parentValue);
  }
  return new Map([...properties, ...userProperties]);
}

function compilerPlugin(plugins, properties) {
  const matches = children(plugins, "plugin").filter((plugin) => {
    const artifact = scalar(single(plugin, "artifactId"), "plugin artifactId");
    const group = scalar(single(plugin, "groupId"), "plugin groupId") ?? "org.apache.maven.plugins";
    return optionalSubstitute(artifact, properties) === "maven-compiler-plugin" &&
      optionalSubstitute(group, properties) === "org.apache.maven.plugins";
  });
  if (matches.length > 1) throw fail("Ambiguous duplicate compiler plugins.", "JAVA_RELEASE_AMBIGUOUS");
  return matches[0];
}

function mergeConfiguration(base, configuration, origin, properties) {
  const result = configuration?.attributes.get("combine.self") === "override"
    ? new Map()
    : new Map(base);
  for (const field of COMPILER_FIELDS) {
    const node = single(configuration, field);
    if (!node) continue;
    if (configuration.attributes.get("combine.children") === "append" && result.has(field)) {
      throw fail("Ambiguous appended compiler version configuration.", "JAVA_RELEASE_AMBIGUOUS");
    }
    result.set(field, { value: scalar(node, `compiler ${field}`), origin: `${origin}.${field}` });
  }
  for (const argument of [
    scalar(single(configuration, "compilerArgument"), "compilerArgument"),
    ...children(single(configuration, "compilerArgs"), "arg").map((node) => scalar(node, "compiler arg")),
  ]) {
    if (
      argument &&
      /(?:^|\s)(?:--release|--source|-source|--target|-target)(?:=|\s|$)/.test(
        substitute(argument, properties, "compiler arguments"),
      )
    ) {
      throw fail("Compiler argument version overrides are unsupported; use explicit compiler version fields.");
    }
  }
  if (
    single(configuration, "compilerArguments")?.children.some(
      (node) => ["release", "source", "target"].includes(node.name),
    )
  ) {
    throw fail("Compiler argument version overrides are unsupported; use explicit compiler version fields.");
  }
  return result;
}

function compilerConfiguration(project, properties) {
  const build = single(project, "build");
  const managed = compilerPlugin(single(single(build, "pluginManagement"), "plugins"), properties);
  const active = compilerPlugin(single(build, "plugins"), properties);
  let configuration = mergeConfiguration(new Map(), single(managed, "configuration"), "compiler-management", properties);
  configuration = mergeConfiguration(configuration, single(active, "configuration"), "compiler-configuration", properties);
  const executions = new Map();
  for (const plugin of [managed, active]) {
    const seen = new Set();
    for (const execution of children(single(plugin, "executions"), "execution")) {
      const id = substitute(scalar(single(execution, "id"), "execution id") ?? "default", properties, "compiler execution id");
      if (seen.has(id)) throw fail("Ambiguous duplicate compiler execution.", "JAVA_RELEASE_AMBIGUOUS");
      seen.add(id);
      const prior = executions.get(id);
      const executionConfiguration = single(execution, "configuration");
      executions.set(id, {
        id,
        phase: single(execution, "phase")
          ? substitute(scalar(single(execution, "phase"), "execution phase"), properties, "compiler execution phase")
          : prior?.phase,
        goals: single(execution, "goals")
          ? children(single(execution, "goals"), "goal").map((goal) => substitute(scalar(goal, "compiler goal"), properties, "compiler execution goal"))
          : prior?.goals ?? [],
        override: executionConfiguration?.attributes.get("combine.self") === "override" || prior?.override || false,
        configuration: mergeConfiguration(
          prior?.configuration ?? new Map(),
          executionConfiguration,
          `compiler-execution[${executions.size + 1}]`,
          properties,
        ),
      });
    }
  }
  return { configuration, executions: [...executions.values()] };
}

function resolveCompilerLevel(configuration, properties, test, label) {
  const parameter = (field) => {
    const configured = configuration.get(field);
    if (configured) {
      return {
        release: releaseNumber(substitute(configured.value, properties, label), label),
        origin: configured.origin,
      };
    }
    const key = `maven.compiler.${field}`;
    if (properties.has(key)) {
      return {
        release: releaseNumber(substitute(properties.get(key), properties, label), label),
        origin: `compiler-property.${field}`,
      };
    }
  };
  const release = (test ? parameter("testRelease") : undefined) ?? parameter("release");
  // javac's --release controls both language and bytecode; source/target do not override it.
  if (release) return { ...release, targetRelease: release.release };
  const source = (test ? parameter("testSource") : undefined) ?? parameter("source");
  const target = (test ? parameter("testTarget") : undefined) ?? parameter("target");
  if (!source) {
    throw fail(
      `Unresolved Java ${label}: an explicit release or source level is required; target alone does not establish a language level.`,
      "JAVA_RELEASE_UNRESOLVED",
    );
  }
  if (target && target.release < source.release) {
    throw fail(`Incompatible compiler source and target levels in ${label}.`, "JAVA_COMPILER_INCOMPATIBLE");
  }
  return { ...source, targetRelease: target?.release ?? null };
}

function resolveLane(compiler, properties, test, label) {
  const executions = compiler.executions.filter(
    (execution) =>
      execution.phase !== "none" &&
      execution.phase !== "disabled" &&
      (execution.goals.includes(test ? "testCompile" : "compile") ||
        execution.id === (test ? "default-testCompile" : "default-compile")),
  );
  const levels = (executions.length ? executions : [null]).map((execution) => {
    const configuration = execution?.override ? new Map() : new Map(compiler.configuration);
    for (const [field, value] of execution?.configuration ?? []) configuration.set(field, value);
    return resolveCompilerLevel(configuration, properties, test, label);
  });
  if (new Set(levels.map((level) => level.release)).size !== 1) {
    throw fail(`Ambiguous Java ${label}: compiler executions disagree.`, "JAVA_RELEASE_AMBIGUOUS");
  }
  return levels;
}

function runtimeVersion(output) {
  const versions = [];
  for (const line of output.split(/\r?\n/)) {
    const match = /^\s*(?:openjdk|java)\s+(?:version\s+)?["']?(\d+(?:\.\d+)*)(?:[-+_"'\s]|$)/i.exec(line);
    if (!match) continue;
    const parts = match[1].split(".");
    const major = Number(parts[0] === "1" ? parts[1] : parts[0]);
    versions.push(requireMajor(major, "runtime Java version"));
  }
  if (!versions.length || new Set(versions).size !== 1) {
    throw fail("Unresolved or ambiguous runtime Java version.", "JAVA_RELEASE_UNRESOLVED");
  }
  return versions[0];
}

function missingArtifactFailure(output) {
  if (/Compilation failure|Non-parseable POM|Malformed POM|test failures|failed to execute.*(?:compiler|surefire|failsafe)/i.test(output)) {
    return false;
  }
  return (
    /(?:PluginResolutionException|PluginNotFoundException|NoPluginFoundForPrefixException|DependencyResolutionException)/.test(output) ||
    /No plugin found for prefix|Could not find artifact|(?:Plugin|plugin)\s+[^\r\n]+(?:could not be resolved|not found)/.test(output) ||
    (
      /offline|not (?:been )?downloaded|not cached/i.test(output) &&
      /Could not resolve (?:dependencies|plugin|artifact)|Non-resolvable parent POM|Cannot access [^\r\n]+artifact/i.test(output)
    )
  );
}

function environmentDigest() {
  return sha256(JSON.stringify([
    process.env.JAVA_HOME ?? null,
    process.env.PATH ?? null,
    process.env.MAVEN_HOME ?? null,
    process.env.M2_HOME ?? null,
    process.env.MAVEN_OPTS ?? null,
    process.env.MAVEN_ARGS ?? null,
    process.env.JAVA_TOOL_OPTIONS ?? null,
    process.env.JDK_JAVA_OPTIONS ?? null,
  ]));
}

function digestEvidence(evidence) {
  return sha256(JSON.stringify({
    schemaVersion: evidence.schemaVersion,
    runtimeJavaMajor: evidence.runtimeJavaMajor,
    modules: evidence.modules,
    evidence: evidence.evidence,
  }));
}

export async function detectJavaTestRelease({
  root: rootInput,
  outputDirectory,
  mavenExecutable = "mvn",
  mavenArgs = [],
  javaExecutable = "java",
  effectivePomText,
  runtimeJavaMajor,
  commandRunner,
} = {}) {
  const root = await canonicalRoot(rootInput);
  requireString(mavenExecutable, "Maven executable");
  requireString(javaExecutable, "Java executable");
  const args = parseMavenArgs(mavenArgs);
  if (commandRunner !== undefined && typeof commandRunner !== "function") {
    throw fail("Invalid commandRunner; a function is required.");
  }
  if (effectivePomText !== undefined && typeof effectivePomText !== "string") {
    throw fail("Invalid effectivePomText; XML text is required.");
  }
  if (runtimeJavaMajor !== undefined) requireMajor(runtimeJavaMajor, "runtimeJavaMajor");
  const state = await collectLayout(root, args);
  const localExecutable = path.resolve(root, mavenExecutable);
  if (within(root, localExecutable)) {
    await state.addInput(localExecutable, true);
  }
  const run = async (command, commandArgs, phase) => {
    let runner = commandRunner;
    if (!runner) {
      try {
        ({ runProcessCommand: runner } = await import("./process-command.mjs"));
      } catch {
        throw fail("The process-command.mjs runner is required for live preflight commands.");
      }
    }
    let result;
    try {
      result = await runner(root, command, commandArgs, { env: process.env });
    } catch {
      throw fail(`Java test preflight ${phase} command could not execute.`, "JAVA_PREFLIGHT_COMMAND_FAILED");
    }
    const status = result?.status ?? result?.exitCode;
    if (!Number.isInteger(status) || result.error || result.signal) {
      throw fail(`Java test preflight ${phase} command did not complete.`, "JAVA_PREFLIGHT_COMMAND_FAILED");
    }
    const stdout = String(result.stdout ?? "");
    const stderr = String(result.stderr ?? "");
    if (Buffer.byteLength(stdout) + Buffer.byteLength(stderr) > 16 * 1024 * 1024) {
      throw fail("Preflight command output exceeds the diagnostic limit.");
    }
    return { status, output: `${stdout}\n${stderr}` };
  };
  const runtimeSource = runtimeJavaMajor === undefined ? "java-version" : "supplied";
  if (runtimeJavaMajor === undefined) {
    const result = await run(javaExecutable, ["-version"], "java-version");
    if (result.status !== 0) throw fail("Java version command failed.", "JAVA_PREFLIGHT_COMMAND_FAILED");
    runtimeJavaMajor = runtimeVersion(result.output);
  }
  let offline = false;
  let onlineRetry = false;
  const pomSource = effectivePomText === undefined ? "maven-help:effective-pom" : "supplied";
  if (effectivePomText === undefined) {
    requireString(outputDirectory, "private outputDirectory");
    const directory = path.resolve(root, outputDirectory);
    const metadata = await inspectPath(directory, { create: true });
    if (!metadata.isDirectory()) throw fail("Private outputDirectory must be a regular directory.");
    const output = path.join(directory, `java-test-effective-${randomUUID()}.xml`);
    await writeFile(output, "", { flag: "wx", mode: 0o600 });
    try {
      const invocation = [
        "-B",
        "-o",
        ...mavenArgs,
        "help:effective-pom",
        `-Doutput=${output}`,
      ];
      offline = true;
      let result = await run(mavenExecutable, invocation, "effective-pom");
      if (result.status !== 0 && !args.explicitlyOffline && missingArtifactFailure(result.output)) {
        onlineRetry = true;
        await inspectPath(output);
        await writeFile(output, "", { mode: 0o600 });
        result = await run(
          mavenExecutable,
          invocation.filter((argument, index) => index !== 1),
          "effective-pom",
        );
      }
      if (result.status !== 0) {
        throw fail("Maven effective-POM command failed; no authoritative releases were captured.", "JAVA_PREFLIGHT_COMMAND_FAILED");
      }
      effectivePomText = decodeUtf8(await readBounded(output, MAX_XML_BYTES), "effective POM");
    } finally {
      await rm(output, { force: true });
    }
  }
  const document = parseXmlDocument(effectivePomText);
  const projects = document.name === "project"
    ? [document]
    : document.name === "projects"
      ? children(document, "project")
      : [];
  if (
    !projects.length ||
    (document.name === "projects" && document.children.length !== projects.length)
  ) {
    throw fail("Effective POM must contain project or projects/project elements.");
  }
  const mapped = await mapProjects(root, projects, state, args.properties);
  const modules = [];
  const compilerEvidence = [];
  for (const project of projects) {
    const entry = mapped.get(project);
    const properties = projectProperties(project, projects, mapped, args.properties);
    const compiler = compilerConfiguration(project, properties);
    const packaging = scalar(single(project, "packaging"), "packaging");
    const hasLevel = compiler.configuration.size ||
      compiler.executions.some((execution) => execution.configuration.size) ||
      COMPILER_FIELDS.some((field) => properties.has(`maven.compiler.${field}`));
    if (packaging === "pom" && !hasLevel) continue;
    const main = resolveLane(compiler, properties, false, "main release");
    const tests = resolveLane(compiler, properties, true, "test release");
    for (const level of [...main, ...tests]) {
      if (runtimeJavaMajor < Math.max(level.release, level.targetRelease ?? 0)) {
        throw fail(
          `Runtime JDK ${runtimeJavaMajor} is older than the compiler level required by module ${entry.modulePath}.`,
          "JAVA_RUNTIME_INCOMPATIBLE",
        );
      }
    }
    modules.push({
      modulePath: entry.modulePath,
      mainRelease: main[0].release,
      testRelease: tests[0].release,
      features: featuresFor(tests[0].release),
    });
    compilerEvidence.push({ kind: "compiler", modulePath: entry.modulePath, main, test: tests });
  }
  if (!modules.length) throw fail("No authoritative Java compiler levels were found.", "JAVA_RELEASE_UNRESOLVED");
  const comparePaths = (left, right) => left < right ? -1 : left > right ? 1 : 0;
  modules.sort((left, right) => comparePaths(left.modulePath, right.modulePath));
  compilerEvidence.sort((left, right) => comparePaths(left.modulePath, right.modulePath));
  const files = [...state.inputs.values()].sort((left, right) => comparePaths(left.path, right.path));
  for (const file of files) {
    const absolute = resolveInside(root, file.path, "POM input");
    if (file.sha256 === null) {
      if (await inspectPath(absolute, { allowMissing: true })) {
        throw fail("Preflight build inputs changed during release detection.", "JAVA_EVIDENCE_STALE");
      }
      continue;
    }
    const bytes = await readBounded(absolute, MAX_XML_BYTES);
    if (sha256(bytes) !== file.sha256) {
      throw fail("Preflight build inputs changed during release detection.", "JAVA_EVIDENCE_STALE");
    }
  }
  const result = {
    schemaVersion: 1,
    status: "resolved",
    capturedAt: new Date().toISOString(),
    inputDigest: "",
    runtimeJavaMajor,
    modules,
    evidence: [
      { kind: "effective-pom", source: pomSource, sha256: sha256(effectivePomText), offline, onlineRetry },
      { kind: "runtime", source: runtimeSource, major: runtimeJavaMajor },
      {
        kind: "inputs",
        rootDigest: sha256(pathKey(root)),
        configurationDigest: sha256(JSON.stringify({ mavenExecutable, mavenArgs, javaExecutable })),
        environmentDigest: environmentDigest(),
        files,
      },
      ...compilerEvidence,
    ],
  };
  result.inputDigest = digestEvidence(result);
  return result;
}

function validateModule(module, full = false) {
  if (!module || typeof module !== "object" || Array.isArray(module)) {
    throw fail("Invalid module release evidence.", "JAVA_EVIDENCE_INVALID");
  }
  requireMajor(module.testRelease, "module testRelease");
  if (full || module.mainRelease !== undefined) requireMajor(module.mainRelease, "module mainRelease");
  if (full || module.features !== undefined) {
    const expected = featuresFor(module.testRelease);
    if (
      !module.features ||
      Object.keys(module.features).length !== Object.keys(expected).length ||
      Object.entries(expected).some(([key, value]) => module.features[key] !== value)
    ) {
      throw fail("Invalid module feature evidence.", "JAVA_EVIDENCE_INVALID");
    }
  }
}

function canonicalRelative(value) {
  if (value === ".") return true;
  return typeof value === "string" &&
    value.length <= 4096 &&
    !path.win32.isAbsolute(value) &&
    !path.isAbsolute(value) &&
    !value.includes("/") &&
    !/[\u0000-\u001f\u007f:]/u.test(value) &&
    value.split("\\").every((part) => part && part !== "." && part !== "..");
}

async function validateEvidence(root, evidence) {
  if (
    evidence?.schemaVersion !== 1 ||
    evidence.status !== "resolved" ||
    !isDigest(evidence.inputDigest) ||
    !Array.isArray(evidence.modules) ||
    !evidence.modules.length ||
    evidence.modules.length > MAX_FILES ||
    !Array.isArray(evidence.evidence) ||
    evidence.evidence.length !== evidence.modules.length + 3 ||
    typeof evidence.capturedAt !== "string" ||
    !Number.isFinite(Date.parse(evidence.capturedAt)) ||
    new Date(evidence.capturedAt).toISOString() !== evidence.capturedAt ||
    Date.parse(evidence.capturedAt) > Date.now() + 60_000
  ) {
    throw fail("Invalid Java release evidence.", "JAVA_EVIDENCE_INVALID");
  }
  requireMajor(evidence.runtimeJavaMajor, "evidence runtimeJavaMajor");
  const provenance = (kind) => {
    const entries = evidence.evidence.filter((entry) => entry?.kind === kind);
    if (entries.length !== 1) throw fail("Invalid Java release provenance.", "JAVA_EVIDENCE_INVALID");
    return entries[0];
  };
  const pom = provenance("effective-pom");
  const runtime = provenance("runtime");
  const inputs = provenance("inputs");
  if (
    !isDigest(pom.sha256) ||
    !["supplied", "maven-help:effective-pom"].includes(pom.source) ||
    typeof pom.offline !== "boolean" ||
    typeof pom.onlineRetry !== "boolean" ||
    !["supplied", "java-version"].includes(runtime.source) ||
    runtime.major !== evidence.runtimeJavaMajor ||
    !isDigest(inputs.rootDigest) ||
    !isDigest(inputs.configurationDigest) ||
    !isDigest(inputs.environmentDigest) ||
    !Array.isArray(inputs.files) ||
    !inputs.files.length ||
    inputs.files.length > MAX_FILES
  ) {
    throw fail("Invalid Java release provenance.", "JAVA_EVIDENCE_INVALID");
  }
  const modulePaths = new Set();
  for (const module of evidence.modules) {
    validateModule(module, true);
    if (!canonicalRelative(module.modulePath) || modulePaths.has(pathKey(module.modulePath))) {
      throw fail("Invalid or duplicate Java module paths.", "JAVA_EVIDENCE_INVALID");
    }
    modulePaths.add(pathKey(module.modulePath));
    const compiler = evidence.evidence.filter(
      (entry) => entry?.kind === "compiler" && entry.modulePath === module.modulePath,
    );
    if (compiler.length !== 1) throw fail("Invalid compiler provenance.", "JAVA_EVIDENCE_INVALID");
    for (const [lane, level] of [["main", module.mainRelease], ["test", module.testRelease]]) {
      if (
        !Array.isArray(compiler[0][lane]) ||
        !compiler[0][lane].length ||
        compiler[0][lane].some(
          (entry) =>
            entry.release !== level ||
            typeof entry.origin !== "string" ||
            !/^(?:compiler-(?:management|configuration)\.(?:release|testRelease|source|testSource|target|testTarget)|compiler-execution\[\d+\]\.(?:release|testRelease|source|testSource|target|testTarget)|compiler-property\.(?:release|testRelease|source|testSource|target|testTarget))$/.test(entry.origin) ||
            (entry.targetRelease !== null &&
              (!Number.isInteger(entry.targetRelease) || entry.targetRelease < level)) ||
            evidence.runtimeJavaMajor < Math.max(level, entry.targetRelease ?? 0),
        )
      ) {
        throw fail("Invalid or runtime-incompatible compiler provenance.", "JAVA_EVIDENCE_INVALID");
      }
    }
    try {
      const metadata = await inspectPath(resolveInside(root, module.modulePath, "module"));
      if (!metadata.isDirectory()) throw fail("Stale Java module evidence.", "JAVA_EVIDENCE_STALE");
    } catch (error) {
      if (error.code === "JAVA_PATH_SYMLINK" || error.code === "JAVA_PATH_ESCAPE") throw error;
      throw fail("Stale Java module evidence: a module directory is unavailable.", "JAVA_EVIDENCE_STALE");
    }
  }
  if (digestEvidence(evidence) !== evidence.inputDigest) {
    throw fail("Invalid Java release evidence digest.", "JAVA_EVIDENCE_INVALID");
  }
  if (
    inputs.rootDigest !== sha256(pathKey(root)) ||
    inputs.environmentDigest !== environmentDigest()
  ) {
    throw fail("Stale Java release evidence: root or Java/Maven environment changed.", "JAVA_EVIDENCE_STALE");
  }
  const inputPaths = new Set();
  let totalBytes = 0;
  for (const file of inputs.files) {
    if (!canonicalRelative(file?.path) || file.path === "." || (file.sha256 !== null && !isDigest(file.sha256)) || inputPaths.has(pathKey(file.path))) {
      throw fail("Invalid preflight input-file evidence.", "JAVA_EVIDENCE_INVALID");
    }
    inputPaths.add(pathKey(file.path));
    const absolute = resolveInside(root, file.path, "build input");
    if (file.sha256 === null) {
      if (await inspectPath(absolute, { allowMissing: true })) {
        throw fail("Stale Java release evidence: a previously absent build input appeared.", "JAVA_EVIDENCE_STALE");
      }
      continue;
    }
    let bytes;
    try {
      bytes = await readBounded(absolute, MAX_XML_BYTES);
    } catch (error) {
      if (error.code === "JAVA_PATH_SYMLINK" || error.code === "JAVA_PATH_ESCAPE") throw error;
      throw fail("Stale Java release evidence: a build input is unavailable.", "JAVA_EVIDENCE_STALE");
    }
    totalBytes += bytes.length;
    if (totalBytes > MAX_INPUT_BYTES) throw fail("Preflight build inputs exceed the total size limit.");
    if (sha256(bytes) !== file.sha256) {
      throw fail("Stale Java release evidence: a build input changed.", "JAVA_EVIDENCE_STALE");
    }
  }
}

function translatedJava(source, fileLabel) {
  const chars = [];
  const offsets = [];
  let backslashes = 0;
  let escapedPreviously = false;
  for (let index = 0; index < source.length; index += 1) {
    const start = index;
    let char = source[index];
    let escaped = false;
    if (char === "\\" && (escapedPreviously || backslashes % 2 === 0) && source[index + 1] === "u") {
      let hex = index + 1;
      while (source[hex] === "u") hex += 1;
      if (!/^[0-9a-fA-F]{4}$/.test(source.slice(hex, hex + 4))) {
        const { line, column } = location(source, start);
        throw fail(`${fileLabel}:${line}:${column}: malformed Java Unicode escape.`, "JAVA_SOURCE_LEXICAL_INVALID");
      }
      char = String.fromCharCode(Number.parseInt(source.slice(hex, hex + 4), 16));
      index = hex + 3;
      escaped = true;
    }
    chars.push(char);
    offsets.push(start);
    backslashes = char === "\\" ? backslashes + 1 : 0;
    escapedPreviously = escaped;
  }
  return { text: chars.join(""), offsets };
}

function javaTokens(source, fileLabel) {
  const { text, offsets } = translatedJava(source, fileLabel);
  const tokens = [];
  const lexicalError = (index, reason) => {
    const { line, column } = location(source, offsets[index] ?? source.length);
    throw fail(`${fileLabel}:${line}:${column}: ${reason}.`, "JAVA_SOURCE_LEXICAL_INVALID");
  };
  let index = 0;
  const identifierStart = /[\p{L}\p{Nl}_$]/u;
  const identifierPart = /[\p{L}\p{Nl}\p{N}\p{Mn}\p{Mc}\p{Pc}_$]/u;
  while (index < text.length) {
    const char = text[index];
    if (/\s/u.test(char)) {
      index += 1;
      continue;
    }
    if (text.startsWith("//", index)) {
      index += 2;
      while (index < text.length && text[index] !== "\n" && text[index] !== "\r") index += 1;
      continue;
    }
    if (text.startsWith("/*", index)) {
      const end = text.indexOf("*/", index + 2);
      if (end < 0) lexicalError(index, "unterminated Java block comment");
      index = end + 2;
      continue;
    }
    const start = index;
    if (text.startsWith('"""', index)) {
      index += 3;
      let closed = false;
      while (index < text.length) {
        if (text[index] === "\\") {
          index += 2;
        } else if (text.startsWith('"""', index)) {
          index += 3;
          closed = true;
          break;
        } else index += 1;
      }
      if (!closed) lexicalError(start, "unterminated Java text block");
      tokens.push({ value: "<text-block>", kind: "textBlock", offset: offsets[start] });
    } else if (char === '"' || char === "'") {
      index += 1;
      let closed = false;
      while (index < text.length) {
        if (text[index] === "\\") index += 2;
        else if (text[index++] === char) {
          closed = true;
          break;
        }
      }
      if (!closed) lexicalError(start, "unterminated Java quoted literal");
      tokens.push({ value: "<literal>", kind: "literal", offset: offsets[start] });
    } else if (identifierStart.test(String.fromCodePoint(text.codePointAt(index)))) {
      index += String.fromCodePoint(text.codePointAt(index)).length;
      while (index < text.length) {
        const next = String.fromCodePoint(text.codePointAt(index));
        if (!identifierPart.test(next)) break;
        index += next.length;
      }
      tokens.push({ value: text.slice(start, index), kind: "identifier", offset: offsets[start] });
    } else {
      const pair = text.slice(index, index + 2);
      const value = ["->", "::", "==", "!=", "<=", ">=", "&&", "||", "+=", "-=", "*=", "/="].includes(pair)
        ? pair
        : char;
      index += value.length;
      tokens.push({ value, kind: "punctuation", offset: offsets[start] });
    }
    if (tokens.length > 500_000) lexicalError(start, "Java lexical token budget exceeded");
  }
  return tokens;
}

function tokenPairs(tokens) {
  const stack = [];
  const pairs = new Map();
  const closing = { ")": "(", "]": "[", "}": "{" };
  for (let index = 0; index < tokens.length; index += 1) {
    const value = tokens[index].value;
    if (["(", "[", "{"].includes(value)) {
      if (stack.length >= MAX_DEPTH) throw fail("Java lexical nesting budget exceeded.");
      stack.push(index);
    } else if (Object.hasOwn(closing, value)) {
      const start = stack.pop();
      if (start !== undefined && tokens[start].value === closing[value]) {
        pairs.set(start, index);
        pairs.set(index, start);
      }
    }
  }
  return pairs;
}

function syntaxFeatures(tokens) {
  const found = [];
  const pairs = tokenPairs(tokens);
  const knownTypes = new Set();
  for (let index = 0; index < tokens.length - 1; index += 1) {
    if (["class", "interface", "enum"].includes(tokens[index].value)) {
      knownTypes.add(tokens[index + 1].value);
    }
    if (tokens[index].value === "import") {
      let end = index + 1;
      while (end < tokens.length && tokens[end].value !== ";") end += 1;
      if (end > index + 1) knownTypes.add(tokens[end - 1].value);
    }
  }
  const braces = [];
  const parentheses = [];
  let declarationStart = 0;
  const add = (feature, token, requiredRelease = FEATURE_RELEASES[feature]) =>
    found.push({ feature, offset: token.offset, requiredRelease });
  const recordParameters = (index) => {
    if (tokens[index + 1]?.kind !== "identifier") return undefined;
    let cursor = index + 2;
    if (tokens[cursor]?.value === "<") {
      let depth = 1;
      while (++cursor < tokens.length && depth > 0) {
        if (tokens[cursor].value === "<") depth += 1;
        if (tokens[cursor].value === ">") depth -= 1;
        if (["{", "}", ";", "="].includes(tokens[cursor].value)) return undefined;
      }
    }
    return tokens[cursor]?.value === "(" ? cursor : undefined;
  };
  const expressionBefore = new Set([
    "=", "+=", "-=", "*=", "/=", "return", "throw", "yield", "(", "[", ",",
    "?", "+", "-", "*", "/", "%", "!", "~", "&&", "||", "==", "!=", "<", ">",
    "<=", ">=", "->", "assert",
  ]);
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    const value = token.value;
    const next = tokens[index + 1];
    const previous = tokens[index - 1]?.value;
    if (value === "(") parentheses.push(index);
    else if (value === ")") parentheses.pop();
    if (token.kind === "textBlock") add("textBlocks", token);
    if (value === "{") {
      const prefix = tokens.slice(declarationStart, index);
      const type = prefix.some((entry, position) =>
        (
          ["class", "interface", "enum"].includes(entry.value) ||
          (entry.value === "record" && recordParameters(declarationStart + position) !== undefined)
        ) && prefix[position + 1]?.kind === "identifier",
      );
      const headerStart = previous === ")" ? pairs.get(index - 1) : undefined;
      const anonymous = headerStart !== undefined &&
        !["if", "for", "while", "switch", "catch", "synchronized", "try"].includes(tokens[headerStart - 1]?.value) &&
        prefix.some((entry) => entry.value === "new");
      braces.push(type || anonymous ? "type" : "block");
      declarationStart = index + 1;
    } else if (value === "}") {
      braces.pop();
      declarationStart = index + 1;
    } else if (value === ";") declarationStart = index + 1;
    if (
      value === "var" &&
      !knownTypes.has("var") &&
      previous !== "." &&
      next?.kind === "identifier" &&
      ["=", ":"].includes(tokens[index + 2]?.value) &&
      braces.at(-1) !== "type"
    ) {
      add("localVariableInference", token);
    }
    if (
      value === "var" &&
      !knownTypes.has("var") &&
      previous !== "." &&
      next?.kind === "identifier" &&
      parentheses.length
    ) {
      const parameterEnd = pairs.get(parentheses.at(-1));
      if (parameterEnd !== undefined && tokens[parameterEnd + 1]?.value === "->") {
        add("localVariableInference", token, 11);
      }
    }
    if (
      value === "record" &&
      !knownTypes.has("record") &&
      previous !== "." &&
      previous !== "new" &&
      next?.kind === "identifier"
    ) {
      const end = pairs.get(recordParameters(index));
      if (end !== undefined) {
        let body = end + 1;
        while (body < tokens.length && !["{", ";", "=", "}"].includes(tokens[body].value)) body += 1;
        if (tokens[body]?.value === "{") add("records", token);
      }
    }
    if (value === "sealed" && previous !== ".") {
      let end = index + 1;
      while (end < tokens.length && !["{", ";", "}", "=", "(", ")"].includes(tokens[end].value)) {
        if (tokens[end].value === "@") {
          end += 1;
          if (tokens[end]?.kind === "identifier") end += 1;
          while (tokens[end]?.value === "." && tokens[end + 1]?.kind === "identifier") end += 2;
          if (tokens[end]?.value === "(" && pairs.has(end)) end = pairs.get(end) + 1;
          continue;
        }
        if (
          ["class", "interface"].includes(tokens[end].value) &&
          tokens[end - 1]?.value !== "." &&
          tokens[end + 1]?.kind === "identifier"
        ) {
          add("sealedTypes", token);
          break;
        }
        if (!["public", "protected", "private", "abstract", "static", "final", "strictfp"].includes(tokens[end].value)) break;
        end += 1;
      }
    }
    if (value !== "switch" || next?.value !== "(") continue;
    const headerEnd = pairs.get(index + 1);
    const body = headerEnd === undefined ? undefined : headerEnd + 1;
    const bodyEnd = pairs.get(body);
    if (tokens[body]?.value !== "{" || bodyEnd === undefined) continue;
    const precedingHeader = previous === ")" ? pairs.get(index - 1) : undefined;
    let expression = expressionBefore.has(previous) ||
      (precedingHeader !== undefined &&
        !["if", "while", "for", "switch", "catch", "synchronized"].includes(tokens[precedingHeader - 1]?.value));
    if (previous === ":") {
      let cursor = index - 2;
      while (cursor >= 0 && ![";", "{", "}"].includes(tokens[cursor].value)) {
        if (tokens[cursor].value === "?") expression = true;
        cursor -= 1;
      }
    }
    let label = false;
    for (let cursor = body + 1; cursor < bodyEnd; cursor += 1) {
      const current = tokens[cursor].value;
      if (
        current === "yield" &&
        !knownTypes.has("yield") &&
        tokens[cursor - 1]?.value !== "." &&
        !["=", "+", "-", "++", "--", "(", ".", ";"].includes(tokens[cursor + 1]?.value) &&
        !(tokens[cursor + 1]?.kind === "identifier" && tokens[cursor + 2]?.value === "=")
      ) {
        expression = true;
      }
      if (["(", "[", "{"].includes(current) && pairs.has(cursor)) {
        cursor = pairs.get(cursor);
        continue;
      }
      if (current === "case" || current === "default") label = true;
      else if (current === ":" || current === ";") label = false;
      else if (current === "->" && label) expression = true;
    }
    if (expression) add("switchExpressions", token);
  }
  return found.sort((left, right) => left.offset - right.offset);
}

export function validateJavaTestSource(source, moduleEvidence, fileLabel = "draft.java") {
  if (typeof source !== "string" || Buffer.byteLength(source) > MAX_SOURCE_BYTES) {
    throw fail("Java test source must be bounded text.");
  }
  requireString(fileLabel, "Java source file label", 4096);
  validateModule(moduleEvidence);
  const tokens = javaTokens(source, fileLabel);
  for (const { feature, offset, requiredRelease } of syntaxFeatures(tokens)) {
    if (moduleEvidence.testRelease >= requiredRelease) continue;
    const { line, column } = location(source, offset);
    const error = fail(
      `${fileLabel}:${line}:${column}: ${feature} requires Java ${requiredRelease}; configured test release is ${moduleEvidence.testRelease}.`,
      "JAVA_TEST_SYNTAX_INCOMPATIBLE",
    );
    Object.assign(error, { fileLabel, line, column, feature, requiredRelease, testRelease: moduleEvidence.testRelease });
    throw error;
  }
  return { status: "passed", validation: "lexical", testRelease: moduleEvidence.testRelease };
}

export async function validateJavaTestFiles({ root: rootInput, files, evidence } = {}) {
  const root = await canonicalRoot(rootInput);
  if (!Array.isArray(files) || files.length > MAX_FILES) throw fail("Invalid Java test files array.");
  await validateEvidence(root, evidence);
  const modules = evidence.modules.map((module) => ({
    ...module,
    absolute: resolveInside(root, module.modulePath, "module"),
  })).sort((left, right) => right.absolute.length - left.absolute.length);
  const validated = [];
  const seen = new Set();
  let totalBytes = 0;
  for (const entry of files) {
    const draft = entry && typeof entry === "object" && !Array.isArray(entry);
    const absolute = resolveInside(root, draft ? entry.path : entry, "Java test file");
    if (path.extname(absolute).toLowerCase() !== ".java") throw fail("Only Java test source files may be validated.");
    const filePath = relativePath(root, absolute);
    if (seen.has(pathKey(filePath))) throw fail("Duplicate Java test file paths.");
    seen.add(pathKey(filePath));
    const metadata = await inspectPath(absolute, { allowMissing: draft });
    if (metadata && !metadata.isFile()) throw fail("Java test source path is not a regular file.");
    const module = modules.find((candidate) => within(candidate.absolute, absolute));
    if (!module) throw fail("Unknown Java module for test source.", "JAVA_MODULE_UNKNOWN");
    let directory = path.dirname(absolute);
    while (within(root, directory)) {
      const pom = path.join(directory, "pom.xml");
      if (await inspectPath(pom, { allowMissing: true })) {
        if (!modules.some((candidate) => pathKey(candidate.absolute) === pathKey(directory))) {
          throw fail("Unknown Java module between test source and release evidence.", "JAVA_MODULE_UNKNOWN");
        }
        break;
      }
      if (pathKey(directory) === pathKey(module.absolute)) break;
      directory = path.dirname(directory);
    }
    let bytes;
    let source;
    if (draft) {
      if (typeof entry.source !== "string") throw fail("A Java draft requires a source string.");
      source = entry.source;
      bytes = Buffer.from(source, "utf8");
    } else {
      bytes = await readBounded(absolute, MAX_SOURCE_BYTES);
      source = decodeUtf8(bytes, "Java test source");
    }
    totalBytes += bytes.length;
    if (totalBytes > MAX_TOTAL_SOURCE_BYTES) throw fail("Java test source batch exceeds its size limit.");
    validateJavaTestSource(source, module, filePath);
    validated.push({ path: filePath, sha256: sha256(bytes), modulePath: module.modulePath, testRelease: module.testRelease });
  }
  return { status: "passed", validation: "lexical", files: validated };
}

async function cli() {
  const [root, configFile, ...flags] = process.argv.slice(2);
  if (!root || !configFile || (flags.length && (flags.length !== 2 || flags[0] !== "--output"))) {
    throw fail("Usage: node java-test-preflight.mjs <codebase> <config.json> [--output <evidence.json>]");
  }
  const configBytes = await readBounded(path.resolve(configFile), MAX_XML_BYTES);
  let config;
  try {
    config = JSON.parse(decodeUtf8(configBytes, "preflight configuration"));
  } catch {
    throw fail("Invalid Java test preflight configuration JSON.");
  }
  if (!config || typeof config !== "object" || Array.isArray(config) || config.commandRunner !== undefined) {
    throw fail("Invalid Java test preflight configuration object.");
  }
  const evidence = await detectJavaTestRelease({ ...config, root });
  if (config.files !== undefined) await validateJavaTestFiles({ root, files: config.files, evidence });
  const json = `${JSON.stringify(evidence, null, 2)}\n`;
  if (flags.length) {
    const output = path.resolve(flags[1]);
    const metadata = await inspectPath(output, { allowMissing: true });
    if (metadata && !metadata.isFile()) throw fail("Evidence output is not a regular file.");
    await writeFile(output, json, { flag: "wx", mode: 0o600 });
  } else process.stdout.write(json);
}

if (process.argv[1] && pathKey(path.resolve(process.argv[1])) === pathKey(fileURLToPath(import.meta.url))) {
  cli().catch((error) => {
    process.stderr.write(`${error.code?.startsWith("JAVA_") ? error.message : "Java test preflight failed."}\n`);
    process.exitCode = 1;
  });
}
