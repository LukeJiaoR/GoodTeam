# PRD + architecture — worker failure triage & self-correction (Moat #1)

> Stage: product_requirement + architect. PRD/RFC gate — review before per-track
> handoffs. The highest-leverage remaining Moat #1 piece (product-vision §6.2).
> Direction (founder): "I need to stop being the always-on babysitter of output."

## 1. Intent (why)

Today every worker failure dead-ends. `graph.py:862-873`: any non-`completed`
worker → `state.next_node = "blocked"` + a blocker string, full stop. `dispatch.py`
groups failures into `needs_human` with no recovery. There is **no classification,
no retry, no distinction between a transient error and a real one.** So a flaky
`403 Forbidden` / expired token / `npm 504` / `better-sqlite3` ABI drift — all
**transient or mechanical** — halts the run and demands a human, exactly like a
genuine code bug would. The footprints show this eating hours repeatedly
(401/INVALID_API_KEY ×3, 504, ABI drift…). Triage turns the common, recoverable
failure modes into automatic recipes so the human is only pulled in for decisions
that actually need judgment.

## 2. Users & value

- **The operator (primary):** stops watching every run. A transient blip self-heals
  (retry with backoff); only a genuinely stuck/ambiguous failure escalates. This is
  the "stop babysitting output" win.
- **Design partners (later):** runs that don't die on the first network hiccup =
  the product feels reliable, not brittle.

## 3. User-facing acceptance

- A worker that fails with a **transient** error (auth-refresh/`401`/`403`,
  rate-limit, network/`5xx`, stream-disconnect, timeout) is **automatically retried
  with bounded backoff** (e.g. up to N attempts) before anything is marked blocked.
- A worker that fails with a **non-transient** error (real `code_bug`,
  `ambiguous_spec`, persistent auth-missing) is **classified and escalated** with a
  clear, typed reason — not a raw stack trace.
- The run state shows, per failed worker: a **failure class** (`auth`, `rate_limit`,
  `network`, `timeout`, `flaky`, `scaffold`, `code_bug`, `ambiguous_spec`,
  `unknown`), how many retries were attempted, and the final disposition
  (`recovered` / `escalated`).
- Retries and the triage decision are **recorded** (state + memory write-back) so
  the failure library compounds (a recipe seen once is applied next time).
- Nothing transient silently loops forever: a hard cap + escalation guarantees the
  human is pulled in if self-correction fails.

## 4. Scope

**In (phase 1 — deterministic triage, no extra LLM call):**
- A pure `classify_worker_failure(summary, exit_code, stderr) -> FailureClass`
  classifier driven by the worker summary text GoodTeam already produces
  (`workers.py` `_codex_summary`/`_claude_summary`: "invalid API key", "401
  Unauthorized", "stream disconnected", "exited with code N", timeout) + common
  patterns (`403`, `5xx`, `rate`, `ECONN`, `better-sqlite3`, `npm`/registry).
- A bounded **retry-with-backoff** loop for the transient classes, applied where
  jobs run (`WorkerJobStore.run_job` caller path + `dispatch._run_dispatched_job`).
- Typed disposition surfaced into `state` (and the `dispatch` job report) +
  memory write-back of the root-cause/decision.

**Out (later phases):** an LLM triage agent that reasons about novel failures;
auto-routing back to the spec author for `ambiguous_spec`; expert synthesis. Phase
1 is deterministic recipes for the *known* recoverable modes — the 80% that eats
time today.

## 5. Architecture / key decisions (decision record)

1. **Deterministic classifier first, not an LLM triage agent.** The recurring
   failure modes are mechanical and pattern-matchable from text GoodTeam already
   emits. A deterministic `classify_worker_failure` is testable, free, and CI-
   gateable. (LLM triage for novel failures is a later phase — don't block phase 1
   on it.) Rejected: jumping straight to an LLM agent (nondeterministic, costly,
   unverifiable in CI).
2. **Retry at the job-run layer, classify once.** Put the backoff loop where a job
   is executed so BOTH the graph path (graph.py implementation/qa jobs) and the
   `dispatch` path benefit, instead of duplicating logic. Classify the failure,
   and only retry classes tagged transient.
3. **Bounded + escalate.** Hard cap (default ~3) with exponential backoff; on
   exhaustion, mark blocked/needs_human WITH the failure class + attempt count, so
   escalation carries a typed reason, not a raw error.
4. **Record to memory (Moat #2 tie-in).** Each triage decision/root-cause is a
   memory write-back event → the failure library compounds. Reuse the existing
   `_write_back_memory` path.
5. **Config knobs, sane defaults.** Max attempts + which classes are retryable are
   defaults in code, optionally overridable per project; never require config to
   get the safe default behavior.

## 6. Verified ground truth (for the implementer)

- Failure today: `graph.py` ~line 862 collects non-`completed` workers →
  `next_node="blocked"` + `state.blockers.append(...)`. `dispatch.py`
  `_run_dispatched_job` / `_job_report` produce `outcome="needs_human"` on
  non-completed.
- Classifiable signal already exists: `workers.py` summaries emit "invalid API
  key", "401 Unauthorized", "stream disconnected before response.completed",
  "exited with code N", and a timeout path; `status.json` has `exit_code`,
  `summary`, `status`, and stderr is captured to `stderr.txt`.
- Real transient modes seen (footprints): `401 Unauthorized`/`INVALID_API_KEY`,
  `403 Forbidden`, expired/`refresh token`, `npm`/`registry … 504`,
  `better-sqlite3` ABI drift, `timed out`, `stream disconnected`.
- `run_job` already does memory write-back on completion — reuse that plumbing for
  recording triage outcomes.

## 7. Tracks (issued after this gate)
- **A. classifier + retry (Codex, backend):** `classify_worker_failure(...)`
  (pure, fully unit-tested against the real summary strings) + bounded
  backoff-retry at the job-run layer + typed disposition into state/dispatch
  report + memory write-back. Tests are deterministic (no real worker) → CI gate.
- **B. graph/dispatch wiring (Codex, backend):** route both the graph
  implementation/qa path and `dispatch` through the retry/classify layer; blocked
  now carries the failure class + attempt count.
- **C. QA (CI):** unit tests for every class + a fake-worker integration test that
  a transient-then-success sequence recovers and a persistent failure escalates
  with the right class. All deterministic → lands in CI per §0.3.
- (UI surfacing of failure class in the dashboard = small follow-up, not blocking.)

## 8. Open questions (resolve at this gate)
- **Default max attempts + backoff?** Recommend 3 attempts, expo backoff
  (e.g. 2s/8s/30s) — enough to ride out token-refresh/5xx without long stalls.
- **Which classes retry vs escalate?** Recommend retry: auth-refresh/`401`/`403`,
  rate_limit, network/`5xx`, timeout, stream-disconnect, flaky. Escalate
  immediately: `code_bug`, `ambiguous_spec`, persistent auth-missing (CLI not
  installed / no creds — retrying won't help).
- **Per-project override now or later?** Recommend code defaults now; project
  config override deferred unless trivial.
