# Runtime catalog and reasoning selection

This is the runtime-only extraction of #430 on main after #431. It does not
replace the Work composer, remove its recovery controls, change approval cards,
or modify example projects. The chat-feedback increment is independent.

Team refreshes `/api/agents` with `cache: no-store`. The server reports catalog
provenance (live, static, unavailable, unsupported); a failed live probe must not
substitute bundled or previously returned models. Saved model names remain
visible even when discovery is unavailable. Per-model effort metadata takes
precedence over adapter fallbacks, including an explicit empty capability list.
Changing provider/model resets an incompatible draft effort to default.

The portable effort vocabulary is shared by Team persistence, web normalization,
role/reviewer dispatch and worker command construction. Acceptance in that
vocabulary is not evidence that every provider/model accepts every effort. The
installed CLI validates the actual runtime arguments; catalog mocks and command
construction tests are not live provider-compatibility evidence. Claude's static
catalog and adapter fallback efforts are not claimed to be live discovery.

Saving Task Assistant runtime in Team explicitly offers a connection check and
warns about one potentially charged read-only model call. It saves configuration
first; save success and check failure are presented separately. No check is
launched merely by opening Team. Work retains its existing runtime controls.

Verification tier: Tier 4 UI/adapter path plus Tier 3 runtime persistence/dispatch.
Focused coverage: tests/test_workers.py, tests/test_team_control_plane.py,
TeamPage.test.jsx, runtimeOptions.vitest.js and runtimeCatalog.vitest.js. Full
backend, frontend tests/lint/build, and isolated fake-worker browser acceptance
are required before merge. Real installed-agent catalog/effort and connection
smoke remain separate acceptance evidence; do not run them on a real project or
represent simulated evidence as a validated provider configuration.
