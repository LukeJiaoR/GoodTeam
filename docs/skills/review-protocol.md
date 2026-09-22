# Code Review protocol preparation (S6 / #446)

Status: **non-activated S6 preparation, not complete S6 or real review acceptance**.
The user authorized proceeding to the next independent development step while
#440/#445 remain blocked. This branch starts from main after #462; it neither
includes nor merges #463. #446 remains Open. Do not turn passing CI into proof of
actual Codex loading, read-only isolation, report delivery or review quality.

## Captured protocol versus replaceable methods

New bounded Skill tasks capture `goodteam.code-review-protocol.v1` for
`role:code_review`. The existing binding contract reference remains unchanged;
the complete protocol value is part of the immutable task digest. This adds an
explicit protocol version rather than changing old captured bytes. Old task
replay uses its saved contract, and old Worker/reviewer retries use their saved
input files. Missing historical protocol metadata is not upgraded in place;
unknown versions or modified v1 contracts are rejected rather than guessed.

The thin contract retains the named task and exact candidate, original explicit
requirements/evidence, result protocol, advisory status, read-only boundary and
separate reviewer session. It does not contain quality/security/performance
checklists or the built-in reviewer profiles' focus and report templates.

Parallel Code Review producers use the captured contract to select their task
format. New-protocol tasks carry the artifact path and exact digest, but do not
include the profile label, focus list or report-section template. The profile
still identifies the reviewer and its configured runtime; it is not a hidden
source of methods. Missing artifact bytes/digest block new-protocol preparation.
Legacy profile jobs retain their original task format.

Explicit task/context/constraints and the stored Worker's user-configured
`additional_prompt` are retained, not blanket-filtered by keyword. A user can
explicitly request security checks or a particular section; those requirements
are not removed simply because an old default used similar words. Future UI
should make this additional instruction source visible; no new UI is claimed.

## Real preparation integration; execution remains closed

The Worker request capture adds `review_prompt_base.md` to the protected immutable
input bundle for the new protocol. It contains only the thin contract and exact
original task inputs, without the legacy Role prompt, primary-memory policy or
Learned/Caveats/Decisions/Open Questions write-back template. It is **a prompt
base, not a complete Runtime input**: #445 must still combine it with required
methods and authenticated resource access, enforce actual context budgets, and
record independent delivery evidence. No caller may send the base alone and
claim selected methods were delivered.

New direct Code Review and artifact-generation producers select `read_only` and
new-session policy. Parallel reviewers already use read-only/new jobs. The
Worker capture rejects writable policies or a reused builder session for this
protocol. These are configuration checks, not an OS sandbox proof. An actual
verified #440 adapter is still required. The legacy `_worker_prompt` now rejects
Skill jobs before appending its generic Role/memory wrapper; all existing lower
Runtime gates are retained. No configuration flag enables Skills in this patch.

Reviewer retries copy the original entire input bundle, including the prepared
base, and retain methods, task identity and read-only policy. Current Team or
protocol templates cannot replace a retry's saved inputs. Results remain blocked
before provider invocation under the existing S4/S5 gate.

## Optional first-party preset

`skills/builtin/review-core/` is a standard SKILL.md package with optional profile
reference content. `review_core_package()` returns parsed bytes and the explicit
`builtin:review-core` source identity. It does not install, bind or activate it.
The ordinary package parser/store handles it just like a user package; custom
names and formats are not special-cased. Users can select only their own package
without retaining this preset. Automatic default-preset resolution and its
selection UI are not introduced by this preparation step.

`NOTICE.md` attributes the migrated text to this repository's
`role_prompts.json` and `reviewer_profiles.py` at main commit
`f4d58d02d3741271ba48d1381819c95d21775f43`; no third-party package or new license is
asserted. Legacy prompt/profile files remain unchanged for historical behavior.

## Remaining in this same S6 issue

The verified #445 adapter must consume the protocol base and locked methods,
observe required/on-demand/task-required loading and protected report return,
and complete the real end-to-end UI review with an arbitrary user package.
Actual output validation and independent evidence must distinguish a structured
report from a model's success claim. Builder/reviewer process isolation must be
observed, not inferred from distinct job/session fields.

The Operator's current independent PR review uses the `qa` role, not
`code_review`; this patch does not relabel or change that role's bound task.
Its existing exact-head/read-only/advisory protocol remains, but the complete
S6 all-entrypoint audit and any explicit Role migration remain outstanding.
Other-role method extraction, generated graph artifact guidance, additional
instruction provenance/UI, default adoption and report-channel integration must
be reconciled with #445 before declaring complete method replacement. Do not
close #446, merge this as complete S6, or certify the overall Skills milestone
based on these preparatory contracts alone.

## Verification

Tier 3/4. Focused tests exercise actual task capture, Worker publication, parallel
review creation, direct dispatch, historical replay, retries, readonly/session
rejection and legacy-wrapper rejection. They preserve explicit user inputs and
require zero Runtime calls. A package test checks optional installation without
implicit binding. Existing legacy regression assertions are retained.

Product tests run only in the authorized CI environment. Full backend/Ruff and
unchanged HTTP/browser/frontend validation remain required, with exact head/base
and logs recorded in the PR. Synthetic fixtures are not real-model evidence.
No deployment, provider calls, credential reuse, host/global settings or runtime
activation are authorized by this document.
