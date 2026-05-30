# QA — Worker Backends panel: live browser integration test

You are the **QA / verification** agent for the GoodTeam repo. The "Worker
Backends" dashboard panel was just built across two tracks (Python backend +
React frontend). Your job is to **verify it works end-to-end in a real browser**
and report pass/fail **with evidence**. This is the integration test that the
backend and frontend agents could not do alone, because it requires both running
together.

## Rules (read first)

- **Verification only — do NOT edit feature code.** If a step fails, capture
  evidence (screenshot + the exact symptom) and record which track owns the fix
  (frontend = `frontend/src/App.jsx`; backend = `src/goodteam_os/web.py` /
  `projects.py`). Do not fix it in this pass and do not commit anything.
- Use a **throwaway** project key `QATEST`. Do **not** touch real project configs
  (e.g. `CONTENTOPS`, `AGENTSMESH`) and do **not** run `goodteam_os.cli smoke`
  anywhere — it mutates state.
- Clean up at the end (stop the server, delete the throwaway config).

## Workspace

- **Repo root (run everything from here):** `/Users/luke/WorkSpace/codeAi/GoodTeam`
- Tooling you need: a Python venv, Node/npm, and a **browser-automation tool**
  (Playwright — this repo has a `webapp-testing` skill and a `.playwright-cli/`;
  use whatever browser driver you have).

## What the feature is

In the dashboard's **project detail** view there is a **"Worker Backends"** panel
that lets a user set, per role, which worker backend (CLI) runs — a global
default plus per-role overrides — and shows each backend's readiness (CLI on
PATH + credentials present) with an actionable hint. The panel reads from
`GET /api/state?project=<KEY>` and saves via `POST /api/project-config`.

## Setup

```bash
cd /Users/luke/WorkSpace/codeAi/GoodTeam
python3 -m venv .venv 2>/dev/null; source .venv/bin/activate
pip install -e ".[dev]" >/dev/null

# Build the React app so the backend serves it from frontend/dist
(cd frontend && npm install >/dev/null && npm run build >/dev/null)

# Create a throwaway project (defaults to the `fake` backend, no creds needed)
goodteam-os init --key QATEST --name "QA Test" --repo "$PWD" --worker-mode fake

# Start the backend (it serves the built frontend on the same origin)
PYTHONPATH=src python -m goodteam_os.cli serve --workspace . --port 8799 &
sleep 2
```

### Fast pre-check (no browser) — confirm the API before driving the UI
```bash
curl -s "http://127.0.0.1:8799/api/state?project=QATEST" | python -c "import sys,json;sc=json.load(sys.stdin)['start_context'];print('options', sc['worker_backend_options']);print('roles', sc['worker_roles']);print('roleBackends', sc['project_report']['role_worker_backends'])"
```
Expect: `options ['fake', 'codex', 'anthropic', 'noop', 'human']`, `roles
['ui', 'implementation', 'qa']`, and a `roleBackends` map. If this fails, stop —
it's a backend problem; report it.

## Browser test steps (capture a screenshot at each ✦)

Open `http://127.0.0.1:8799/?project=QATEST`.

1. **Find the panel.** Navigate to the project detail and locate the **"Worker
   Backends"** panel (it may be a tab or a card/section). ✦ screenshot the panel.
2. **Initial display is correct:**
   - A **global default** selector showing `fake`.
   - One **per-role** row for each of `ui`, `implementation`, `qa`, each with a
     backend selector (options include "Use default" + the 5 backends).
   - A **readiness** area: at least one card (for `fake`) showing a "Ready" badge.
     (`fake` needs no credentials, so it should read Ready.)
3. **Change backends:** set `implementation` → **anthropic** and `qa` → **codex**,
   keep global default as is (or set it to `codex`). Click **Save**. ✦ screenshot.
4. **Save succeeds + readiness refreshes:**
   - A success toast/message appears (no error).
   - The readiness area now shows cards for the newly-used backends (**anthropic**
     and **codex**). Each shows Ready/Not-Ready **and a hint** — e.g. anthropic
     likely "Not Ready / run `claude login` or set ANTHROPIC_API_KEY" unless those
     exist on this machine; codex's hint depends on the local `codex` setup.
     ✦ screenshot showing the hint text (this is the core value of the feature).
5. **Persistence:** confirm the change hit disk:
   ```bash
   grep -A3 "worker_backend:\|workers:" projects/qatest.local.yaml
   ```
   Expect `workers:` to list `implementation: anthropic` and `qa: codex`.
6. **Reload round-trips:** refresh the page (or re-open the project). The panel
   should show `implementation = anthropic`, `qa = codex` (read back from the
   saved config). ✦ screenshot.
7. **"Use default" removes an override:** set `implementation` back to **"Use
   default"**, Save. Then:
   ```bash
   grep -A3 "workers:" projects/qatest.local.yaml
   ```
   Expect `implementation` to be **gone** from `workers:` (it now falls back to
   the global default). The panel should show `implementation` as "Use default".
8. **Error handling (optional):** if reachable, attempt an invalid save and
   confirm the UI surfaces the error message rather than silently failing.

### Role scope (correct behavior)
The panel manages only the dispatched roles `ui`/`implementation`/`qa`. A config
may contain other roles (e.g. a `frontend` entry seeded by older
`goodteam-os init`, or hand-added roles) — the panel must **ignore** those and
submit only the managed roles. If Save fails with `400 Unknown worker role:
<role>`, that is a **bug**, not expected behavior (it was fixed via
`patch-worker-backends-save-role-filter.md`; flag it if it reappears).

## Pass criteria

- Steps 1–7 behave as described; the on-disk config matches the UI after each
  Save; readiness cards render Ready/Not-Ready **with hint text**.
- `npm run build` and the page load with no console errors related to this panel.

## Report format

Produce a short markdown report: per-step PASS/FAIL, the screenshots, and for any
FAIL the exact symptom + which track (`frontend` / `backend`) owns it. Do not fix
code; do not commit.

## Cleanup
```bash
kill %1 2>/dev/null            # stop the backend server you started
rm -f projects/qatest.local.yaml
```
