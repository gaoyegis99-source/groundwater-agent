---
name: "playwright"
description: "Use when the task requires automating a real browser from the terminal (navigation, form filling, snapshots, screenshots, data extraction, UI-flow debugging) via `playwright-cli` or the bundled wrapper script."
---


# Playwright CLI Skill

Drive a real browser from the terminal using `playwright-cli`. Prefer the bundled wrapper script so the CLI works even when it is not globally installed.
Treat this skill as CLI-first automation. Do not pivot to `@playwright/test` unless the user explicitly asks for test files.

## Prerequisite check (required)

Before proposing commands, check whether `npx` is available (the wrapper depends on it):

```bash
command -v npx >/dev/null 2>&1
```

The wrapper requires Node.js 20 or newer and npm with `npx`.

## Skill path (set once)

Set `PWCLI` in the same shell as the Playwright commands, from any directory in the project checkout.

```bash
PWCLI="$(git rev-parse --show-toplevel)/.agents/skills/playwright/scripts/playwright_cli.sh"
```

If Chromium is missing from the project's browser cache, run `"$PWCLI" install-browser chromium`.

On the Ubuntu 20.04 workspace host, select the Ubuntu 22.04 fallback build for the download.

```bash
PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu22.04-x64 "$PWCLI" install-browser chromium
```

## Quick start

Use the wrapper script:

```bash
"$PWCLI" open https://playwright.dev
"$PWCLI" snapshot
"$PWCLI" click e15
"$PWCLI" type "Playwright"
"$PWCLI" press Enter
"$PWCLI" screenshot
```

## Core workflow

1. Open the page.
2. Snapshot to get stable element refs.
3. Interact using refs from the latest snapshot.
4. Re-snapshot after navigation or significant DOM changes.
5. Capture artifacts (screenshot, pdf, traces) when useful.

Minimal loop:

```bash
"$PWCLI" open https://example.com
"$PWCLI" snapshot
"$PWCLI" click e3
"$PWCLI" snapshot
```

## When to snapshot again

Snapshot again after:

- navigation
- clicking elements that change the UI substantially
- opening/closing modals or menus
- tab switches

Refs can go stale. When a command fails due to a missing ref, snapshot again.

## Recommended patterns

### Form fill and submit

```bash
"$PWCLI" open https://example.com/form
"$PWCLI" snapshot
"$PWCLI" fill e1 "user@example.com"
"$PWCLI" fill e2 "password123"
"$PWCLI" click e3
"$PWCLI" snapshot
```

### Debug a UI flow with traces

```bash
"$PWCLI" open https://example.com --headed
"$PWCLI" tracing-start
# ...interactions...
"$PWCLI" tracing-stop
```

### Multi-tab work

```bash
"$PWCLI" tab-new https://example.com
"$PWCLI" tab-list
"$PWCLI" tab-select 0
"$PWCLI" snapshot
```

## Wrapper script

The wrapper runs `@playwright/cli@0.1.25` through `npx` and defaults to Chromium. It stores browser binaries in `${XDG_CACHE_HOME:-$HOME/.cache}/groundwater-v6/playwright/browsers` unless `PLAYWRIGHT_BROWSERS_PATH` is set.

```bash
"$PWCLI" --help
```

## References

Open only what you need:

- CLI command reference: `references/cli.md`
- Practical workflows and troubleshooting: `references/workflows.md`

## Guardrails

- Always snapshot before referencing element ids like `e12`.
- Re-snapshot when refs seem stale.
- Prefer explicit commands over `eval` and `run-code` unless needed.
- When you do not have a fresh snapshot, use placeholder refs like `eX` and say why; do not bypass refs with `run-code`.
- Use `--headed` for checks that require a visible window when a display is available.
- When capturing artifacts in this repo, use `output/playwright/` and avoid introducing new top-level artifact folders.
- Default to CLI commands and workflows, not Playwright test specs.
