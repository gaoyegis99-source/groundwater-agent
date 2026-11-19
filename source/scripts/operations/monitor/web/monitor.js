const byId = (id) => document.getElementById(id);
const logs = byId("logs");
const windowLog = byId("log-window");
const filters = byId("filters");
const moreFilters = byId("more-filters");
const copyStatus = byId("copy-status");
const copyLogs = byId("copy-logs");
const alertsOnly = byId("alerts-only");
const alertsOnlyPreference = "groundwater.monitor.alertsOnly";
const showRoutine = byId("show-routine");
const logDetail = byId("log-detail");
const entriesByRow = new WeakMap();
const logPageSize = 1000;
const logRowLimit = 1500;
const logTime = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
});
const routineRequests = new Set([
  "GET /healthz",
  "GET /readyz",
  "GET /api/v12/health",
  "GET /internal/health/ready",
  "GET /internal/metrics",
  "POST /internal/workers/v3/heartbeat",
  "POST /internal/workers/v3/nodes:claim",
]);
let filter = {};
let follow = true;
let stream;
let cursor = 0;
let olderCursor = null;
let hiddenRoutine = 0;
let generation = 0;
let lastStatus;

function cellRow(values) {
  const row = document.createElement("tr");
  for (const value of values) {
    const cell = row.insertCell();
    cell.textContent = value;
  }
  return row;
}

function statusText(value) {
  return value === true ? "正常" : value === false ? "异常" : "未知";
}

function showConnection(state) {
  const badge = byId("connection");
  const label = {
    connecting: "正在连接日志",
    online: "实时连接已建立",
    offline: "连接中断，正在重连",
    unavailable: "实时连接不可用",
  }[state];
  if (badge.textContent !== label) badge.textContent = label;
  badge.dataset.state = state;
  badge.hidden = false;
}

function setStatusWarning(snapshot) {
  const warning = byId("status-warning");
  const stale = snapshot.collectedAt == null || Date.now() / 1000 - snapshot.collectedAt > 12;
  const message = snapshot.warning || (stale ? "服务状态更新延迟" : "");
  if (warning.textContent !== message) warning.textContent = message;
  warning.hidden = !message;
}

function renderStatus(snapshot) {
  lastStatus = snapshot;
  const kernel = snapshot.services.find((item) => item.name === "kernel-service");
  const route = (name) => kernel?.metrics?.find((metric) => metric.name === "platform_intent_routing_" + name)?.value;
  const pending = route("pending");
  const running = route("running");
  const oldest = route("oldest_wait_seconds");
  for (const [id, ready] of [["gateway-status", snapshot.gatewayUp], ["kernel-status", kernel?.ready]]) {
    const status = byId(id);
    status.textContent = statusText(ready);
    status.dataset.state = ready === true ? "normal" : ready === false ? "attention" : "unknown";
  }
  const routing = byId("routing-status");
  routing.textContent = pending == null && running == null
    ? "未知"
    : (pending ?? "未知") + " 待领取 · " + (running ?? "未知") + " 处理中";
  routing.dataset.state = pending == null && running == null ? "unknown" : "";
  const wait = byId("oldest-wait");
  wait.textContent = oldest == null ? "未知" : Math.round(oldest) + " 秒";
  wait.dataset.state = oldest == null ? "unknown" : oldest >= 60 ? "attention" : "";
  setStatusWarning(snapshot);
  byId("services-title").textContent = "服务状态 · " + snapshot.services.length;
  byId("services").replaceChildren(...snapshot.services.map((item) =>
    cellRow([item.service, item.process, item.health ?? "未配置", statusText(item.ready), item.heartbeat ?? "—", item.restarts, item.container])));
  byId("metrics").replaceChildren(...snapshot.services.flatMap((item) => {
    const rows = (item.metrics || []).map((metric) =>
      cellRow([item.name, metric.name, Object.entries(metric.labels).map(([key, value]) => key + "=" + value).join(" · "), metric.value]));
    if (item.metricsAvailable === false) rows.push(cellRow([item.name, "指标不可用", "", ""]));
    return rows;
  }));
  const select = filters.elements.service;
  const knownServices = Array.from(select.options).slice(1).map(option => option.value);
  const services = [...new Set([...knownServices, ...snapshot.services.map(item => item.service)])];
  if (services.length > knownServices.length) {
    const selected = select.value;
    select.replaceChildren(new Option("所有服务", ""), ...services.sort().map(service => new Option(service, service)));
    select.value = selected;
  }
}

function describeLine({line, service}) {
  const inbound = /^INFO:\s+\S+ - "(?<method>[A-Z]+) (?<target>\/[^"\s]*) HTTP\/[\d.]+" (?<code>\d{3})(?: [^\r\n]*)?$/.exec(line);
  const outbound = /^INFO:httpx:HTTP Request: (?<method>[A-Z]+) https?:\/\/[^/"\s]+(?<target>\/[^"\s]*) "HTTP\/[\d.]+ (?<code>\d{3})(?: [^"\r\n]*)?"$/.exec(line);
  const gateway = service.endsWith("/gateway")
    && /^\S+ (?<code>\d{3}) (?<method>[A-Z]+) (?<target>\/\S*) request_time=/.exec(line);
  const request = inbound ?? outbound ?? gateway;
  if (request) {
    const {method, target, code} = request.groups;
    const status = Number(code);
    const path = target.split("?")[0].replace(/%3a/gi, ":");
    return {
      summary: code + "  " + method + " " + path,
      level: status >= 500 ? "error" : status >= 400 ? "warning" : "",
      routine: status >= 200 && status < 300 && routineRequests.has(method + " " + path),
    };
  }
  const level = /^\s*\[?(ERROR|CRITICAL|FATAL|WARN(?:ING)?)\b/i.exec(line)?.[1].toUpperCase();
  return {summary: line, level: level ? (level.startsWith("WARN") ? "warning" : "error") : ""};
}

function logRow(entry) {
  if (filter.service && entry.service !== filter.service) return;
  if (filter.q && !entry.line.toLowerCase().includes(filter.q.toLowerCase())) return;
  if (filter.start && entry.epoch < filter.start || filter.end && entry.epoch > filter.end) return;
  const {summary, level, routine} = entry.kind === "gap"
    ? {summary: entry.line, level: "gap"}
    : describeLine(entry);
  if (alertsOnly.checked && level !== "error" && level !== "warning") return;
  if (routine && !showRoutine.checked) {
    hiddenRoutine++;
    return;
  }
  const row = cellRow([logTime.format(entry.epoch * 1000), entry.service.split("/").at(-1)]);
  row.setAttribute("role", "row");
  row.cells[0].title = entry.stamp;
  row.cells[1].title = entry.service;
  row.dataset.cursor = entry.id;
  entriesByRow.set(row, entry);

  const message = document.createElement("button");
  message.type = "button";
  message.className = "log-message";
  message.setAttribute("aria-haspopup", "dialog");
  if (level) {
    row.dataset.level = level;
    const label = document.createElement("span");
    label.className = "log-level";
    label.textContent = {error: "错误", warning: "告警", gap: "间断"}[level];
    message.append(label);
  }
  const preview = document.createElement("span");
  preview.className = "log-preview";
  preview.textContent = summary.length > 640 ? summary.slice(0, 640) + "…" : summary || "空行";
  const open = document.createElement("span");
  open.className = "log-open";
  open.setAttribute("aria-hidden", "true");
  open.textContent = "›";
  message.append(preview, open);
  row.insertCell().append(message);
  return row;
}

function updateLogCount() {
  const count = logs.rows.length;
  byId("log-count").textContent = "已显示 " + count + " 条" + (alertsOnly.checked ? "错误或告警" : "")
    + (hiddenRoutine ? " · 已隐藏 " + hiddenRoutine + " 条例行日志" : "");
  copyLogs.disabled = count === 0 || windowLog.hasAttribute("data-loading");
  const empty = byId("empty-logs");
  empty.hidden = count > 0;
  if (count > 0) return;
  let title = "没有匹配的日志";
  let hint = Object.keys(filter).length ? "可修改搜索词，或清空筛选再查看。" : "新日志到达后会显示在这里。";
  if (alertsOnly.checked) {
    title = olderCursor ? "本批没有错误或告警" : "当前范围没有错误或告警";
    hint = olderCursor ? "点击「加载更早日志」继续查看。" : "取消勾选「只看错误和告警」可查看常规日志。";
  } else if (hiddenRoutine) {
    title = "本批只有例行日志";
    hint = "勾选「显示例行日志」即可查看。";
  }
  empty.querySelector("strong").textContent = title;
  empty.querySelector("span").textContent = hint;
}

function renderAppliedFilters() {
  const active = byId("applied-filters");
  const parts = [];
  if (filter.q) parts.push("搜索「" + filter.q + "」");
  if (filter.service) parts.push("服务 " + filter.service);
  const formatDate = value => new Date(value * 1000).toLocaleString("zh-CN", {hour12: false});
  if (filter.start) parts.push("从 " + formatDate(filter.start));
  if (filter.end) parts.push("到 " + formatDate(filter.end));
  byId("filter-summary").textContent = parts.length ? "已筛选　" + parts.join(" · ") : "";
  active.hidden = parts.length === 0;
}

async function copyText(text, success, feedback = copyStatus) {
  const dialog = byId("manual-copy");
  const field = byId("copy-text");
  feedback.textContent = "";
  if (navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      feedback.textContent = success;
      return;
    } catch {
      // The browser may deny clipboard access even on HTTPS.
    }
  }
  field.value = text;
  dialog.showModal();
  field.focus();
  field.select();
  if (document.execCommand?.("copy")) {
    dialog.close();
    field.value = "";
    feedback.textContent = success;
  }
}

function renderLog(entry) {
  const previousHidden = hiddenRoutine;
  const row = logRow(entry);
  if (!row) {
    if (hiddenRoutine !== previousHidden) updateLogCount();
    return;
  }
  if (!follow && logs.rows.length >= logRowLimit) {
    byId("notice").textContent = "日志持续到达，已保留当前画面。恢复跟随可载入最新日志。";
    return;
  }
  logs.append(row);
  if (logs.rows.length > logRowLimit) {
    logs.deleteRow(0);
    olderCursor = Number(logs.rows[0].dataset.cursor);
    byId("older").hidden = false;
  }
  if (follow) windowLog.scrollTop = windowLog.scrollHeight;
  updateLogCount();
}

function authScreen(message = "") {
  generation++;
  stream?.close();
  setHistoryLoading(false);
  byId("manual-copy").close();
  logDetail.close();
  byId("workspace").hidden = true;
  byId("startup").hidden = true;
  byId("logout").hidden = true;
  byId("access-mode").hidden = true;
  byId("connection").hidden = true;
  byId("login").hidden = false;
  byId("login-error").textContent = message;
  logs.replaceChildren();
  for (const id of ["services", "metrics"]) byId(id).replaceChildren();
  lastStatus = undefined;
}

function setHistoryLoading(loading) {
  windowLog.toggleAttribute("data-loading", loading);
  windowLog.setAttribute("aria-busy", String(loading));
  logs.inert = loading;
  copyLogs.disabled = loading || logs.rows.length === 0;
  if (loading) {
    byId("older").hidden = true;
    byId("empty-logs").hidden = true;
    byId("notice").textContent = "正在加载日志…";
  }
}

async function loadHistory() {
  const current = ++generation;
  stream?.close();
  showConnection("connecting");
  setHistoryLoading(true);
  let history;
  try {
    const response = await fetch("/api/logs?" + new URLSearchParams({...filter, limit: logPageSize}));
    if (generation !== current) return false;
    if (response.status === 401 || response.status === 403) {
      authScreen("请重新登录监控。");
      return false;
    }
    if (!response.ok) throw new Error("无法读取日志");
    history = await response.json();
  } catch {
    if (generation === current) {
      setHistoryLoading(false);
      showConnection("unavailable");
      byId("notice").textContent = logs.rows.length
        ? "查询失败，当前显示上次加载的日志。点击「查询」重试。"
        : "暂时无法加载日志。点击「查询」重试。";
    }
    return false;
  }
  if (generation !== current) return false;
  setHistoryLoading(false);
  hiddenRoutine = 0;
  olderCursor = history.before;
  byId("older").hidden = !olderCursor;
  byId("notice").textContent = "";
  logs.replaceChildren(...history.logs.map(logRow).filter(Boolean));
  renderAppliedFilters();
  updateLogCount();
  windowLog.scrollTop = follow ? windowLog.scrollHeight : 0;
  cursor = history.cursor;
  stream = new EventSource("/api/events?after=" + cursor);
  stream.onopen = () => {
    if (generation === current) showConnection("online");
  };
  stream.onerror = async () => {
    if (generation !== current) return;
    showConnection("offline");
    const state = await fetch("/api/status").catch(() => null);
    if (generation === current && (state?.status === 401 || state?.status === 403)) authScreen("请重新登录监控。");
  };
  stream.addEventListener("status", (event) => {
    if (generation === current) renderStatus(JSON.parse(event.data));
  });
  stream.addEventListener("log", (event) => {
    if (generation !== current) return;
    const row = JSON.parse(event.data);
    if (row.id > cursor) {
      cursor = row.id;
      renderLog(row);
    }
  });
  stream.addEventListener("gap", async () => {
    if (generation === current && await loadHistory()) {
      byId("notice").textContent = "断线期间的部分日志已超过保留期限。";
    }
  });
  stream.addEventListener("auth", () => {
    if (generation === current) authScreen("监控会话已失效。");
  });
  return true;
}

function showError(error) {
  byId("notice").textContent = error.message;
  byId("startup-status").textContent = error.message;
}

async function openWorkspace() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  let response;
  let snapshot;
  try {
    response = await fetch("/api/status", {signal: controller.signal});
    if (response.ok) snapshot = await response.json();
  } catch {
    throw new Error("暂时无法连接监控服务，请重新打开页面。");
  } finally {
    clearTimeout(timeout);
  }
  if (response.status === 401) return authScreen();
  if (response.status === 403) throw new Error("此监控地址仅对配置的局域网开放。");
  if (!response.ok) throw new Error("无法读取监控状态");
  byId("login").hidden = true;
  byId("startup").hidden = true;
  byId("workspace").hidden = false;
  byId("logout").hidden = snapshot.accessMode === "lan";
  byId("access-mode").hidden = snapshot.accessMode !== "lan";
  renderStatus(snapshot);
  await loadHistory();
}

byId("login").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button");
  button.disabled = true;
  try {
    const result = await fetch("/api/login", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
    });
    form.elements.password.value = "";
    if (!result.ok) throw new Error((await result.json()).detail || "登录失败");
    await openWorkspace();
  } catch (error) {
    byId("login-error").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

byId("logout").addEventListener("click", async () => {
  await fetch("/api/logout", {method: "POST"});
  authScreen();
});

function setFollow(enabled) {
  follow = enabled;
  const button = byId("follow");
  button.textContent = follow ? "暂停跟随" : "恢复跟随";
  button.classList.toggle("is-paused", !follow);
  const state = byId("follow-state");
  state.textContent = follow ? "跟随最新日志" : "跟随已暂停";
  state.dataset.state = follow ? "" : "attention";
}

byId("follow").addEventListener("click", () => {
  setFollow(!follow);
  if (follow) loadHistory().catch(showError);
});

windowLog.addEventListener("scroll", () => {
  if (follow && windowLog.scrollHeight - windowLog.clientHeight - windowLog.scrollTop > 48) {
    setFollow(false);
  }
});

byId("older").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "正在加载…";
  const current = generation;
  try {
    const response = await fetch("/api/logs?" + new URLSearchParams({
      ...filter, before: olderCursor, limit: logPageSize,
    }));
    if (current !== generation) return;
    if (response.status === 401 || response.status === 403) return authScreen("请重新登录监控。");
    if (!response.ok) throw new Error("无法读取更早的日志");
    const history = await response.json();
    if (current !== generation) return;
    const height = windowLog.scrollHeight;
    const top = windowLog.scrollTop;
    setFollow(false);
    byId("notice").textContent = "";
    olderCursor = history.before;
    button.hidden = !olderCursor;
    logs.prepend(...history.logs.map(logRow).filter(Boolean));
    updateLogCount();
    windowLog.scrollTop = top + windowLog.scrollHeight - height;
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
    button.textContent = "加载更早日志";
  }
});

logs.addEventListener("click", (event) => {
  const button = event.target.closest(".log-message");
  if (!button) return;
  const entry = entriesByRow.get(button.closest("tr"));
  setFollow(false);
  byId("log-detail-meta").textContent = new Date(entry.epoch * 1000).toLocaleString("zh-CN", {hour12: false})
    + " · " + entry.service;
  byId("log-detail-line").textContent = entry.line;
  byId("log-detail-line").scrollTop = 0;
  logDetail.showModal();
});

byId("copy-entry").addEventListener("click", () => {
  const original = byId("log-detail-line").textContent;
  copyText(original, "已复制原文", byId("entry-copy-status"));
});

logDetail.addEventListener("close", () => {
  byId("log-detail-line").textContent = "";
  byId("entry-copy-status").textContent = "";
});

copyLogs.addEventListener("click", () => {
  const lines = Array.from(logs.rows, (row) => {
    const entry = entriesByRow.get(row);
    return entry.stamp + "  " + entry.service + "  " + entry.line;
  });
  if (lines.length) copyText(lines.join("\n"), "已复制 " + lines.length + " 条日志");
});

byId("manual-copy").addEventListener("close", () => { byId("copy-text").value = ""; });

byId("wrap").addEventListener("change", (event) => {
  windowLog.classList.toggle("no-wrap", !event.currentTarget.checked);
  if (follow) windowLog.scrollTop = windowLog.scrollHeight;
});

showRoutine.addEventListener("change", () => {
  loadHistory().catch(showError);
});

try {
  alertsOnly.checked = localStorage.getItem(alertsOnlyPreference) === "true";
} catch {
  // The filter remains available when browser storage is blocked.
}
showRoutine.disabled = alertsOnly.checked;
alertsOnly.addEventListener("change", () => {
  showRoutine.disabled = alertsOnly.checked;
  try {
    localStorage.setItem(alertsOnlyPreference, String(alertsOnly.checked));
  } catch {
    // Apply the filter for this page even if the preference cannot be saved.
  }
  loadHistory().catch(showError);
});

filters.addEventListener("reset", () => {
  filter = {};
  moreFilters.open = false;
  filters.elements.q.focus();
  loadHistory().catch(showError);
});

filters.addEventListener("submit", async (event) => {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(filters));
  const nextFilter = Object.fromEntries(Object.entries(values).filter(([, value]) => value));
  for (const key of ["start", "end"]) {
    if (nextFilter[key]) nextFilter[key] = new Date(nextFilter[key]).getTime() / 1000;
  }
  if (nextFilter.start > nextFilter.end) {
    byId("notice").textContent = "起始时间必须早于结束时间。";
    return;
  }
  filter = nextFilter;
  const applied = await loadHistory().catch(showError);
  if (applied && moreFilters.contains(event.submitter)) {
    const returnFocus = document.activeElement === event.submitter;
    moreFilters.open = false;
    if (returnFocus) moreFilters.querySelector("summary").focus({preventScroll: true});
  }
});

setInterval(() => { if (lastStatus) setStatusWarning(lastStatus); }, 5000);
openWorkspace().catch(showError);
