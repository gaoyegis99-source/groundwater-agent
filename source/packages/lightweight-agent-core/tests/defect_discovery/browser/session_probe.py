"""Real Chromium + SQLite/Web regression checks with controlled response order.

The browser harness deliberately ignores fetch abortion and can invoke a closed
EventSource's callback. Identity guards, not fortunate transport timing, must win.
No request headers, credentials, prompts, or browser storage dumps are recorded.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import runpy

import anyio
from playwright.sync_api import sync_playwright
from platform_lightweight_agent import LocalWebSecurity, create_web_app

REAL = runpy.run_path(str(Path(__file__).with_name("real_backend_probe.py")))


def off_loop(function, *args):
    # Playwright's sync facade owns an asyncio loop in this thread.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(function, *args).result()


HARNESS = r"""
(() => {
  const fetchReal = window.fetch.bind(window);
  const probe = window.probe = { rule: null, held: [], calls: [], sources: [], finished: 0 };
  window.fetch = async (url, options = {}) => {
    probe.calls.push({url: String(url), method: options.method || 'GET', body: options.body || null});
    const matches = probe.rule && String(url).includes(probe.rule);
    if (matches) probe.rule = null;
    const {signal, ...withoutAbort} = options;
    const response = await fetchReal(url, withoutAbort);
    if (!matches) return response;
    return new Promise(resolve => probe.held.push((failure = false) => {
      resolve(failure ? new Response(JSON.stringify({code:'STALE_ERROR'}), {status:500}) : response);
      probe.finished++;
    }));
  };
  window.EventSource = class {
    constructor(url) { this.url = url; this.callbacks = {}; this.closed = false; probe.sources.push(this); }
    addEventListener(type, handler) { this.callbacks[type] = handler; }
    close() { this.closed = true; }
    emit(type) { if (this.callbacks[type]) this.callbacks[type]({}); }
  };
})();
"""


def _select(page, suffix):
    page.locator("button.run", has_text="real-run-" + suffix).first.click()


def _ready(page, suffix, status="WAITING_APPROVAL"):
    page.wait_for_function(
        "([id,status]) => { const text=document.querySelector('#detail').textContent; if(!text)return false; const v=JSON.parse(text); return v.run_id===id && v.execution_status===status; }",
        arg=["real-run-" + suffix, status],
    )


def _hold(page, match):
    page.evaluate("value => window.probe.rule=value", match)


def _held(page):
    page.wait_for_function("() => window.probe.held.length === 1")


def _release(page, failure=False):
    page.evaluate("failure => window.probe.held.shift()(failure)", failure)
    page.wait_for_function("() => window.probe.finished === 1")
    # Flush fetch JSON parsing, promise continuations and stale debounce timers.
    page.wait_for_timeout(180)


def trial(browser, root, variant):
    root.mkdir()
    engine = off_loop(REAL["_identity_engine"], root)
    app = create_web_app(
        engine, security=LocalWebSecurity(session_token=REAL["SESSION"])
    )
    with REAL["RunningApp"](app) as origin:
        context = browser.new_context()
        context.add_init_script(HARNESS)
        page = context.new_page()
        page.goto(origin + "/console/")
        page.locator("button.run", has_text="real-run-A").wait_for()
        try:
            if variant in {"selection_ab", "selection_aba", "stale_error"}:
                _hold(page, "/real-run-A/view")
                _select(page, "A")
                _held(page)
                _select(page, "B")
                _ready(page, "B")
                if variant == "selection_aba":
                    off_loop(engine.cancel, "real-run-A")
                    _select(page, "A")
                    _ready(page, "A", "CANCELLED")
                _release(page, variant == "stale_error")
                expected = "A" if variant == "selection_aba" else "B"
                _ready(
                    page,
                    expected,
                    "CANCELLED" if expected == "A" else "WAITING_APPROVAL",
                )
                assert "STALE_ERROR" not in page.locator("#notice").inner_text()
            elif variant == "same_run_refresh_order":
                _select(page, "A")
                _ready(page, "A")
                _hold(page, "/real-run-A/view")
                page.locator("#refresh-runs").click()
                _held(page)
                off_loop(engine.cancel, "real-run-A")
                page.locator("#refresh-runs").click()
                _ready(page, "A", "CANCELLED")
                revision = json.loads(page.locator("#detail").inner_text())["revision"]
                _release(page)
                _ready(page, "A", "CANCELLED")
                assert (
                    json.loads(page.locator("#detail").inner_text())["revision"]
                    == revision
                )
            elif variant == "stale_sse":
                _select(page, "A")
                _ready(page, "A")
                _select(page, "B")
                _ready(page, "B")
                before = page.evaluate("window.probe.calls.length")
                page.evaluate("window.probe.sources[0].emit('v6-terminal')")
                page.wait_for_timeout(200)
                assert page.evaluate("window.probe.calls.length") == before
                assert page.evaluate("window.probe.sources[0].closed")
                _ready(page, "B")
            elif variant == "pending_controls":
                _select(page, "A")
                _ready(page, "A")
                _hold(page, "/real-run-B/view")
                _select(page, "B")
                _held(page)
                assert page.locator("#cancel-run").is_disabled()
                assert page.locator("#reconcile-form button").is_disabled()
                assert page.locator("#approvals button").count() == 0
                assert page.locator("#detail").inner_text() == ""
                _release(page)
                _ready(page, "B")
            elif variant == "bound_control_identity":
                _select(page, "A")
                _ready(page, "A")
                page.evaluate(
                    "window.oldApproval=document.querySelector('#approvals button')"
                )
                _select(page, "B")
                _ready(page, "B")
                page.locator("#session-token").fill(REAL["SESSION"])
                page.evaluate("window.oldApproval.click()")
                page.locator("#cancel-run").click()
                _ready(page, "B", "CANCELLED")
                controls = page.evaluate(
                    "window.probe.calls.filter(c=>c.method==='POST')"
                )
                assert len(controls) == 1
                assert controls[0]["url"] == "/v2/runs/real-run-B/cancel"
                assert (
                    json.loads(controls[0]["body"])["expected_execution_id"]
                    == engine.get("real-run-B").execution_id
                )
                assert engine.get("real-run-A").status == "WAITING_APPROVAL"
            elif variant == "list_order":
                _hold(page, "pending_only=true")
                page.locator("#pending-only").check()
                _held(page)
                page.locator("#pending-only").uncheck()
                page.wait_for_function(
                    "() => document.querySelectorAll('button.run').length===2"
                )
                _release(page)
                assert page.locator("button.run").count() == 2
            elif variant == "xss_token_storage_and_duplicate_click":
                current = engine.get("real-run-A")
                state = current._state_copy()
                state["v3"] = {
                    "output": {"text": '<img src=x onerror="window.xss=true">'}
                }
                engine.workflow_services.journal.replace(
                    current.next_revision(state=state),
                    expected_revision=current.revision,
                    event_type="FIXTURE",
                    payload={},
                )
                _select(page, "A")
                _ready(page, "A")
                assert "<img" in page.locator("#detail").inner_text()
                assert not page.evaluate("Boolean(window.xss)")
                page.locator("#session-token").fill(REAL["SESSION"])
                _hold(page, "/cancel")
                page.evaluate(
                    "document.querySelector('#cancel-run').click(); document.querySelector('#cancel-run').click()"
                )
                _held(page)
                assert (
                    page.evaluate(
                        "window.probe.calls.filter(c=>c.method==='POST').length"
                    )
                    == 1
                )
                assert page.evaluate("localStorage.length + sessionStorage.length") == 0
                _release(page)
            return {
                "case": "U04" if variant.startswith("xss_") else "U01",
                "variant": variant,
                "contract_result": "PASS",
            }
        finally:
            # Release a stalled synthetic response before closing the real server.
            page.evaluate("window.probe.held.forEach(release=>release())")
            context.close()


def collect(root):
    root.mkdir(parents=True, exist_ok=False)
    variants = [
        "selection_ab",
        "selection_aba",
        "same_run_refresh_order",
        "stale_error",
        "stale_sse",
        "pending_controls",
        "bound_control_identity",
        "list_order",
        "xss_token_storage_and_duplicate_click",
    ]
    rows = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(REAL["CHROMIUM"]), headless=True
        )
        try:
            for variant in variants:
                try:
                    rows.append(trial(browser, root / variant, variant))
                except Exception as error:
                    rows.append(
                        {
                            "case": "U04" if variant.startswith("xss_") else "U01",
                            "variant": variant,
                            "contract_result": "FAIL"
                            if isinstance(error, AssertionError)
                            else "ERROR",
                            "error_type": type(error).__name__,
                            "error": str(error),
                        }
                    )
            business = root / "csv"
            business.mkdir()
            engine, snapshots = off_loop(anyio.run, REAL["_prepare_csv_runs"], business)
            for row in REAL["business_trials"](browser, engine, snapshots):
                row["variant"] = {
                    "csv-real-valid": "business_approved",
                    "csv-real-invalid": "business_blocked",
                    "csv-real-execution-error": "execution_error",
                }[row["variant"]]
                rows.append(row)
            # A generic approval workflow has no domain business outcome.
            generic = root / "generic"
            generic.mkdir()
            engine = off_loop(REAL["_identity_engine"], generic)
            with REAL["RunningApp"](create_web_app(engine)) as origin:
                page = browser.new_page()
                page.goto(origin + "/console/")
                _select(page, "A")
                _ready(page, "A")
                passed = page.locator("#business-status").inner_text() == "Not reported"
                rows.append(
                    {
                        "case": "U02",
                        "variant": "generic_output",
                        "contract_result": "PASS" if passed else "FAIL",
                    }
                )
                page.close()
        finally:
            browser.close()
    return {
        "schema_version": "v6-console-session-evidence-v2",
        "result": "PASS"
        if all(r["contract_result"] == "PASS" for r in rows)
        else "FAIL",
        "backend": "real create_web_app + SQLite + real CSV wheel; controlled fetch order and stale SSE callbacks",
        "results": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = collect(args.evidence_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "result": report["result"],
                "cases": [
                    (r["variant"], r["contract_result"]) for r in report["results"]
                ],
            }
        )
    )
    raise SystemExit(0 if report["result"] == "PASS" else 1)
