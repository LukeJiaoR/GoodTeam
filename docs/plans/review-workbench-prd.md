# PRD + Architecture — Review Workbench, Telegram Approval, and Approval Audit

> Stage: **product_requirement + architect**. Review this before implementation
> prompts are issued. This PRD applies the product strategy in
> `docs/product-core-adapter-strategy.md`: GoodTeam owns the engineering-org
> protocol; mature external tools provide evidence through adapters.

## 1. Intent

GoodTeam's most important user moment is the approval gate. The human operator
must be able to answer:

- What am I approving?
- What evidence exists?
- What is missing or risky?
- Who reviewed this, from which client, and with what notes?
- If I request changes, will GoodTeam return to the right node with the right
  context?

Today GoodTeam has the pieces: web approvals, Telegram approval callbacks,
step-by-step Telegram review sessions, artifacts, worker jobs, debate messages,
memory, server-action policy, and provenance records. The product gap is that
the approval experience is not yet a unified review workbench, and the approval
audit trail does not clearly distinguish Web vs Telegram vs CLI/API decisions.

This PRD makes approval a first-class product surface across Web and Telegram.

## 2. Product Position

Dashboard and Telegram are two clients for the same GoodTeam approval protocol:

- **Dashboard** is the deep review workbench: full artifacts, diffs, long
  evidence, provenance, memory, debate, and diagnostics.
- **Telegram** is the complete mobile approval client: remote supervision,
  compact evidence, step-by-step review, request-change notes, and final
  approve/request changes/reject.

Neither client is the source of truth. The source of truth remains GoodTeam's
local ledger: state, action log, artifacts, worker jobs, messages, memory, and
provenance.

## 3. Users And Value

- **Founder/operator**: can keep GoodTeam moving from a phone while away from the
  workstation, without losing auditability or safety.
- **Design partner**: can understand why a gate is safe to approve without
  reading raw files or terminal logs.
- **Reviewer/auditor**: can later reconstruct who approved a gate, from which
  client, after which review path, and with which evidence snapshot.

## 4. Current Reality

Already present:

- Web approval path through `decide_approval`.
- Telegram quick approval and v1 interactive review callbacks.
- Telegram review session files under `state/tg-reviews/`.
- Sensitive-data masking for Telegram text.
- Stale legacy Telegram callback protections: active run, matching run id,
  pending gate, and actionable `await_<gate>_approval` node.
- Web-to-Telegram sync when a web decision closes an active Telegram review.
- Action-log rows for workflow events.

Gaps:

- Approval actions currently collapse to a generic human actor in the main
  decision path.
- Approval origin is not explicit enough: Web, Telegram, CLI, API, system.
- Telegram item-level review state is session-local and not promoted into a
  durable approval audit record.
- The evidence visible at decision time is not captured as a stable snapshot.
- Dashboard does not yet present approvals as a single "why approve / why block"
  workbench.
- Telegram is not yet positioned as a remote supervision client for run status,
  worker status, blockers, and evidence.

## 5. User-Facing Acceptance

### Web Review Workbench

For any actionable gate, the dashboard shows a workbench with:

- gate summary: project, run, gate, current node, artifact under review;
- decision recommendation: approve / request changes / reject with reasons when
  available;
- evidence matrix: artifacts, worker jobs, tests, CI, screenshots, deployment
  checks, blockers, debate, memory citations, provenance;
- missing evidence callouts;
- request-changes note box that drives the same revision loop as the existing
  approval flow;
- approval audit panel showing previous gate decisions, origin, actor ref,
  review mode, evidence snapshot, and notes.

### Telegram Complete Approval

From Telegram, a user can:

- receive an actionable gate overview card;
- inspect compact evidence and blockers;
- step through review items;
- mark items ok or flagged;
- add concrete request-change notes;
- approve, request changes, or reject;
- see the final state update;
- later have the same decision visible in the dashboard audit panel.

Happy path must not require opening the dashboard. The dashboard remains
available for deep inspection.

### Remote Supervision

Telegram supports read-only status for:

- active projects and current run stage;
- pending approvals;
- active worker jobs and elapsed time;
- latest blockers or failed jobs;
- CI / smoke / QA evidence status;
- recent dispatch, pre-review, and land events;
- links back to the dashboard or artifact paths for full review.

Mutating Telegram actions remain narrow: approval decisions and explicitly
guarded GoodTeam server actions only. Telegram must never become arbitrary remote
shell.

## 6. Approval Audit Contract

Every approval decision should append a durable audit event with these fields:

```json
{
  "action": "approval_decided",
  "project_key": "DEMO",
  "run_id": "run-123",
  "gate": "prd",
  "decision": "approved",
  "decision_origin": "telegram",
  "actor_ref": "telegram:user:111",
  "review_mode": "step_by_step",
  "review_session_id": "tg:sesabc",
  "client_context": {
    "telegram_chat_id": "12345",
    "telegram_message_id": "9999",
    "telegram_user_id": "111"
  },
  "evidence_snapshot": {
    "artifact_refs": ["prd"],
    "worker_job_refs": ["job-abc"],
    "ci_status": "unknown",
    "smoke_status": "unknown",
    "qa_status": "unknown",
    "screenshot_refs": [],
    "debate_refs": [],
    "blocker_count": 0,
    "provenance_refs": []
  },
  "review_items": [
    {
      "id": "acceptance",
      "title": "Acceptance",
      "state": "ok",
      "note": ""
    }
  ],
  "note": "",
  "created_at": "2026-06-10T00:00:00Z"
}
```

Field rules:

- `decision_origin`: one of `web`, `telegram`, `cli`, `api`, `system`.
- `actor_ref`: non-secret operator reference. Telegram user IDs are acceptable;
  bot tokens are never stored.
- `review_mode`: `quick`, `step_by_step`, `request_changes_with_notes`,
  `reject_confirmed`, or `system`.
- `client_context`: client-specific identifiers only, never credentials.
- `evidence_snapshot`: a stable summary of what was known at decision time.
  Later evidence changes must not rewrite the historical decision.
- `review_items`: item-level review states after masking sensitive data.

The existing action log can carry this as enriched `approval_decided` metadata,
or a dedicated approval-audit projection can be derived from it. The ledger
should stay append-only.

## 7. Evidence Snapshot

The first implementation does not need perfect evidence coverage. It should
capture what already exists locally:

- artifact refs from `state.artifacts` and `approval_artifact_map`;
- current blockers and blocker count;
- debate refs when the approval action carries a debate/message reference;
- worker job ids related to the current run when available;
- smoke/QA/CI fields as `unknown` unless already available from existing
  endpoints or Dev Loop data;
- provenance refs when a dispatch/pre-review/land record exists.

The snapshot should be best-effort and failure-safe. Missing evidence should be
represented as `unknown` or an empty list, not as a server error.

## 8. Telegram Remote Supervision Contract

Telegram status commands/buttons should return compact, masked summaries:

- `/gt status`: active project, current run, current node, pending gate count,
  active worker count, blocker count.
- `/gt approvals`: pending approval cards with quick review buttons.
- `/gt workers`: active/recent worker jobs, elapsed time, status, last error
  excerpt.
- `/gt evidence`: latest smoke/QA/CI/deployment evidence status.

Exact command syntax can be adjusted, but the product requirement is that a
remote operator can understand whether GoodTeam is progressing, waiting for a
decision, blocked, or unsafe to continue.

## 9. Safety And Privacy

- Telegram commands require the existing allowed-user gate.
- Telegram decisions must validate project, run, gate, pending approval state,
  and actionable node before mutating state.
- Telegram must mask paths, IPs, SSH targets, deployment dirs, tokens, and other
  secret-like strings before sending.
- Telegram cannot run arbitrary shell commands.
- Telegram cannot bypass server-action nonce, typed confirmation, or report-only
  boundaries.
- Web and Telegram decisions must be idempotent: stale buttons should be ignored
  and auditable as ignored/stale where useful.

## 10. Architecture Decisions

### Decision 1: Enrich The Approval Ledger First

Implement the audit contract before large UI changes. Once the ledger has
`decision_origin`, `actor_ref`, `review_mode`, `evidence_snapshot`, and
`review_items`, both Web and Telegram can render from the same truth.

### Decision 2: Telegram Is A Client, Not A Separate Workflow

Telegram should call the same approval decision path as Web. It may pass richer
client metadata, but it must not fork approval semantics.

### Decision 3: Evidence Is Snapshot, Not Live Reference Only

The approval audit must record what the reviewer saw at decision time. Links to
live artifacts are useful, but not sufficient for audit.

### Decision 4: Remote Supervision Starts Read-Only

Implement Telegram run/worker/evidence status before adding any new remote
mutating actions beyond approvals.

## 11. Tracks

### A. Backend Approval Audit Foundation

Files likely touched:

- `src/goodteam_os/web.py`
- `src/goodteam_os/telegram.py`
- `tests/test_web.py`
- `tests/test_telegram.py`
- `tests/test_telegram_review.py`

Scope:

- Extend `decide_approval` to accept optional audit metadata:
  `decision_origin`, `actor_ref`, `review_mode`, `review_session_id`,
  `client_context`, `review_items`, and `note`.
- Build a failure-safe `build_approval_evidence_snapshot(...)` helper.
- Write enriched `approval_decided` action-log rows.
- Preserve existing API behavior for callers that do not pass metadata.
- Add regression tests for Web/default, Telegram quick, Telegram step-by-step,
  stale callback ignored, and web-to-telegram sync.

### B. Telegram Complete Approval + Remote Supervision

Files likely touched:

- `src/goodteam_os/telegram.py`
- `tests/test_telegram.py`
- `tests/test_telegram_review.py`

Scope:

- Pass Telegram metadata into `decide_approval`.
- Persist item-level review summaries into the approval audit event.
- Add compact status handling for active run, pending approvals, workers,
  blockers, and evidence.
- Keep all text masked and all mutating callbacks guarded.

### C. Web Review Workbench

Files likely touched:

- `frontend/src/components/tabs/ApprovalsTab.jsx`
- related frontend components or utilities if extraction is needed;
- `tests/test_web.py` frontend contract tests.

Scope:

- Show evidence matrix and missing-evidence callouts.
- Show approval audit history with origin badges: Web, Telegram, CLI/API,
  system.
- Show actor ref and review mode.
- Show evidence snapshot and item-level review notes.
- Preserve current approve/request changes/reject behavior.

### D. Product Narrative Update

Files likely touched:

- `README.md`
- `pyproject.toml`
- `docs/product-vision.md`
- `docs/product-core-adapter-strategy.md`

Scope:

- Remove stale LangGraph-centered wording.
- Position GoodTeam as local-first AI software delivery control layer.
- Link Review Workbench and Telegram remote supervision into the core product
  narrative.

## 12. Sequencing

Recommended order:

1. Backend approval audit foundation.
2. Telegram metadata + remote read-only status.
3. Web review workbench audit display.
4. Product narrative update once the audit contract is stable.

Do not start with UI polish. The audit contract is the product backbone.

## 13. Open Questions

Resolved (founder, 2026-06-10):

- **`actor_ref` default** — RESOLVED: environment-configurable. Web reads an
  operator reference from env (`GOODTEAM_OPERATOR_REF`) and falls back to
  `web:local` when unset. No new auth system in this slice.
- **Approval audit storage** — RESOLVED: store only in the append-only
  `action-log.jsonl` as an enriched `approval_decided` row. No separate
  `approvals/audit.jsonl` projection; the FE already reads action rows through
  `build_action_items`, so the action log stays the single source of truth.

Still open (deferred to Track B, not blocking the backend foundation):

- Which Telegram command syntax is best: slash commands, inline buttons from a
  pinned status card, or both?
- Should request-changes notes from multiple flagged Telegram items be combined
  into one gate note or preserved as structured per-item notes plus a summary?

## 14. Non-Goals

- No arbitrary remote command execution from Telegram.
- No auto-approval based on green checks.
- No full project-management replacement.
- No new external auth system in this slice.
- No broad graph/parallel execution changes.
- No i18n expansion beyond copy touched by this work.

## 15. Success Criteria

- A gate decision records whether it came from Web or Telegram.
- Telegram approvals include actor, chat/message/session identifiers, review
  mode, review items, and notes when present.
- Web decisions include origin and actor defaults.
- A dashboard reviewer can inspect the approval audit trail.
- A Telegram user can supervise active progress and complete an approval remotely
  without losing auditability.
- Existing approvals remain backward-compatible for smoke tests and API callers.

