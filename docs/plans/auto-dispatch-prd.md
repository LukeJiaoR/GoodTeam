# PRD + Architecture — Minimal auto-dispatch (Moat #1, phase 1)

> Stage: product_requirement + architect (PRD/RFC gate). Review before per-role
> implementation prompts are issued.

## 1. Intent (why)

Today the human is the message bus: Claude writes per-role prompts → human pastes
them into interactive Gemini/Codex → babysits per-command permission prompts →
relays results back for review. Everything serializes on the human's attention,
with lots of idle time and step-by-step supervision.

Minimal auto-dispatch removes the relay: **GoodTeam hands the per-role prompts to
autonomous worker CLIs itself, runs them in parallel, and surfaces results +
evidence for one human review.** The human defines intent and reviews outcomes —
not keystrokes. This is phase 1 of Moat #1 (the dispatch primitive the full
collaboration graph is built on).

## 2. Value / acceptance

- Given a backend task prompt and a frontend task prompt, **one command**
  dispatches both to worker CLIs, runs them **in parallel** and **autonomously**
  (no per-command prompts — the bypass flags are already in `CLI_BACKENDS`), and
  returns, per job: role, adapter, status, `result.md` path, changed files, and
  the M1 `memory_writeback` summary.
- No manual copy-paste; no babysitting. The human reviews the consolidated result
  (result.md + the target repo's diff) and accepts or iterates — that review IS
  the gate.
- A failing task surfaces clearly (status `failed` + summary) without blocking
  the other track.

## 3. Scope (v1 = all three, per decision)

**In:**
1. **Base dispatch** — a parallel runner over the existing `create_job` /
   `run_job` / `CLI_BACKENDS` + a `goodteam-os dispatch` CLI + consolidated
   result reporting. Reuses M1 write-back and the autonomous flags as-is.
2. **Gemini worker adapter** — a new `CliBackendSpec` for the official `gemini`
   CLI so frontend tracks dispatch to Gemini (the user's real FE agent).
3. **Auto-verify + escalate-on-red** — after a track completes, run its declared
   verification command; on failure, flag/escalate to the human instead of
   surfacing it as "done." This is the half that turns "review every diff" into
   "review only failures."

**Out (full Moat #1, later):** the collaboration DAG/plan, inter-agent message
bus, triage/self-correction, committee/debate.

## 4. Architecture / decisions (the gate)

**D1 — Standalone dispatch, NOT the fixed delivery graph.** `graph.py`'s pipeline
(PRD→RFC→…) is a fixed CONTENTOPS-style flow with hardcoded tasks. This feature
dispatches **arbitrary role prompts** (like the handoffs Claude already writes)
without a full run — exactly what dogfooding GoodTeam's own development needs.

**D2 — Reuse the worker primitives.** `WorkerJobStore.create_job(role, adapter,
task, context, constraints, expected_artifacts)` + `run_job` already spawn the
autonomous CLI (bypass flags) and do M1 write-back. New code = a parallel runner
+ a thin CLI + reporting. Do not reimplement worker execution.

**D3 — Parallel execution.** `run_job` blocks (`Popen.communicate`). Run the jobs
concurrently via a small thread pool and collect results. Each job has its own
dir + heartbeat thread, so concurrent `run_job` calls are isolated.

**D4 — The gate = review of consolidated results (no new state machine in v1).**
Dispatch runs to completion, then prints a consolidated summary (per job: role,
adapter, status, result_path, changed_files, memory_writeback). The human reviews
each `result.md` + the target repo's `git diff` and decides to keep/commit or
iterate. Keep it minimal — no separate accept/reject persistence yet.

**D5 — Inputs.** A dispatch spec = a list of tracks, each `{role, adapter, task
(inline or file), context?, constraints?, expected_artifacts?}`. The CLI accepts
per-track task files + a shared `--project-root` (where workers run). Exact flag
syntax is the planner's to finalize; pin the behavior.

**D6 — Safety (ties to §0.1).** Workers run autonomous, so dispatch must target an
**isolated/owned repo** (a worktree, dev-workspace, or an isolated target) — never
arbitrary real configs. Lane boundaries are passed as `constraints`. Refuse or
warn if `--project-root` looks like a real/active project.

**D7 — Gemini worker adapter (verified invocation).** Add a `CliBackendSpec`
mirroring claude, using the official `gemini` CLI (NOT the interactive
Antigravity IDE — you don't headless-dispatch to an IDE):
- non-interactive: `-p/--prompt` (prompt can be passed as the value or appended
  to stdin)
- autonomous: `--approval-mode yolo` (auto-approve all tools)
- model: `--model <m>`; result captured from **stdout** (`capture="stdout"`)
- auth env: `GEMINI_API_KEY` / `GOOGLE_API_KEY` (plus OAuth creds)
- de-hardcode the binary like the others (`GOODTEAM_GEMINI_PATH`)
- register under `gemini` (and accept `agy` as an alias if convenient)

**D8 — Auto-verify + escalate-on-red.** Each track may declare a `verify`
command (e.g. `npm run build && npm run lint`, or `PYTHONPATH=src pytest -q`).
After the worker completes, run it in the track's project-root; record
`verify: {command, exit_code, passed, output_tail}`. A track's overall outcome is
`needs_human` if the worker failed OR verify failed; `ok` only when both pass.
The consolidated report groups jobs into **ok (auto) vs needs_human (escalated)**
so the human reviews only the red ones.

**Contract (CLI shape — illustrative, planner finalizes):**
```
goodteam-os dispatch --project-root <repo> \
  --track backend:codex:plans/feat-backend.md \
  --track frontend:gemini:plans/feat-frontend.md \
  [--verify backend:"PYTHONPATH=src pytest -q"] \
  [--verify frontend:"cd frontend && npm run build && npm run lint"] \
  [--constraints-file plans/lane.md]
```
→ runs tracks in parallel; prints JSON:
```json
{ "all_ok": false,
  "jobs": [ {"role":"backend","adapter":"codex","status":"completed",
             "result_path":"…/result.md","changed_files":[…],
             "memory_writeback":{…}, "verify":{"passed":true,…},
             "outcome":"ok","summary":"…"},
            {"role":"frontend","adapter":"gemini","status":"completed",
             "verify":{"passed":false,…}, "outcome":"needs_human","summary":"…"} ] }
```

## 5. Tracks (issued after this gate)
- **backend (Codex):** ① the parallel dispatch runner + `goodteam-os dispatch`
  CLI + consolidated ok/needs_human reporting; ② the Gemini `CliBackendSpec`
  (D7); ③ auto-verify (D8). New `dispatch.py` (or in `workers.py`) + adapter in
  `workers.py`. Tests: parallel fake-track dispatch; a failing worker AND a
  failing verify each route to `needs_human` without blocking the other track;
  isolation guard; Gemini command shape.
- **QA:** dispatch two `fake` tracks in parallel in an isolated workspace, one
  with a passing verify and one with a deliberately-failing verify; assert both
  jobs run, the passing one is `ok`, the failing one is `needs_human`, and the
  report separates them. (Use `fake` adapter — do not call real Gemini/Codex.)

## 6. Review-guidance gotchas (the plan MUST honor these)
1. **Reuse, don't reimplement** `create_job`/`run_job`/`CLI_BACKENDS`/M1
   write-back. The new code is a parallel runner + CLI + reporting + one adapter
   spec + verify-runner.
2. **Parallelism**: `run_job` blocks (`Popen.communicate`); run tracks via a
   thread pool. Each job has its own dir + heartbeat thread, so concurrency is
   isolated — but capture per-track exceptions so one crash doesn't kill the
   batch.
3. **Gemini adapter**: verified flags are `-p` + `--approval-mode yolo` +
   `--model`, result via stdout; mirror the claude spec exactly. Don't invent
   Antigravity-IDE invocation.
4. **Verify runs in the right cwd** (the track's project-root / a sub-path), and
   its failure must NOT be swallowed — it flips outcome to `needs_human`.
5. **Isolation guard (§0.1)**: refuse/warn if `--project-root` looks like a real
   active config; autonomous workers only against isolated/owned repos.
6. **Don't touch the fixed delivery graph** (`graph.py`) — this is standalone.

## 7. Self-hosting note (operational — NOT v1 implementation)

This is guidance for *using* dispatch to develop GoodTeam itself; it is **not code
to build in v1** (the planner/Codex should ignore it for implementation):

- Once we dispatch workers to develop GoodTeam, the **orchestrator must be a
  stable "release" GoodTeam** (a pinned checkout/venv at a blessed tag, e.g.
  `v1.0.0`), and the **target must be a worktree of the "dev" repo** — never the
  half-built dev code running its own dispatch against its own live files.
- "dev → release" graduation happens only after tests pass + a human bless. This
  is the same idea as the existing `self` smoke suite (git worktree from
  committed HEAD).
- For normal users this split does NOT exist (GoodTeam is the stable tool, their
  product is the target); it is only the GoodTeam-on-GoodTeam bootstrap case.
- Implication for this feature: D6's isolation guard already covers "target an
  isolated/owned repo," which a dev worktree satisfies. No extra code needed in
  v1 — just run `dispatch` from the release instance against a dev worktree when
  the time comes.
