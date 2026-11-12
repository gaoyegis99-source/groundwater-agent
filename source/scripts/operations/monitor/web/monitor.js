const byId = (id) => document.getElementById(id);
const logs = byId("logs");
const windowLog = byId("log-window");
const filters = byId("filters");
const copyStatus = byId("copy-status");
const copyLogs = byId("copy-logs");
let filter = {};
let follow = true;
let stream;
let cursor = 0;
let olderCursor = null;
let generation = 0;
let lastStatus;
let disconnected = false;

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

function renderStatus(snapshot) {
  lastStatus = snapshot;
  const kernel = snapshot.services.find((item) => item.name === "kernel-service");
  const route = (name) => kernel?.metrics?.find((metric) => metric.name === "platform_intent_routing_" + name)?.value;
  const pending = route("pending");
  const oldest = route("oldest_wait_seconds");
  const age = snapshot.collectedAt === null ? null : Date.now() / 1000 - snapshot.collectedAt;
  const signals = [
    ["网关 " + statusText(snapshot.gatewayUp), snapshot.gatewayUp === false],
    ["Kernel 调度 " + statusText(kernel?.ready), kernel?.ready === false],
    ["需求判断 待领取 " + (pending ?? "未知") + " · 处理中 " + (route("running") ?? "未知"), oldest >= 60],
    ["最久等待 " + (oldest === undefined ? "未知" : Math.round(oldest) + " 秒"), oldest >= 60],
    [disconnected ? "实时连接中断，正在重连" : "实时连接已建立", disconnected],
  ];
  if (snapshot.warning || age === null || age > 12) {
    signals.push([snapshot.warning || "状态采集已过期", true]);
  }
  byId("signals").replaceChildren(...signals.map(([label, warning]) => {
    const item = document.createElement("span");
    item.textContent = label;
    item.classList.toggle("warn", warning);
    return item;
  }));
  byId("services-title").textContent = "服务状态 · " + snapshot.services.length;
  byId("services").replaceChildren(...snapshot.services.map((item) =>
    cellRow([item.service, item.process, item.health ?? "未配置", statusText(item.ready), item.heartbeat ?? "—", item.restarts, item.container])));
  byId("metrics").replaceChildren(...snapshot.services.flatMap((item) => {
    const rows = (item.metrics || []).map((metric) =>
      cellRow([item.name, metric.name, Object.entries(metric.labels).map(([key, value]) => key + "=" + value).join(" · "), metric.value]));
    if (item.metricsAvailable === false) rows.push(cellRow([item.name, "指标不可用", "", ""]));
    return rows;
  }));
  const selected = filters.elements.service.value;
  const services = [...new Set([...snapshot.services.map((item) => item.service), ...Array.from(filters.elements.service.options).map((o) => o.value)])].filter(Boolean).sort();
  filters.elements.service.replaceChildren(new Option("所有服务", ""), ...services.map((service) => new Option(service, service)));
  filters.elements.service.value = selected;
}

function logRow(entry) {
  if (entry.kind === "gap") {
    byId("notice").textContent = entry.service + " · " + entry.line;
  }
  if (filter.service && entry.service !== filter.service) return;
  if (filter.q && !entry.line.toLowerCase().includes(filter.q.toLowerCase())) return;
  if (filter.start && entry.epoch < filter.start || filter.end && entry.epoch > filter.end) return;
  const row = cellRow([new Date(entry.epoch * 1000).toLocaleString("zh-CN", {hour12: false}), entry.service, entry.line]);
  if (entry.kind === "gap") row.className = "warn";
  row.dataset.cursor = entry.id;
  row.dataset.stamp = entry.stamp;
  const copy = document.createElement("button");
  copy.type = "button";
  copy.textContent = "复制";
  copy.setAttribute("aria-label", "复制此条日志");
  copy.dataset.copyLine = "";
  row.insertCell().append(copy);
  return row;
}

function updateLogCount() {
  const count = logs.rows.length;
  byId("log-count").textContent = "已显示 " + count + " 条";
  copyLogs.disabled = count === 0;
}

async function copyText(text, success) {
  const dialog = byId("manual-copy");
  const field = byId("copy-text");
  copyStatus.textContent = "";
  if (navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      copyStatus.textContent = success;
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
    copyStatus.textContent = success;
  }
}

function renderLog(entry) {
  const row = logRow(entry);
  if (!row) return;
  logs.append(row);
  if (follow && logs.rows.length > 1500) {
    olderCursor = Number(logs.rows[1].dataset.cursor);
    byId("older").hidden = false;
    logs.deleteRow(0);
  }
  if (!follow && logs.rows.length > 1500) {
    byId("notice").textContent = "日志持续到达，已保留当前画面。恢复跟随可载入最新日志。";
    logs.deleteRow(logs.rows.length - 1);
  }
  if (follow) windowLog.scrollTop = windowLog.scrollHeight;
  updateLogCount();
}

function authScreen(message = "") {
  generation++;
  stream?.close();
  byId("workspace").hidden = true;
  byId("startup").hidden = true;
  byId("logout").hidden = true;
  byId("access-mode").hidden = true;
  byId("login").hidden = false;
  byId("login-error").textContent = message;
  logs.replaceChildren();
  for (const id of ["signals", "services", "metrics"]) byId(id).replaceChildren();
  lastStatus = undefined;
}

async function loadHistory() {
  const current = ++generation;
  stream?.close();
  const response = await fetch("/api/logs?" + new URLSearchParams(filter));
  if (generation !== current) return;
  if (response.status === 401 || response.status === 403) return authScreen("请重新登录监控。");
  if (!response.ok) throw new Error("无法读取日志");
  const history = await response.json();
  logs.replaceChildren();
  byId("notice").textContent = history.logs.length ? "" : "当前筛选范围内暂无日志。";
  history.logs.forEach(renderLog);
  updateLogCount();
  cursor = history.cursor;
  olderCursor = history.before;
  byId("older").hidden = !olderCursor;
  stream = new EventSource("/api/events?after=" + cursor);
  stream.onopen = () => { disconnected = false; if (lastStatus) renderStatus(lastStatus); };
  stream.onerror = async () => {
    disconnected = true;
    if (lastStatus) renderStatus(lastStatus);
    const state = await fetch("/api/status").catch(() => null);
    if (generation === current && (state?.status === 401 || state?.status === 403)) authScreen("请重新登录监控。");
  };
  stream.addEventListener("status", (event) => renderStatus(JSON.parse(event.data)));
  stream.addEventListener("log", (event) => {
    const row = JSON.parse(event.data);
    if (row.id > cursor) {
      cursor = row.id;
      renderLog(row);
    }
  });
  stream.addEventListener("gap", () => {
    loadHistory().then(() => { byId("notice").textContent = "断线期间的部分日志已超过保留期限。"; }).catch(showError);
  });
  stream.addEventListener("auth", () => authScreen("监控会话已失效。"));
}

function showError(error) {
  byId("notice").textContent = error.message;
  byId("startup").textContent = error.message;
}

async function openWorkspace() {
  const response = await fetch("/api/status");
  if (response.status === 401) return authScreen();
  if (response.status === 403) throw new Error("此监控地址仅对配置的局域网开放。");
  if (!response.ok) throw new Error("无法读取监控状态");
  byId("login").hidden = true;
  byId("startup").hidden = true;
  byId("workspace").hidden = false;
  const snapshot = await response.json();
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

byId("follow").addEventListener("click", (event) => {
  follow = !follow;
  event.currentTarget.textContent = follow ? "暂停跟随" : "恢复跟随";
  event.currentTarget.setAttribute("aria-pressed", String(follow));
  if (follow) loadHistory().catch(showError);
});

byId("older").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  const current = generation;
  try {
    const response = await fetch("/api/logs?" + new URLSearchParams({...filter, before: olderCursor}));
    if (current !== generation) return;
    if (response.status === 401 || response.status === 403) return authScreen("请重新登录监控。");
    if (!response.ok) throw new Error("无法读取更早的日志");
    const history = await response.json();
    const height = windowLog.scrollHeight;
    const top = windowLog.scrollTop;
    if (follow) byId("follow").click();
    logs.prepend(...history.logs.map(logRow).filter(Boolean));
    updateLogCount();
    windowLog.scrollTop = top + windowLog.scrollHeight - height;
    olderCursor = history.before;
    button.hidden = !olderCursor;
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
  }
});

logs.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-copy-line]");
  if (button) copyText(button.closest("tr").cells[2].textContent, "已复制此条日志");
});

copyLogs.addEventListener("click", () => {
  const lines = Array.from(logs.rows, (row) => (
    row.dataset.stamp + "  " + row.cells[1].textContent + "  " + row.cells[2].textContent
  ));
  if (lines.length) copyText(lines.join("\n"), "已复制 " + lines.length + " 条日志");
});

byId("manual-copy").addEventListener("close", () => { byId("copy-text").value = ""; });

byId("wrap").addEventListener("click", (event) => {
  const enabled = event.currentTarget.getAttribute("aria-pressed") !== "true";
  event.currentTarget.setAttribute("aria-pressed", String(enabled));
  windowLog.classList.toggle("no-wrap", !enabled);
});

filters.addEventListener("reset", () => {
  filter = {};
  loadHistory().catch(showError);
});

filters.addEventListener("submit", (event) => {
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
  loadHistory().catch(showError);
});

setInterval(() => { if (lastStatus && !byId("workspace").hidden) renderStatus(lastStatus); }, 5000);
openWorkspace().catch(showError);
