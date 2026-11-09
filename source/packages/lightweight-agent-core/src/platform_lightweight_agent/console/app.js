(() => {
  "use strict";

  const state = { cursor: null, listRequest: 0, session: null };
  const byId = (id) => document.getElementById(id);
  const notice = byId("notice"), list = byId("run-list"), more = byId("more-runs");
  const token = byId("session-token");
  const json = (value) => JSON.stringify(value, null, 2);
  const active = (session) => state.session === session;
  const query = (session) => new URLSearchParams({ expected_execution_id: session.handle.execution_id });
  const path = (session, suffix) => "/v2/runs/" + encodeURIComponent(session.handle.run_id) + "/" + suffix;
  function say(message) { notice.textContent = message; }
  function sessionError(session, error) {
    if (active(session) && error.name !== "AbortError") say(error.message);
  }

  async function request(url, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    if (options.control) {
      if (!token.value) throw new Error("Enter the local control token first.");
      headers.set("X-V6-Local-Session", token.value);
    }
    const response = await fetch(url, { ...options, headers });
    const body = response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) throw new Error((body && body.code) || "Request failed (" + response.status + ").");
    return body;
  }

  function runButton(item) {
    const button = document.createElement("button");
    button.type = "button"; button.className = "run";
    button.dataset.selected = String(state.session && state.session.handle.execution_id === item.execution_id);
    button.textContent = item.run_id + " · " + item.status;
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = item.workflow_id + "@" + item.workflow_version + " · approvals: " + item.pending_approval_count;
    button.append(meta);
    button.addEventListener("click", () => selectRun(item));
    return button;
  }

  async function loadRuns(reset = true) {
    const requestId = ++state.listRequest, selection = state.session;
    if (reset) { state.cursor = null; list.replaceChildren(); }
    more.disabled = true;
    const params = new URLSearchParams({ limit: "50" });
    if (byId("pending-only").checked) params.set("pending_only", "true");
    if (state.cursor) params.set("cursor", state.cursor);
    try {
      const page = await request("/v2/runs?" + params);
      if (requestId !== state.listRequest) return;
      for (const item of page.items) list.append(runButton(item));
      state.cursor = page.next_cursor; more.hidden = !state.cursor;
      if (!state.session) say(list.childElementCount + " run(s) loaded.");
    } catch (error) {
      if (requestId === state.listRequest && selection === state.session) say(error.message);
    } finally {
      if (requestId === state.listRequest) more.disabled = false;
    }
  }

  function closeSession(session) {
    if (!session) return;
    session.controller.abort();
    if (session.events) session.events.close();
    if (session.timer) clearTimeout(session.timer);
  }

  function controls(session) {
    const enabled = active(session) && session.ready && !session.busy;
    byId("run-controls").hidden = !session.ready;
    for (const input of document.querySelectorAll("#run-controls button, #approvals button, #reconcile-form input, #reconcile-form textarea, #reconcile-form button")) {
      input.disabled = !enabled;
    }
  }

  function fileReferences(value, found = new Map()) {
    if (!value || typeof value !== "object") return found;
    if (!Array.isArray(value) && typeof value.logical_id === "string" &&
        typeof value.sha256 === "string" && Number.isInteger(value.size_bytes)) {
      found.set(value.sha256 + ":" + value.logical_id, value); return found;
    }
    for (const child of Array.isArray(value) ? value : Object.values(value)) fileReferences(child, found);
    return found;
  }

  function renderResultFiles(output) {
    const container = byId("result-files"); container.replaceChildren();
    const references = [...fileReferences(output).values()];
    if (!references.length) {
      const empty = document.createElement("span");
      empty.className = "meta"; empty.textContent = "No published result files."; container.append(empty);
    }
    for (const reference of references) {
      const row = document.createElement("div"), identity = document.createElement("span");
      row.className = "result-file"; row.textContent = reference.logical_id;
      identity.className = "meta"; identity.textContent = reference.size_bytes + " bytes · " + reference.sha256;
      row.append(identity); container.append(row);
    }
  }

  function renderProgress(progress) {
    const container = byId("step-progress"); container.replaceChildren();
    for (const step of progress) {
      const item = document.createElement("li"); item.dataset.state = step.status;
      item.textContent = step.step_id + " · " + step.status.toLowerCase(); container.append(item);
    }
  }

  async function refreshEvents(session) {
    const sequence = ++session.eventSequence;
    try {
      const events = await request(path(session, "events") + "?" + query(session), { signal: session.controller.signal });
      if (!active(session) || sequence !== session.eventSequence) return;
      byId("events").textContent = events.map(json).join("\n");
      // Events are a bounded diagnostic page, not the source of node state.
    } catch (error) {
      if (sequence === session.eventSequence) sessionError(session, error);
    }
  }

  async function refreshView(session) {
    if (!active(session)) return;
    const sequence = ++session.viewSequence;
    session.ready = false; controls(session);
    try {
      const view = await request(path(session, "view") + "?" + query(session), { signal: session.controller.signal });
      if (!active(session) || sequence !== session.viewSequence) return;
      if (!view || view.run_id !== session.handle.run_id || view.execution_id !== session.handle.execution_id ||
          !Number.isInteger(view.revision) || view.revision < 1) throw new Error("Run view identity is inconsistent.");
      if (view.revision >= session.revision) {
        session.revision = view.revision;
        const explanation = view.explanation, business = view.business_result;
        byId("run-title").textContent = view.run_id + " · " + view.execution_status;
        byId("execution-status").textContent = view.execution_status;
        byId("business-status").textContent = business ? business.status : "Not reported";
        byId("run-reason").textContent = (business && business.reason_code) || explanation.reason.code || explanation.reason.kind || "—";
        byId("detail").textContent = json(view);
        byId("explanation").textContent = json(explanation);
        approvalControls(session, explanation.pending_approvals || []);
        renderResultFiles(view.output); renderProgress(view.progress);
      }
      session.ready = true; controls(session);
    } catch (error) {
      if (sequence === session.viewSequence) sessionError(session, error);
    }
  }

  function scheduleRefresh(session, terminal = false) {
    if (!active(session)) return;
    if (session.timer) clearTimeout(session.timer);
    session.timer = setTimeout(() => {
      if (!active(session)) return;
      session.timer = null;
      void refreshEvents(session); void refreshView(session);
      if (terminal) void loadRuns(true);
    }, terminal ? 0 : 80);
  }

  function connectEvents(session) {
    if (!active(session)) return;
    const source = new EventSource(path(session, "events/stream") + "?" + query(session));
    session.events = source;
    for (const type of [
      "STEP_STARTED", "STEP_COMPLETED", "APPROVAL_REQUESTED", "APPROVALS_RECORDED",
      "ACTION_DISPATCHED", "ACTION_RESOLVED", "ACTION_OUTCOME_UNKNOWN",
      "RUN_WAITING_APPROVAL", "RUN_BLOCKED", "RUN_RECONCILING", "WORKFLOW_OUTPUT_ACCEPTED"
    ]) source.addEventListener(type, () => scheduleRefresh(session));
    source.addEventListener("v6-terminal", () => { source.close(); scheduleRefresh(session, true); });
    source.addEventListener("v6-stream-error", () => { source.close(); scheduleRefresh(session); });
  }

  async function control(session, suffix, payload) {
    if (!active(session) || !session.ready || session.busy) return;
    session.busy = true; controls(session);
    try {
      await request(path(session, suffix), {
        method: "POST", control: true, headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payload, expected_execution_id: session.handle.execution_id })
      });
      if (!active(session)) return;
      await refreshView(session); void refreshEvents(session); void loadRuns(true);
    } catch (error) { sessionError(session, error); }
    finally {
      session.busy = false;
      if (active(session)) controls(session);
    }
  }

  function approvalControls(session, approvals) {
    const container = byId("approvals"); container.replaceChildren();
    for (const item of approvals) {
      const row = document.createElement("div"), label = document.createElement("span");
      row.className = "approval";
      label.textContent = (item.step_id || "approval") + (item.approval_id ? " (" + item.approval_id + ")" : "");
      row.append(label);
      for (const approved of [true, false]) {
        const button = document.createElement("button");
        button.type = "button"; button.textContent = approved ? "Approve" : "Reject";
        button.addEventListener("click", () => {
          const payload = { approved };
          if (item.approval_id) payload.approval_id = item.approval_id;
          void control(session, "approval", payload);
        });
        row.append(button);
      }
      container.append(row);
    }
  }

  function selectRun(item) {
    closeSession(state.session);
    const session = {
      handle: Object.freeze({ run_id: item.run_id, execution_id: item.execution_id }),
      controller: new AbortController(), viewSequence: 0, eventSequence: 0,
      revision: 0, ready: false, busy: false, events: null, timer: null
    };
    state.session = session;
    say(""); byId("run-title").textContent = item.run_id + " · Loading";
    for (const id of ["execution-status", "business-status", "run-reason"]) byId(id).textContent = "—";
    for (const id of ["detail", "explanation", "events", "step-progress", "approvals", "result-files"]) byId(id).replaceChildren();
    byId("reconcile-form").reset(); controls(session);
    void refreshView(session); void refreshEvents(session); connectEvents(session);
  }

  byId("refresh-runs").addEventListener("click", () => {
    void loadRuns(true);
    if (state.session) { void refreshView(state.session); void refreshEvents(state.session); }
  });
  byId("pending-only").addEventListener("change", () => loadRuns(true));
  more.addEventListener("click", () => loadRuns(false));
  byId("cancel-run").addEventListener("click", () => {
    if (state.session) void control(state.session, "cancel", {});
  });
  byId("reconcile-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const session = state.session;
    if (!session || !session.ready || session.busy) return;
    try {
      const actionId = byId("reconcile-action-id").value.trim();
      if (!actionId) throw new Error("Action ID is required.");
      const succeeded = byId("reconcile-succeeded").checked, payload = { succeeded };
      if (succeeded) payload.output = JSON.parse(byId("reconcile-output").value);
      else {
        payload.error = byId("reconcile-error").value.trim();
        if (!payload.error) throw new Error("Error code is required.");
      }
      void control(session, "actions/" + encodeURIComponent(actionId) + "/reconcile", payload);
    } catch (error) { sessionError(session, error); }
  });
  window.addEventListener("beforeunload", () => closeSession(state.session));
  // No control is enabled before a validated view is rendered.
  for (const input of document.querySelectorAll("#reconcile-form input, #reconcile-form textarea, #reconcile-form button")) input.disabled = true;
  void loadRuns(true);
})();
