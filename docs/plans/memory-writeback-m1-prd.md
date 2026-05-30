# PRD + Architecture — M1: automatic memory write-back

> Stage: **product_requirement + architect** (PRD/RFC gate). Review before the
> per-role implementation prompts are issued.

## 1. Intent (why)

Today the memory loop is **half-open**: workers *read* memory on entry
(`build_pickup_packet`), but **nothing writes memory on exit**. Every lesson is
captured only when a human hand-edits markdown. So `events.jsonl` is ~empty,
which is why retrieval (the BM25 work) and the planned Memory panel have nothing
to show.

M1 closes the loop: when a worker finishes, parse its `result.md` for structured
sections and append them as memory events. **Every run silently grows the
corpus.** This is the prerequisite for the entire Moat #2 — retrieval, the Memory
panel, cross-project knowledge, and (later) the temporal graph all consume the
events M1 produces.

It also lays the "own the ledger" foundation we agreed on: write-back goes
through `append_memory_event`, i.e. into the locally-owned `events.jsonl` WAL.
Any future engine (embeddings via LangGraph Store, or Graphiti) becomes a
*projection* of that same log — never the source of truth.

## 2. Value / acceptance

- After a worker job completes, durable takeaways from its `result.md` appear as
  events in the role's `events.jsonl` and in the role's horizon markdown — with
  no human editing.
- The next job for that role retrieves those facts (the BM25 loop now has real
  fuel).
- It works for real CLI workers (codex/claude) that emit the sections; builtin
  `fake`/`noop` jobs (which emit no sections) are a clean no-op.

## 3. Scope

**In:** worker-`result.md` → memory events, on job completion, via
`append_memory_event`. Plus the worker prompt change that makes workers emit the
sections.

**Out (fast-follows, not this round):** review-gate decisions → mid-term;
triage/failure-library write-back; embeddings; the Memory panel (separate FE
feature, already PRD'd — M1 is what makes it non-empty); cross-project store.

**Frontend:** ~none. M1 is **backend + QA only**. (The visualization is the
separate Memory-panel feature; sequence M1 → then that panel has real data.)

## 4. Architecture / decisions (the gate)

**D1 — Hook point: `WorkerJobStore.run_job`, after dispatch, on `completed`.**
Refactor `run_job` so all adapter branches return a `status_payload`, then:
```python
status_payload = <dispatch as today>
if status_payload.get("status") == "completed":
    self._write_back_memory(job_root, job)
return status_payload
```
One choke point → covers codex, claude, and any future CLI backend uniformly.
`fake`/`noop`/`human` produce no sections, so write-back is a harmless no-op.

**D2 — `result.md` section schema.** Parse these markdown sections; each `- `
bullet becomes one event (skip empty sections):
- `## Learned`
- `## Caveats`
- `## Decisions`
- `## Open Questions`

**D3 — Section → horizon mapping** (proposed; this is a decision to confirm):
| Section | Horizon | Rationale |
|---|---|---|
| `## Decisions` | long-term | durable architectural/contract decisions |
| `## Learned` | mid-term | knowledge that may later stabilize upward |
| `## Caveats` | mid-term | active risks/gotchas |
| `## Open Questions` | short-term | current, unresolved, near-term |

(Promotion mid→long over time is a later concern; new write-backs land low and
get promoted, per the existing memory "Update Rules".)

**D4 — Worker prompt change.** Extend `_worker_prompt` so workers end `result.md`
with these sections (omit if empty), one concise bullet each. Without this, real
workers won't emit structured memory.

**D5 — Source + dedup.** Each event's `source` = `"<job_id>/result.md"`. Guard
against duplicates: before appending, skip a bullet whose exact text already
exists in the role's recent events (so re-running a job doesn't spam the log).
Cap bullets per section (e.g. ≤ 10) to bound flooding.

**D6 — Safety.** Write-back must never break job completion: wrap parsing/writing
so any error is logged into the job status (e.g. a `memory_writeback` note) but
does not flip a completed job to failed. No secrets are parsed/echoed (memory is
non-secret by policy; rely on workers not putting secrets in result.md).

## 5. Tracks (issued after this gate is approved)
- **backend (Codex):** `run_job` refactor + `_write_back_memory` + a
  `parse_result_sections(text)` helper (pure, unit-testable) + `_worker_prompt`
  update; tests for parsing, horizon mapping, dedup, and the no-op path.
- **QA:** drive a job whose `result.md` contains the sections (or seed one),
  confirm events land in the right horizons + `events.jsonl`, and that a re-run
  doesn't duplicate. Verification-only.

## 6. Implementation notes the plan MUST honor (review guidance)

Verified against the current code; the implementation plan must address each:

1. **Same-run write-back does NOT reach downstream jobs — state this as expected
   behavior.** `graph.py` creates all three jobs (ui/impl/qa) — and each builds
   its pickup packet at `create_job` time — *before* any `run_job`
   (`graph.py:1011` builds the list, `graph.py:1048` runs it). So a job's
   write-back only benefits the *next run*, not later jobs in the same run. The
   plan must document this so QA doesn't misreport it. (Intra-run propagation =
   deferring packet build to run time = out of scope; note as fast-follow.)

2. **Parser tolerance is make-or-break.** `result.md` is freeform LLM text. The
   section parser must be lenient: case-insensitive headers, accept `##`/`###`,
   accept a trailing colon (`## Learned:`), accept `-`/`*`/`•` bullets, and fall
   back to the section's prose paragraph when there are no bullets. A brittle
   parser captures nothing and makes M1 a no-op. Put the bulk of test effort here.

3. **Make parsing a pure function.** `parse_result_sections(text) ->
   list[(horizon, text)]`, no IO — unit-test it with many freeform samples.
   `_write_back_memory` is then a thin wrapper that only calls
   `append_memory_event`.

4. **Dedup must be built here — `append_memory_event` has none** (it only
   validates horizon/non-empty text, then always appends). Before appending, skip
   a bullet whose exact text already exists in the role's recent events; cap
   bullets per section (≤10) to bound flooding.

5. **Don't over-engineer the horizon mapping (D3) — retrieval is
   horizon-agnostic.** `retrieve_relevant_events` defaults to `horizons=None` =
   searches all horizons. So horizon only affects human-readable grouping and the
   pickup packet's short-term/summary surfacing, not what is retrievable. A
   sensible default mapping is enough.

6. **Single write path = the WAL.** All write-back goes through
   `append_memory_event` (→ `events.jsonl`) and nowhere else, so any future engine
   (LangGraph embeddings, Graphiti) is a projection of the owned log. Hard
   constraint.

7. **Worker-prompt change (D4) has a prerequisite and a test risk.** Real workers
   only emit `## Learned` etc. if `_worker_prompt` asks them to (codex via
   `--output-last-message`, claude via `-p` stdout — both are the final message).
   So D4 is required for M1 to produce anything. It's an additive change, but
   `tests/test_workers.py` asserts prompt contents — re-check those assertions.

8. **Edge cases & safety.** `human` adapter returns `needs_review` (not
   `completed`) → no write-back; accept this gap, note it. Write-back errors must
   never flip a completed job to failed — capture any error in a
   `memory_writeback` field on the job status (D6).

**One-line for the planner:** M1's difficulty is not "write-back" itself — it's
(a) parser tolerance, (b) dedup, (c) clearly documenting "same-run doesn't
propagate." Concentrate tests on the pure parser; keep one WAL write path; don't
over-design the horizon mapping.
