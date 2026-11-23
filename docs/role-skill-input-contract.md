# Ordinary Role Skill input preparation (#445)

## Ordinary QA dispatch

A new isolated `read_only` QA track can use a custom Role binding with Codex
or Pi. The immutable Worker request holds the Provider reference and the Role
snapshot. Retries of ordinary method jobs use the original task lock and
Provider reference.

Codex receives complete required instructions over stdin. GoodTeam places only
the locked packages in a private temporary directory and gives each catalog
entry its own resource base. The read-only Codex command can read files using
their original relative paths. The directory is removed when the process ends.

Pi receives the required instructions over RPC. Its existing read-only tool
policy makes the context-bound resource broker available for textual resources.
The Worker detail shows the adapter, saved Provider revision, Skill version
references and preparation or prompt-transport stage. Resource use and task
acceptance require their own evidence.

## Content projection

`SkillContextProvider.worker_input(reference)` projects an already captured,
verified content context into a bounded private adapter input. It preserves the
original Role, thin Role contract, exact delegated task (including purpose,
scope and acceptance supplied by the shared delegation contract), relevant
Context, constraints and expected artifacts. Required method bodies are complete;
on-demand methods expose only their versioned resource catalog until requested.
The projection never serializes the full Worker request, Operator root or runtime
configuration. Explicit user task text is preserved, not sanitized or rewritten.

The method uses `private_input` for current admission, task scope, immutable bytes
and revocation checks. The complete rendered input has its own byte limit and
fails rather than truncating. No lookup of current Team settings happens at this
point. Code Review retains its dedicated protocol and cannot use this projection.

Each broker read validates the original content reference and checks the
requested Skill and resource against the captured catalog. A broker receipt
records the bytes returned by that operation.

## Verification scope

The combined QA regression runs the original dispatcher and immutable stores in
isolated Git worktrees. It checks both Provider adapters, required instructions,
relative resources, disabled packages and the Worker result. The native smoke
uses Codex 0.171.0 and Pi 1.1.0 against a loopback Responses endpoint. StockFlow
browser acceptance and real-model evidence are tracked in #419.
