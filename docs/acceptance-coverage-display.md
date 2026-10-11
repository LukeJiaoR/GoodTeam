# Original acceptance coverage in Review Workbench

The existing HTML and text workbenches now show every criterion from the
protected task-input snapshot, including criteria with no current check mapping.
Repeated criterion text retains separate snapshot-bound identities. The display
uses one-based labels and also names the zero-based CLI index.

Each mapped criterion names the current validation receipt and exact candidate
head. A passing local check remains **acceptance unknown**: the command-to-item
mapping was supplied by the operator and does not establish semantic coverage.
Only the latest check mapping is displayed; earlier successes are not accumulated.
A stale candidate or mismatched input/receipt is shown as unknown. Missing input
does not fall back to the editable project task. Partial input history preserves
available captured criteria while displaying its incompleteness.

Check details expose the executable basename, recorded process exit code and
nonnegative, timezone-aware host-wall-clock duration. They do not display command
arguments, executable parent paths, environment or raw stdout/stderr. Invalid or
backwards timestamps produce an unknown duration. Host elapsed time is not a
provider cost or billing measurement.

This is a read-only presentation of existing evidence. It adds no execution,
review, merge or provider authorization, no storage mutation and no Git-writing
path. The existing interactive controls and static-export restrictions remain.
Legacy executions without a capture explicitly lack original coverage.

Validation tier: focused Tier 2 read-only UI/contract tests, using real synthetic
capture/API/check paths and generated-HTML DOM checks. No production provider,
live browser, full backend matrix or remote exact-candidate CI is implied.
