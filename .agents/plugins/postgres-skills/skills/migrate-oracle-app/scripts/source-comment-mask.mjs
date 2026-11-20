import path from "node:path";

const XML_EXTENSIONS = new Set([
  ".xml", ".csproj", ".fsproj", ".vbproj", ".vcxproj", ".props", ".targets", ".resx", ".slnx",
]);

function incomplete(code) {
  return Object.assign(new Error("Source comment or literal syntax requires review."), { code });
}

function lineEnd(content, start) {
  let end = start;
  while (end < content.length && content[end] !== "\r" && content[end] !== "\n") end += 1;
  return end;
}

export function sourceLiteralAt(content, start, { maxContentLength = Infinity, rawStrings = false } = {}) {
  const quote = content[start];
  if (!['"', "'", "`"].includes(quote)) return null;
  const verbatim = quote === '"' &&
    (content[start - 1] === "@" || content.slice(start - 2, start) === "@$");
  let count = 1;
  while (content[start + count] === quote) count += 1;
  if (!verbatim && quote === '"' && count >= 6 &&
    /[;,\])}]/.test(content[start + count] ?? "")) {
    return { start, contentStart: start + count, contentEnd: start + count,
      end: start + count, verbatim: false, terminated: true, oversized: false };
  }
  const delimiter = !verbatim && count >= 3 ? quote.repeat(count) : quote;
  const raw = rawStrings && quote === '"' && delimiter.length >= 3;
  const contentStart = start + delimiter.length;
  let index = contentStart;
  while (index < content.length) {
    if (verbatim && content.startsWith('""', index)) {
      index += 2;
      continue;
    }
    if (!verbatim && !raw && content[index] === "\\") {
      index += 2;
      continue;
    }
    if (content.startsWith(delimiter, index)) {
      return { start, contentStart, contentEnd: index, end: index + delimiter.length,
        verbatim, terminated: true, oversized: index - contentStart > maxContentLength };
    }
    index += 1;
  }
  return { start, contentStart, contentEnd: content.length, end: content.length,
    verbatim, terminated: false, oversized: content.length - contentStart > maxContentLength };
}

export function maskSourceComments(content, relativePath) {
  const extension = path.extname(relativePath).toLowerCase();
  const name = path.basename(relativePath).toLowerCase();
  const xml = XML_EXTENSIONS.has(extension) || name === ".project" ||
    (extension === ".config" && content.trimStart().startsWith("<"));
  const properties = [".properties", ".prefs"].includes(extension);
  const solution = extension === ".sln";
  const code = [".java", ".cs"].includes(extension);
  if (!xml && !properties && !solution && !code) return content;

  const pieces = [];
  let retained = 0;
  const mask = (start, end) => {
    pieces.push(content.slice(retained, start), content.slice(start, end).replace(/[^\r\n]/g, " "));
    retained = end;
  };
  if (properties || solution) {
    let start = 0;
    let continued = false;
    while (start < content.length) {
      const end = lineEnd(content, start);
      const line = content.slice(start, end);
      if (!continued && (properties ? /^[ \t\f]*[#!]/ : /^[ \t\f]*#/).test(line)) {
        mask(start, end);
      } else {
        let last = end - 1;
        let slashes = 0;
        while (last >= start && content[last--] === "\\") slashes += 1;
        continued = properties && slashes % 2 === 1;
      }
      start = end + (content.startsWith("\r\n", end) ? 2 : 1);
    }
  } else {
    let index = 0;
    let inTag = false;
    while (index < content.length) {
      if (xml && content.startsWith("<![CDATA[", index)) {
        const end = content.indexOf("]]>", index + 9);
        if (end < 0) throw incomplete("SOURCE_CDATA_UNTERMINATED");
        index = end + 3;
      } else if (xml && content.startsWith("<!--", index)) {
        const end = content.indexOf("-->", index + 4);
        if (end < 0) throw incomplete("SOURCE_COMMENT_UNTERMINATED");
        mask(index, end + 3);
        index = end + 3;
      } else if (code && content.startsWith("//", index)) {
        const end = lineEnd(content, index + 2);
        mask(index, end);
        index = end;
      } else if (code && content.startsWith("/*", index)) {
        const end = content.indexOf("*/", index + 2);
        if (end < 0) throw incomplete("SOURCE_COMMENT_UNTERMINATED");
        mask(index, end + 2);
        index = end + 2;
      } else if (code) {
        const literal = sourceLiteralAt(content, index, { rawStrings: extension === ".cs" });
        if (literal && !literal.terminated) throw incomplete("SOURCE_LITERAL_UNTERMINATED");
        index = literal ? literal.end : index + 1;
      } else if (inTag && ['"', "'"].includes(content[index])) {
        const end = content.indexOf(content[index], index + 1);
        if (end < 0) throw incomplete("SOURCE_LITERAL_UNTERMINATED");
        index = end + 1;
      } else {
        if (content[index] === "<") inTag = true;
        if (content[index] === ">") inTag = false;
        index += 1;
      }
    }
  }
  return pieces.length ? pieces.join("") + content.slice(retained) : content;
}
