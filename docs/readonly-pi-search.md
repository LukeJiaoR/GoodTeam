# Authorized Web Search for read-only Researcher / Planner

The existing #701 task search policy can now accompany newly captured
readonly_pi_methods.v1 Researcher/Planner jobs. Explicit policy is still required:
pinned Provider reference/revision, model, request count and timeout. Missing
policy grants nothing. The policy is validated in the original task ledger,
project batch track adapter, immutable Worker request and existing JobWebSearch.
Changing a read-only search task to an unsupported Role is rejected, without
changing the saved task. No default network, provider or billing authority added.

The read-only tool contract adds goodteam_web_search only when the original job
carries that explicit policy. The extension requires all five original tools and
permits only that one optional tool; its exact registry and active set are
witnessed before prompts. Manager and strict contracts do not acquire Search.
Original policy/identity/method availability checks, per-job budget, unknown
request states, same-query cached evidence, Stop and revocation remain owned by
JobWebSearch and the original Role broker. Cached evidence remains readable;
revocation or Stop prevents new consumption. Search query input is not implicitly
filled with private task Context. Source/result attribution stays on the same job.
Runtime environment composition now retains the tool-policy nonce together with
Search and Skill channel credentials. Restores still require the exact saved
tool contract and fresh startup witness. No shell/file writer/session-switch or
other read-only capability is added. This remains tool-level restriction, not
OS confinement, network sandboxing or confidential-path isolation.

Evidence:
- Actual LocalTaskStore task creation, Role-change refusal, dispatch capture,
  method broker and original Runtime are exercised with isolated fixtures.
- Search Provider/requester, workspace mapping, local listener and Pi process
  are explicit offline doubles. The Runtime representative maps its task to its test track; a separate original
  queued-search test exercises configure_queued_search, revision/digest rebinding,
  require_queued_project_task and the real _runtime_tracks adapter.
- Research/Planner with and without policy: authorized search consumes one
  existing request receipt; same query returns cached result; revocation/Stop do
  not consume again; no policy means no Search tool, token, host or request.
- Existing role/method/Manager/Search regressions: 23 passed. Ruff passed.
- Previous source: two authorized cases fail at the old ledger restriction;
  two no-policy cases pass. Independent review reran five composition/producer
  checks and both Node extension registry modes, no blocker.
- No real network search, Provider call, paid model, install or deployment.

Base complete readonly-methods archive SHA256:
24d1cf92c288ea9d7277c5ebaf9337aaabe757f4f895f6787ae2a325fcf22d95.
Local candidate only; not pushed or merged. Real Pi SDK/provider behavior and
full continuous project acceptance remain under the original acceptance work.

Final entrypoint completion: the original search configuration UI had disabled
all read-only assignments. It now allows only Researcher/Planner among those
assignments and keeps explicit cost/provider confirmation; unsupported QA stays
disabled. UI 2 tests passed using existing local dependencies. The original queue
configuration API was corrected in the same scope; queue/Runtime regressions 6
passed. Existing error-message assertions were adjusted, and the previous blanket
Researcher-denial case now checks unsupported QA instead. No negative boundary
was silently removed. The first full result is retained for transparency.

Final full backend: 562 passed, 32 failed, 5 errors, 21 skipped. Exact 37 failure/error identities match the prior readonly-methods baseline (558 passed). Suite is not green; original environment/baseline failures remain.
