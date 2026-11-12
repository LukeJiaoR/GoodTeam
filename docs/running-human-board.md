# Running Manager judgment to the existing Human board

Outcome: while an original batch is still running, Manager can capture a
necessary owner decision on the project's existing question board. The owner can
answer through the existing `/answer` path; that receipt enters the next ordinary
running observation. No new dispatcher, board, model call or task grant is added.

The former `allow_running_observation` contract was advice-only (plus separately
budgeted Worker follow-up). Old grants remain that way. A newly confirmed UI
observation choice explicitly discloses internal-board questions and captures
`allow_running_human_question: true`; there is no additional checkbox. The optional
marker requires observation permission, retains omission in older digests and
never grants external messaging, investigation, write access, retries or expense
increases. Existing Worker follow-up limits remain independent.

A fresh judgment may include a validated `human_question`. It is stored atomically
with that judgment in the original `human_questions`, with a distinct
`running_human_question_source.v1` packet binding the original observation,
continuation and grant digest. There is no fake discovery_pending or delivery.
The original question ID, board, `/answer`, answer receipt and authority checks
are reused. Stop, authority, batch, observation or Human-context changes prevent
late question publication. Same-question proposals retain the existing question.

Publishing our own question does not count as a new observation event. All actual
questions stay available in the next input, but self-generated pending questions
are excluded from the wake key. Human answers and actual Worker changes still
wake the original consumer. This avoids spending the Manager budget merely to
reread its own question.

A simultaneous, already-authorized Worker progress follow-up remains possible.
For its freshness check only, the exact same judgment's own unanswered question
is excluded before the full Human context is truncated. Other questions, answers,
or a resolution of that question still invalidate the old follow-up. Original
packet evidence is never rewritten. The new board question does not pause the
running batch or create work.

## Verification and scope

Base is the saved local backlog-Human-board candidate, itself based on the local
blocked-judgment/multiple-followup chain plus PR #726 frontend head. This is a
separate increment, not a patch against unmodified main.

Tests exercise actual local grants, session/board/question/answer storage,
budgeting and source binding. Model output and a running batch are fixtures;
ordinary response recovery projection is stubbed in that storage regression.
No real Provider, Pi, Worker or paid search is invoked. Actual runtime acceptance
and the whole-project loop remain #419 responsibilities. Nothing is pushed or
merged, and no production or security configuration is changed.

Final full backend: 457 passed, 32 failed, 5 errors, 20 skipped. All 37 failure/error IDs exactly match the prior candidate. Related focused suite: 19 passed before adding the final truncation regression; final full run includes that regression. UI: 7 passed; Ruff, ESLint and Vite build passed (existing chunk warning). Independent review found no remaining blocking issue and independently exercised original queue combinations and Context bounds.
