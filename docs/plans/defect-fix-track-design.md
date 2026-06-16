# Design — Human-found bug → GoodTeam workflow (defect fix track)

> Stage: **architect / planner**. Captures the design for letting a human tester
> file a defect that enters the GoodTeam workflow as a first-class, audited,
> quality-gated **fix track** — without writing a full PRD. Founder decisions are
> recorded in §2; this doc tees up the PRD + per-track handoffs.

## 1. The gap (grounded investigation)

GoodTeam is positioned as *the local-first control layer for AI software
delivery*. A human-found bug is therefore a **primary input** to that control
layer — but today there is no first-class intake for it. The only existing
entry points (verified read-only against `src/goodteam_os/` on `main`):

| Entry point | Shape | Why it fails for a tester-found bug |
| --- | --- | --- |
| `start_workflow` + `delivery_mode` (`projects.py:13` catalog) | full PRD→RFC→…→release | Too heavy; the *what* is already known, no PRD needed |
| `changes_requested` loop (`graph.py:2007`/`2023`) | reviewer note at a live gate → auto-revise | Only works **inside an active run at a gate**; a released product has no live gate |
| raw `dispatch-track.sh` | direct implementer dispatch | No gates, no QA, no audit trail — bypasses the workflow |

`grep` for `defect|bug_report|issue_report` across `src/` and `frontend/` returns
**zero** product concept. So a tester who finds a bug must either spin up a whole
new workflow run (heavy) or bypass the workflow entirely (unaudited).

## 2. Founder decisions in force

- **Fix-track weight = A (lightweight).** A defect spawns a focused fix track that
  **skips PRD/RFC** and runs `fix-plan → implement → review → QA → land`. It keeps
  the quality gates + append-only audit, and **reuses the evidence/provenance
  contract** already built for the Review Workbench (a defect report *is* an
  evidence bundle).
- **Intake = both surfaces.** A human can file a defect from **(a)** a dashboard
  "Report Bug" button (auto-attaches current project/run/screen context) and
  **(b)** Telegram `/gt bug <description>` (remote, reuses the bot just shipped).

## 3. The defect artifact contract

A filed defect produces a `defect` artifact (markdown + structured sidecar),
analogous to how `prd`/`rfc` artifacts are produced today. Fields:

```
defect_id        : stable slug, e.g. defect-<project>-<short-uuid>
title            : one line
description       : free text (what's wrong)
evidence         : {
                     screenshot_refs[], repro_steps, affected_url/screen,
                     affected_artifact_ref (optional: which gate/artifact),
                     console_or_log_refs[]
                   }
severity          : "p1" | "p2" | "p3"
affected_area     : free string (e.g. "Approvals tab / recommendation card")
source            : { origin: "web"|"telegram", actor_ref, filed_at }
linked_run_id     : optional — the run whose output the bug is about
status            : "filed" | "in_fix_track" | "resolved" | "wont_fix"
```

The `evidence` + `source` blocks deliberately mirror the Review Workbench
`evidence_snapshot` / `decision_origin` / `actor_ref` shapes so the same audit
projection and FE rendering can be reused.

## 4. The fix track (workflow)

A new lightweight delivery mode / node sequence (skips `product_requirement` and
the PRD gate; the defect report stands in for the PRD):

```
filed defect
  → fix_plan        (architect-lite: root-cause hypothesis + minimal change plan;
                     a small RFC-shaped artifact, NOT a full RFC)
  → [fix_plan gate] (human approves the plan, or requests changes)
  → implement       (existing implementer dispatch; lane = the affected files)
  → [review gate]   (code_review against the fix_plan + the original defect)
  → qa              (verification-only: prove the bug is fixed + no regression)
  → land            (human + CI, existing land-pr provenance airdrop)
  → resolve defect  (mark defect status=resolved, link PR/run in provenance)
```

Gate notifications reuse the TG notify-gate set (`web.py:_tg_notify_gate_set`);
`fix_plan` should be in the default notify set so the human is pinged to approve
the plan. The `/gt approve|reject|changes|view` commands already work against any
gate, so they apply to the fix-track gates with zero new bot code.

## 5. Intake mechanics

### 5a. Dashboard "Report Bug"
- A button in the project dashboard (candidate home: the project header or a
  persistent affordance, so it's reachable from any tab while testing).
- Opens a small modal: title, description, severity, optional screenshot/upload,
  optional "this is about artifact X". Auto-fills `project_key`, `linked_run_id`,
  and current screen/tab into `source`/`evidence`.
- POSTs to a new `POST /api/defect` (same-origin mutating-route guard, like the
  other POST routes).

### 5b. Telegram `/gt bug <description>`
- Extend `_parse_telegram_command` valid set with `"bug"` (the command-arg
  extraction helper `_extract_command_arg` already exists from the `/gt view`
  work).
- `/gt bug <text>` files a defect with `source.origin="telegram"`,
  `actor_ref="telegram:user:<id>"`, severity defaulting to `p2` (overridable with
  a leading `[p1]`/`[p3]` tag), `linked_run_id` = the project's current run.
- Bot replies with the `defect_id` and whether a fix track was started.

### 5c. Start policy
Decision still open (§7): does filing a defect **auto-start** the fix track, or
land it as `status=filed` for a human to triage/start? Default proposal:
**file → triage → start** (a defect lands filed; starting the fix track is an
explicit action, web button or `/gt fix <defect_id>`), so a noisy reporter can't
spawn unbounded runs.

## 6. Track decomposition (for dispatch)

- **Track BE-1 (codex):** defect artifact contract + store + `POST /api/defect` +
  defect listing projection in `/api/state`. Lane: `web.py`, a new
  `defects.py`, tests.
- **Track BE-2 (codex):** fix-track delivery mode + workflow nodes (`fix_plan`,
  reuse implement/review/qa/land runners) + defect↔run linkage + resolve-on-land.
  Lane: `graph.py`, `projects.py`, `workflow_table`, tests.
- **Track BOT (codex):** `/gt bug` + `/gt fix <id>` + `/gt defects`. Lane:
  `telegram.py`, tests. Small — reuses existing arg-extraction + decision plumbing.
- **Track FE (agy):** "Report Bug" modal + a Defects panel/tab that lists filed
  defects, their status, and links into the fix-track run. Lane: a new
  `DefectsTab.jsx` + one wiring line in the tab router, plus the modal. Reuses the
  evidence/recommendation rendering patterns from `ApprovalsTab.jsx`.

## 7. Open questions (for the PRD)

1. **Auto-start vs triage-then-start** (§5c) — proposed default: triage-then-start.
2. **Where does the fix track run live** — a sub-run linked to the project's run
   history, or its own top-level run with `linked_run_id` back-reference? (Affects
   StateStore layout and the dashboard's run picker.)
3. **Severity → notify routing** — should `p1` defects always ping Telegram on
   filing (not just at the fix_plan gate)?
4. **`fix_plan` artifact depth** — how light is "architect-lite"? Proposed: a
   single markdown with root-cause hypothesis, the minimal change set (files +
   approach), and a verification plan; no data-model/API-contract ceremony unless
   the fix touches a contract.
5. **Resolution evidence** — require the QA gate to cite the original
   `defect.evidence.repro_steps` and show it now passes, closing the loop.

## 8. Why this fits the moats

- **Multi-agent graph moat:** the fix track is the same gated, role-routed graph,
  just entered at a different node — it strengthens the "every change goes through
  the same audited control plane" story instead of carving an exception.
- **Compounding memory moat:** filed defects + their fix tracks become first-class
  memory (what broke, where, how it was fixed), feeding the failure-pattern library
  (`failure_patterns.py`) and future triage. A bug is no longer a Slack message
  that evaporates — it's owned, local, and portable.
