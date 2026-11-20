import { createHash } from "node:crypto";
import { readFile, realpath, stat } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";

const MAX_FILE_BYTES = 16 * 1024 * 1024;
const MAX_CONTEXT_BYTES = 100_000_000;
const FILE_OPTIONS = [
  "--global-toolchains", "--global-settings", "--toolchains", "--settings", "--file",
  "-gs", "-gt", "-f", "-s", "-t",
];

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function digestJson(value) {
  return sha256(JSON.stringify(value));
}

function requiredString(value, label) {
  if (typeof value !== "string" || !value.trim() || value.length > 16_384 ||
    /[\u0000-\u001f\u007f]/u.test(value)) {
    throw new TypeError(`${label} must be a nonempty string without control characters`);
  }
  return value;
}

function pathKey(value) {
  return process.platform === "win32" ? value.toLowerCase() : value;
}

function inside(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}

function mavenFileOptions(args) {
  const files = [];
  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index];
    if (["-fae", "-ff", "-fn"].includes(argument)) continue;
    const option = FILE_OPTIONS.find((value) => argument === value ||
      argument.startsWith(`${value}=`) ||
      (!value.startsWith("--") && argument.startsWith(value)));
    if (!option) continue;
    const value = argument === option ? args[++index] : argument.slice(option.length).replace(/^=/, "");
    requiredString(value, "Maven build file option");
    files.push({ option, value, pom: ["-f", "--file"].includes(option) });
  }
  return files;
}

export async function fingerprintJavaBuildContext({
  root: rootInput,
  mavenArgs = [],
  mavenExecutable = "mvn",
  javaExecutable = "java",
  runtimeJavaMajor,
  effectivePomText,
  externalInputDigest,
  commandRunner,
} = {}) {
  requiredString(rootInput, "root");
  requiredString(mavenExecutable, "mavenExecutable");
  requiredString(javaExecutable, "javaExecutable");
  if (!Array.isArray(mavenArgs) || mavenArgs.length > 512) {
    throw new TypeError("mavenArgs must be a bounded array");
  }
  for (const argument of mavenArgs) requiredString(argument, "Maven argument");
  if (runtimeJavaMajor !== undefined && runtimeJavaMajor !== null &&
    (!Number.isInteger(runtimeJavaMajor) || runtimeJavaMajor < 1 || runtimeJavaMajor > 999)) {
    throw new TypeError("runtimeJavaMajor must be a Java major version");
  }
  if (effectivePomText !== undefined &&
    (typeof effectivePomText !== "string" || Buffer.byteLength(effectivePomText) > 8 * 1024 * 1024)) {
    throw new TypeError("effectivePomText must contain bounded XML text");
  }
  if (externalInputDigest !== undefined && externalInputDigest !== null) {
    requiredString(externalInputDigest, "externalInputDigest");
  }
  if (commandRunner !== undefined && typeof commandRunner !== "function") {
    throw new TypeError("commandRunner must be a function");
  }

  const root = await realpath(rootInput);
  if (!(await stat(root)).isDirectory()) throw new TypeError("root must be a directory");
  const buildInputs = new Set();
  const inRepository = [];
  const external = [];
  const fileDigests = new Map();
  let bytesRead = 0;

  async function optionalFileDigest(file) {
    const key = pathKey(file);
    if (fileDigests.has(key)) return fileDigests.get(key);
    let metadata;
    try {
      metadata = await stat(file);
    } catch (error) {
      if (["ENOENT", "ENOTDIR"].includes(error.code)) {
        fileDigests.set(key, null);
        return null;
      }
      throw new Error("Cannot inspect a Java/Maven fingerprint input");
    }
    if (!metadata.isFile() || metadata.size > MAX_FILE_BYTES ||
      bytesRead + metadata.size > MAX_CONTEXT_BYTES) {
      throw new Error("Java/Maven fingerprint input is not a bounded regular file");
    }
    let contents;
    try { contents = await readFile(file); } catch {
      throw new Error("Cannot read a Java/Maven fingerprint input");
    }
    let after;
    try { after = await stat(file); } catch {
      throw new Error("Java/Maven fingerprint input changed while being read");
    }
    // Windows executable inspection can change metadata-only ctime.
    const metadataChanged = after.size !== metadata.size || after.mtimeMs !== metadata.mtimeMs ||
      after.ino !== metadata.ino || after.dev !== metadata.dev ||
      (process.platform !== "win32" && after.ctimeMs !== metadata.ctimeMs);
    if (contents.length !== metadata.size || contents.length > MAX_FILE_BYTES ||
      bytesRead + contents.length > MAX_CONTEXT_BYTES ||
      metadataChanged) {
      throw new Error("Java/Maven fingerprint input changed or exceeded its budget");
    }
    bytesRead += contents.length;
    const digest = sha256(contents);
    fileDigests.set(key, digest);
    return digest;
  }

  function recordFile(file, digest, kind) {
    if (inside(root, file)) {
      const relative = path.relative(root, file);
      if (digest !== null) buildInputs.add(relative);
      inRepository.push({ kind, path: relative, digest });
    } else {
      external.push({ kind, pathDigest: sha256(pathKey(file)), digest });
    }
  }

  async function fingerprintFile(value, kind, { required = false, pom = false } = {}) {
    let file = path.resolve(root, value.replace(/[\\/]/g, path.sep));
    if (pom) {
      try {
        if ((await stat(file)).isDirectory()) file = path.join(file, "pom.xml");
      } catch (error) {
        if (!["ENOENT", "ENOTDIR"].includes(error.code)) {
          throw new Error("Cannot inspect the configured Maven project input");
        }
      }
    }
    const digest = await optionalFileDigest(file);
    if (required && digest === null) throw new Error("An explicit Maven build fingerprint input is missing");
    recordFile(file, digest, kind);
  }

  async function executableDigest(executable, kind) {
    const qualified = /[\\/]/.test(executable) || path.isAbsolute(executable);
    const directories = qualified
      ? [root]
      : [
        ...(process.platform === "win32" ? [root] : []),
        ...(process.env.PATH ?? "").split(path.delimiter)
          .map((directory) => directory.replace(/^"|"$/g, "") || root),
      ];
    const extensions = process.platform === "win32" && !path.extname(executable)
      ? [...new Set([...(process.env.PATHEXT ?? ".COM;.EXE;.BAT;.CMD").split(";").filter(Boolean), ""])]
      : [""];
    for (const directory of directories) {
      for (const extension of extensions) {
        const file = path.resolve(directory, `${executable}${extension}`);
        const digest = await optionalFileDigest(file);
        if (digest === null) continue;
        recordFile(file, digest, kind);
        return file;
      }
    }
    external.push({ kind, commandDigest: sha256(executable), digest: null });
    return null;
  }

  for (const { option, value, pom } of mavenFileOptions(mavenArgs)) {
    await fingerprintFile(value, `maven-option:${option}`, { required: true, pom });
  }
  const userHome = process.env.MAVEN_USER_HOME ?? path.join(os.homedir(), ".m2");
  for (const name of ["settings.xml", "toolchains.xml"]) {
    await fingerprintFile(path.join(userHome, name), `maven-user:${name}`);
  }
  for (const home of [process.env.MAVEN_HOME, process.env.M2_HOME].filter(Boolean)) {
    for (const name of ["settings.xml", "toolchains.xml"]) {
      await fingerprintFile(path.join(home, "conf", name), `maven-global:${name}`);
    }
  }
  for (const home of [process.env.JAVA_HOME, process.env.JDK_HOME, process.env.JRE_HOME].filter(Boolean)) {
    await fingerprintFile(path.join(home, "release"), "java-environment-release");
  }
  for (const name of [".java-version", ".sdkmanrc", ".tool-versions"]) {
    await fingerprintFile(name, "java-version-selection");
  }
  await executableDigest(mavenExecutable, "maven-executable");
  const javaFile = await executableDigest(javaExecutable, "java-executable");
  if (javaFile) {
    const canonical = await realpath(javaFile);
    await fingerprintFile(path.resolve(path.dirname(canonical), "..", "release"), "java-executable-release");
  }
  const ordered = (entries) => entries.sort((left, right) =>
    JSON.stringify(left).localeCompare(JSON.stringify(right)));
  const composedExternalDigest = digestJson({
    supplied: externalInputDigest ?? null,
    files: ordered(external),
  });
  const contextDigest = digestJson({
    schemaVersion: 1,
    rootDigest: sha256(pathKey(root)),
    mavenExecutable,
    mavenArgs,
    javaExecutable,
    runtimeJavaMajor: runtimeJavaMajor ?? null,
    effectivePomDigest: effectivePomText === undefined ? null : sha256(effectivePomText),
    environmentDigest: digestJson(Object.entries(process.env).sort(
      ([left], [right]) => left.localeCompare(right),
    )),
    buildInputs: ordered(inRepository),
    externalInputDigest: composedExternalDigest,
  });
  return {
    contextDigest,
    buildInputs: [...buildInputs].sort(),
    externalInputDigest: composedExternalDigest,
  };
}
