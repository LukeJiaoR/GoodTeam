# Phase 1 handoff — the `fix` load profile

> Stage: **planner**. Implements Phase 1 of `minimal-chain-pluggable-load-design.md`:
> add a lightweight **`fix`** load profile (`fix_plan → implement → review → qa`)
> running on the Phase 0 `LoadProfile` seam. **Greenfield must stay byte-identical**
> — implement `fix` as a SEPARATE code path; do not touch the greenfield loop,
> nodes, gates, or runners. Acceptance gate = the full existing suite stays green
> + a new fix end-to-end test.

## Engine facts (verified on `main`, cite before editing)

- A run advances via `decide_approval` (`web.py:2607`): it computes
  `approved_next = approved_next_for_gate(approval)` (L2607), sets the decision,
  `state.next_node = gate_next_node(state, gate=approval, approved_next=...)`
  (L2636), then calls `advance_approved_workflow(...)` (L2685).
- `gate_next_node` (`nodes/review_gate.py`): approved → `approved_next`;
  changes_requested → `revise_{gate}`; rejected → `rejected`; pending →
  `await_{gate}_approval`.
- `advance_approved_workflow` → `_advance_approved_workflow_legacy` (`graph.py:965`):
  resolves the profile (Phase 0), then loops: `node = workflow_node(next_node)` →
  check `node.depends_on` gates → check `node.run_guard_gate` → run
  `runners[node.runner_fn]` → repeat until no progress. Today it raises on any
  non-greenfield profile (L979-980).
- A runner's shape (e.g. `run_until_next_gate` `graph.py:1956`): generate artifact
  content (template and/or `generate_artifact_via_worker`), write via
  `ArtifactStore`, set `state.artifacts[key]`, set `current_node` +
  `next_node = gate_next_node(state, gate=X, approved_next=Y)`, return state.
- Profile seam (Phase 0, `workflow_table.py`): `LoadProfile(name, nodes,
  gate_transitions)` with `node_for / gate_transition / approved_next_for_gate /
  execution_plan`; `GREENFIELD_PROFILE`; `_LOAD_PROFILES` registry;
  `resolve_profile(name)`. `AgileDevState.load_profile` defaults `"greenfield"`.

## The `fix` flow (what to build)

```
start (load_profile="fix")
  → run_fix_until_next_gate : write a fix_plan artifact → await fix_plan approval
  → [gate fix_plan]  approved_next = "fix_implement"
  → fix_implement   : dispatch implementer (reuse run_implementation_workers) →
                      write implementation_handoff artifact → await code_review
  → [gate code_review]  approved_next = "fix_qa"
  → fix_qa          : write qa_report artifact → await qa approval
  → [gate qa]  approved_next = "fix_close"
  → fix_close       : next_node = "" (run complete; optional retro memory event)
```

Gates introduced for `fix`: `fix_plan`, `code_review`, `qa`. Reuse the existing
gate machinery and `evidence_snapshot`/audit/memory — fix decisions write the same
`approval_decided` rows as greenfield.

## Changes

### 1. workflow_table.py — register the fix profile (data only)
- `_FIX_GATE_TRANSITIONS`: `GateTransition`s for `fix_plan`
  (on_approved_next="fix_implement"), `code_review` (→"fix_qa"), `qa`
  (→"fix_close"); each `on_changes_requested="revise_{gate}"`,
  `on_rejected="rejected"`.
- `_FIX_WORKFLOW_NODES`: `WorkflowNode`s —
  `fix_implement`(run_guard_gate="fix_plan", runner_fn="run_fix_implementation",
  depends_on=("fix_plan",)), `fix_qa`(guard="code_review",
  runner_fn="run_fix_qa", depends_on=("code_review",)),
  `fix_close`(guard="qa", runner_fn="run_fix_close", depends_on=("qa",)).
- `FIX_PROFILE = LoadProfile(name="fix", nodes=<frozen fix nodes>,
  gate_transitions=<frozen fix gates>)`. Unlike greenfield, the fix profile binds
  its OWN nodes/gates (captured), not the live greenfield globals.
- Register `_LOAD_PROFILES["fix"] = FIX_PROFILE`.
- Do NOT modify `_WORKFLOW_NODES` / `_GATE_TRANSITIONS` / `GREENFIELD_PROFILE` or
  any greenfield module function.

### 2. graph.py — fix runners + a profile-aware advance path (greenfield untouched)
- Add runners (mirror the `run_until_next_gate` shape, use `ArtifactStore` +
  `gate_next_node`):
  - `run_fix_until_next_gate(state, workspace, project_config)`: produce a
    `fix_plan` artifact (root-cause hypothesis + minimal change plan + verification
    plan). Generate via `generate_artifact_via_worker(role="architect",
    artifact_key="fix_plan", task_text=<fix-plan task built from state.idea /
    defect context>)` with a markdown template fallback (so the `fake` worker and
    no-worker paths both work). Set `current_node="fix_plan_review_gate"`,
    `next_node=gate_next_node(state, gate="fix_plan", approved_next="fix_implement")`.
  - `run_fix_implementation(...)`: reuse `run_implementation_workers(...)` (same
    implementer dispatch the planner uses) to perform the fix; write an
    `implementation_handoff` artifact; stop at `code_review`
    (`next_node=gate_next_node(state, gate="code_review", approved_next="fix_qa")`).
  - `run_fix_qa(...)`: write a `qa_report` artifact (reuse the greenfield QA
    rendering helper if one exists; else a template). Stop at `qa`
    (approved_next="fix_close").
  - `run_fix_close(...)`: optional retro memory event; `next_node=""`.
- Make the advance loop profile-aware WITHOUT changing the greenfield path:
  ```python
  profile = resolve_profile(getattr(state, "load_profile", "") or "greenfield")
  if profile.name == "greenfield":
      <EXISTING loop, verbatim — do not touch>
  else:
      <new loop using profile.node_for / profile.approved_next_for_gate and a
       runners map that includes the fix runners>
  ```
  Keep the greenfield branch character-for-character as it is now (so the
  monkeypatch-based greenfield tests stay valid).

### 3. web.py — make decide_approval's next-node resolution profile-aware
- At `decide_approval` (~L2607): replace the bare
  `approved_next = approved_next_for_gate(approval)` with a profile-resolved lookup:
  `profile = resolve_profile(getattr(state, "load_profile", "") or "greenfield");
  approved_next = profile.approved_next_for_gate(approval)`. For greenfield this is
  identical to today (GREENFIELD_PROFILE delegates to the module function). Verify
  no other greenfield-specific gate assumptions in decide_approval break for fix
  gates (`fix_plan`/`code_review`/`qa`); if `approval_artifact_map()` or
  `current_approval_gate` need the fix gates, extend them additively.
- `notify_current_approval_gate` already filters by `_tg_notify_gate_set()` — add
  `fix_plan`/`code_review`/`qa` to the default set if they should ping Telegram
  (founder default: yes for `fix_plan`, optional for the rest — make them notify by
  default, overridable via the env var).

### 4. Profile selection at start
- Add a `load_profile: str = "greenfield"` parameter to the start path
  (`start_workflow` in graph.py and the `/api/start` handler / `start` CLI as
  needed). When `load_profile == "fix"`: set `state.load_profile = "fix"` and start
  via `run_fix_until_next_gate` instead of `run_until_next_gate`. Default greenfield
  path is unchanged.
- Keep this minimal: a defaulted param threaded through. The dashboard "Report Bug"
  / `/gt bug` intake (defect-fix-track) will set `load_profile="fix"` later — out of
  scope for Phase 1; just make the profile runnable when selected.

## TDD / acceptance (the gate)
- **Greenfield unchanged:** the full existing suite passes UNALTERED —
  `PYTHONPATH=src .venv/bin/python -m pytest tests -q` shows zero existing
  failures. Do not edit existing test assertions.
- **New fix end-to-end test** (new file, e.g. `tests/test_fix_profile.py`): start a
  run with `load_profile="fix"` and a `fake` worker; assert it stops at
  `await_fix_plan_approval` with a `fix_plan` artifact; approve fix_plan → stops at
  `await_code_review_approval` with an implementation artifact; approve code_review
  → stops at `await_qa_approval`; approve qa → run completes (`next_node==""`).
  Assert each decision writes an `approval_decided` audit row with the right gate.
- **Profile isolation:** assert `resolve_profile("fix").name == "fix"` and that the
  fix nodes/gates are disjoint from greenfield; assert a greenfield run never sees
  fix nodes.
- `ruff check` clean; `git diff --check` clean; if anything is path/integration
  shaped, add a live smoke.

## Out of scope for Phase 1 (do NOT build here)
- The defect intake (defect artifact, `POST /api/defect`, `/gt bug`, FE "Report
  Bug") — that's the defect-fix-track build that consumes this profile next.
- Node declaration generalization / node-type registry — that's Phase 2.
- Parallel level execution.
