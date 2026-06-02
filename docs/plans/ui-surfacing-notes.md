# UI surfacing notes — does the UI cover the new collaboration features?

> Stage: research + planning (not a PRD yet). Captures a read-only UI survey (agy backend,
> 2026-06-02) + Claude's reviewer synthesis. Purpose: tee up the next UI increment (M1c+)
> after the backend collaboration features (message bus #35, debate #37) landed without UI.
> Line citations are against `frontend/src/App.jsx` / `src/goodteam_os/*` on main at the
> time of writing — re-verify before editing.

---

## Claude synthesis (the judgement layer)

**Verdict: the UI is well behind the backend.** The dashboard is a clean 10-tab layout, but
six backend features are un/under-surfaced (see agy §3). I spot-checked agy's key claims
against main and they hold:
- zero componentization (`frontend/src/` = only `App.jsx`/`App.css`/`index.css`/`main.jsx`) ✓
- the **`debate_ref` end-to-end gap is real**: it's wired into `decide_approval` (web.py:560-601)
  but the `/api/approve` route (web.py:~5065) doesn't pass it, and `handleApproveDecision`
  (App.jsx:602) doesn't send it ✓
- approval decision controls at App.jsx:L2516-2544 ✓

**Where I dissent from agy:** agy calls the 2796-line single-file `App.jsx` a "severe
maintainability risk" and recommends a full hooks+components extraction. Directionally right,
but **do NOT big-bang it.** The frontend has **no test coverage** (only `npm build` + the
Playwright smoke) — refactoring 2796 lines at once is exactly the large-diff, no-safety-net
change that hides regressions. Componentize **incrementally, as each panel is touched.**

**Recommended next UI increment (M1c, reframed)** — one coherent slice that also seeds the
component pattern, lowest blast radius:
1. Wire **`debate_ref` end-to-end** (`/api/approve` route + `handleApproveDecision`) so the
   decision↔debate link actually works (small, closes a real gap).
2. Extract **`ApprovalsPanel.jsx`** (App.jsx:L2299-2557) into `frontend/src/components/` —
   establishes the seam without touching the other 9 tabs.
3. Render the **debate thread** inside it (fetch `/api/messages?gate=`) at ~L2498, above the
   content preview (agy's insertion point — verified sensible).

**Sequencing caveat:** debate has no *content* until the real-debate-wiring lands (the
`default_debate_dispatch` placeholder in `debate.py` returns `[]`). So **real-wiring should
come before/with M1c**, else the panel renders empty. Order: real-debate-wiring → M1c.

**Other surfaced gaps to backlog** (not M1c): message-bus feed/audit view; memory
`supersedes`/citations rendering (+ `/api/memory/event` doesn't return `supersedes`); richer
agent-readiness panel.

---

## agy read-only survey (verbatim substance, paths normalized to App.jsx:L…)

### 1. UI inventory of `App.jsx`
Multi-Project Control Room when no project selected; a 10-tab Single-Project Dashboard when a
project key is in the URL.

| Panel / Section | Line Range | Functionality | `/api/*` called |
| :--- | :--- | :--- | :--- |
| Multi-Project Control Room | L766-1347 | project list, deletion, start-workflow form, advanced-config drawer | GET /api/state (L274); POST /api/project-delete (L460); POST /api/project-config (L512); POST /api/start (L570) |
| Dashboard Sidebar & Layout | L1348-1561 | tab nav (10 tabs), theme toggle, terminate | POST /api/terminate (L440); GET /api/state (auto-refresh L274) |
| Overview Tab | L1563-1744 | node state, goal, logs, retry-blocked button | POST /api/retry (L483) |
| Roles Tab | L1746-1800 | per-role (ui/implementation/qa) stats + observations | none (data.role_observations) |
| Artifacts Tab | L1802-1840 | produced files + markdown editor drawer | GET/POST /api/artifact (L656/L678) |
| Actions Tab | L1842-1900 | chronological action/event table | none (data.state.action_log) |
| Agile Board Tab | L1902-1989 | Kanban: Todo/In Progress/Review/Done | none (data.agile_board) |
| Smoke Runs Tab | L1991-2057 | QA smoke reports, commands, output | none (data.smoke_run_items) |
| Workers Tab | L2059-2297 | CLI backend routing (global + overrides), discovered agents, worker jobs | GET /api/agents (L737); POST /api/project-config (L712) |
| Approvals Tab | L2299-2557 | gate status list, pipeline progress, artifact preview, decision controls | POST /api/approve (L605); POST /api/generate-prd (L630); GET/POST /api/artifact (L656/L678) |
| Memory Tab | L2559-2713 | long/mid/short memory + semantic retrieved facts + scores | GET /api/memory (L380); GET /api/memory/event (L406) |
| System Tab | L2716-2794 | raw JSON state inspector | none (local data) |

### 2. Approval panel anatomy
- Inline "Approve" for actionable reviews in the left list: L2405-2418.
- Detail-panel footer controls (when `selectedReview.is_actionable`): L2516-2544 — Reject
  (L2518-2525), Request Changes (L2526-2533), Approve Gate (L2534-2542); each calls
  `handleApproveDecision(selectedReview.approval, <decision>)`.
- Detail card: L2477-2545; "Assigned Review Roles" L2488-2497, "Content Preview" L2500-2513.
- **Cleanest debate insertion point: ~L2498**, between Assigned Roles and Content Preview —
  reviewer sees the debate before reading the doc / clicking the footer actions.

### 3. Gap list (backend done, UI missing)
1. **Message bus `/api/messages`** — never called in App.jsx; no feed/audit view.
2. **Debate / threaded visualizer** — `run_gate_debate` (debate.py:13-69) emits ask/reject/
   assert/propose linked via `in_reply_to`; UI renders none of it.
3. **`debate_ref` linkage** — supported in `decide_approval` (web.py:560-567) but the
   `/api/approve` route (web.py:~5064-5070) doesn't extract/pass it, and
   `handleApproveDecision` (App.jsx:602-623) doesn't send it.
4. **Memory citations & supersede** — events support `supersedes` (memory.py:207-240) but
   `build_memory_event_detail` (web.py:~1485-1501) doesn't output it; the memory panel shows
   raw text, no cite/supersede links.
5. **Agent readiness detail** — only a ✓/⚠ badge (App.jsx:~L1325); no detail on what makes an
   agent ready / which models it registers.

### 4. Scale assessment
- 2796 lines / ~170KB; 22 state variables, 14 API wrappers/handlers, 10 tabs + control room.
- **Zero** existing componentization (no hooks/components/utils under `frontend/src/`).
- agy's extraction recommendation (Claude note: do this INCREMENTALLY, no big-bang — no FE
  test net): custom hooks (`useProjectState`, `useMemory`, `useWorkers`); tab components
  (`ApprovalsPanel` L2299-2557, `MemoryPanel` L2559-2713, `WorkersPanel` L2059-2297,
  `OverviewPanel` L1563-1744, control room L765-1347); shared UI (`FilePath` L93-118,
  `Spinner` L121-127) under `components/ui/`.

### 5. Concrete debate UI plan (M1c+)
**Data**: on review-card select, `GET /api/messages?project=&run=&gate=` → `payload.messages`.

**Wireframe** (debate block above the content preview, ~L2498):
```
+------------------------------------------------------------------+
|  Assigned Review Roles: [qa] [architect]                         |
+------------------------------------------------------------------+
|  GATE DEBATE & CONSENSUS (2 Objections)                          |
|  [qa] Objection (reject)  "RFC does not name the rollback path." |
|   └─► [architect] (propose) "Add rollback wording before approve"|
|  [qa] Question (ask)      "What happens on partial failure?"     |
|   └─► [architect] (assert) "Partial failure keeps gate pending." |
+------------------------------------------------------------------+
|  Content Preview: rfc.md … markdown …                            |
+------------------------------------------------------------------+
|  [ Reject ]        [ Request Changes ]        [ Approve Gate ]    |
+------------------------------------------------------------------+
```
**Styling (Tailwind v4)**: dashed `rounded-2xl border-2` container; challenger cards
`border-l-4` amber(`ask`)/red(`reject`); owner rebuttals indented `ml-8 border-l-4`
blue(`assert`)/emerald(`propose`). On decision, forward the resolving message id as
`debate_ref` (`messages/<run>.jsonl#<id>`) in the `/api/approve` body.

### 6. agy's recommended increment (3 steps)
1. **Route + handler**: parse `debate_ref` in the `/api/approve` `do_POST` (web.py:~5064-5070)
   → `decide_approval`; `handleApproveDecision` (App.jsx:602-623) accepts/sends an optional
   `debateRef`.
2. **Componentize**: extract Approvals tab (L2299-2557) → `frontend/src/components/ApprovalsPanel.jsx`.
3. **Debate widget**: fetch `/api/messages?gate=` on card-select; render the `in_reply_to`
   thread per the wireframe; extract the resolving message id as `debate_ref` on submit.

(Claude note: agy's step order matches my recommendation; just gate the whole thing behind the
real-debate-wiring so there's content to render.)
