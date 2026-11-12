# Preserve supported Role methods in an all-Pi mixed-Role batch

Audit against #445/#686 found a production gap beyond mixed Runtime capture:
implementation + architect, both standard Pi, took the historical shared route.
Because architect has no built-in default method package, shared default capture
was disabled for both; implementation silently lost its bounded-work package.

New requests retain the shared route only when all tracks are ordinary Pi and
all their Roles have built-in default packages. Other mixtures reuse the existing
per-track capture, task locks and original request receipt. No new package is
invented for architect; it remains legacy unless its own custom binding applies.
Historical captures/jobs/requests are unchanged; original identities, Team revision,
custom-empty strict behavior, explicit read-only/search and Stop remain intact.

Existing mixed dispatch test now also covers all-Pi implementation + architect.
Old source: 1 passed / 1 failed (implementation method missing). Fixed focused
suite: 7 passed. Independent review: 5 passed, including partial capture/replay
boundaries. Ruff passed. All evidence uses offline fixtures before original Git
workspace preparation; no installed Pi, Provider, Search or paid call occurred.

Final backend: 563 passed, 32 failed, 5 errors, 21 skipped. Exact 37
failure/error identities match the prior Search candidate (562 passed).
#677 integration: Review compact files already match its saved candidate;
Settings/Knowledge retained. Seven-file exact b6517d7 continuity patch applied
with zero fuzz; independent composition review passed 4 UI representatives.
Combined relevant frontend: 88 passed, 2 existing Review failures.
