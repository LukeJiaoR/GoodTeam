# Review Feedback Handoff (#415)

## Goal And Boundary

Preserve review feedback from Request changes into the durable Intent Commit
draft and, after explicit confirmation, into the revision Worker request.
The user approved this slice, isolated real verification and a separate PR.
No automatic merge, production changes, dependencies or CI changes are included.

Worktree: `/private/tmp/goodteam-review-feedback-415`.
Branch: `codex/review-feedback`, based on merged PR #421 (`dd95cff`).
Concurrent UI polish in the saved checkout is out of scope.

## Minimum Contract

- Request changes carries the raw note and existing exact target binding.
- Opening a draft persists before navigation and does not call a model or decide
  an approval. Existing discussion/interpretation/confirmation gates remain.
- Blank input may open an empty draft. Nonblank feedback prefills the editor.
- Same-target/same-text replay is idempotent. Different existing feedback is a
  recoverable conflict, never an implicit overwrite or append.
- Failed/stale requests leave the Review input intact. Manager failure cannot
  erase feedback already accepted into the durable draft.
- Preserve original whitespace through confirmation and Worker handoff; an
  interpretation may summarize, but cannot replace the submitted instruction.
- Cover both Review and the Work review dialog where the same defect occurs.

## Execution

- [x] Trace the existing UI, API and durable store; isolate concurrent work.
- [x] Add failing regression tests and implement the bounded handoff.
- [x] Run focused tests, full backend, frontend tests/lint/build and diff review.
- [x] Verify isolated browser handoff/restart and a real revision Worker result.
- [x] Record evidence and complete the pre-PR handoff on `codex/review-feedback`.

This record is frozen at the verified pre-PR handoff. The GitHub PR for this
branch and comments on #415 carry the subsequent delivery and CI status.

## Verification

Tier 4: approval feedback, persistent intent, exact target binding, confirmation
and live revision dispatch. Focused tests cover blank/nonblank notes, raw text,
replay, existing drafts, stale artifacts, failed interpretation, restart and
confirmation without premature approval. Broader checks follow
`docs/testing-strategy.md`.

No independent subagent review is currently available under the repository's
allowed model list. Perform an explicit local diff/contract review and disclose
that limitation. Browser verification uses Ego Browser in a separate TaskSpace;
live calls and all smoke mutations stay in an isolated project/workspace.

## Findings During Verification

- The first browser handoff exposed a second trimming boundary in the Work
  projection. The durable note was intact. A failing integration regression
  reproduced it; the projection now preserves raw text. Restart restored that
  same draft without manually changing its saved data.
- Initial smoke setup used an artifact outside the canonical run directory.
  The server correctly rejected it. Before submitting any review feedback, the
  fixture was recreated under `workspace-live/artifacts/runs/run-feedback-415/`.
  This setup attempt is not product acceptance evidence.
- Current live scope is revision of GoodTeam's existing tracked review PRD,
  imported as a pending review in an isolated checkout. It is not an initial
  real code-generation run or a claim of full #411 release acceptance.

## Live Evidence

Isolated checkout and home: `/private/tmp/goodteam-feedback-smoke-415/`.
Workspace: `workspace-live`; server: `http://127.0.0.1:21930`.
No writes or restarts targeted the existing 21001/21910 services or saved checkout.

- Review -> Request changes -> Work retained the exact original note. After the
  projection fix, a server restart restored the same draft without state edits.
- One real `codex/gpt-5.5/high` Manager interpretation completed under `read_only`.
  Explicit confirmation dispatched one real Product Requirement revision Worker.
- Run `run-feedback-415`; intent `intent-d880ee469463494d9d0321e3f34ceb9f`.
  Manager job `job-6c2d8fb4-6f4e-4376-89b9-ff047a6989ed`; revision job
  `job-c40c1c65-18f1-4544-86ad-3d2986e47ad9`.
- Both Worker task files contain the exact note. The one approval-decided event
  retains that note and the original artifact version. Confirmation replay
  returned 200/idempotent with no additional jobs or decisions.
- Original PRD SHA-256 starts `b16bb78a`; revised artifact starts `0ccea5e0`.
  The revised PRD is openable in Review, includes the requested handoff section,
  and remains pending human review. No automatic approval or merge occurred.
- The generated PRD broadened the new handoff requirement to Telegram. Its
  handoff transport passed, but this output is not declared fully accepted.
  A Web-only scope correction was saved as a second unconfirmed draft. No third
  model call was made; this remains follow-up product-effect evidence for #411.
- Different feedback returned a visible conflict and left both the original
  draft and Review input intact. Another restart preserved the follow-up draft
  and the earlier committed intent.
- Desktop 1440x900 and mobile 390x844, English/Chinese. No horizontal overflow
  observed; long feedback uses the existing scrollable editor.
- Screenshots: `draft-desktop.png`, `revised-artifact-desktop.png`,
  `conflict-desktop.png`, `draft-mobile-en.png`, `draft-mobile-zh.png`,
  `restart-mobile-zh.png`. Structured checks: `live-evidence.json` and
  `check-evidence.mjs`, all under the isolated base directory.

The first browser wait for revision completion timed out while the Worker was
still running; completion was subsequently observed and checked. A later browser
script used an unsupported alert locator after the conflict had occurred; a DOM
inspection and screenshot verified the retained input and conflict. These script
errors are not reported as passing automation runs.

## Final Verification

Commands run from the isolated implementation worktree:

- `PYTHONPATH=src python -m pytest tests -q --tb=short`: **2136 passed in 463.80s**
  on the final backend code, with Telegram network disabled. Fourteen existing
  macOS multiprocessing/fork deprecation warnings remain.
- `PYTHONPATH=src python -m pytest tests/test_intent_feedback.py tests/test_intent_commit.py tests/test_team_workspace.py tests/test_web.py -q --tb=short`:
  **294 passed in 7.48s**.
- `ruff check src/goodteam_os/ tests/`: passed.
- `npm test --prefix frontend`: **268 Vitest + 6 Node tests passed**.
- `npm run lint --prefix frontend`, `npm run build --prefix frontend`: passed.
- `git diff --check`: passed.
- `node /private/tmp/goodteam-feedback-smoke-415/check-evidence.mjs`: passed
  after confirmation replay and restart. The worker count remains two and the
  committed decision count remains one.

Python/Ruff came from the existing isolated Python 3.12 environment at
`/private/tmp/goodteam-product-acceptance-rQeil6/venv/bin/`. npm used the unchanged
lockfile. Existing Node localStorage and large-bundle advisories remain.
Local review checked raw-text boundaries, compatible omitted-input behavior,
atomic seeding/conflicts, exact target validation and confirmation separation.
Independent review remains a PR recommendation, not a completed verification.
