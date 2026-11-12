# Reuse original Manager completions across the Loop

The new Manager Pi producer publishes an immutable request plus mutable terminal
status. Its replay returns a status projection on top of that request. Chat and
intent previously called run_job unconditionally when task_skill_context existed:
a completed session started a second Pi process and repeated the witness/restore
handshake, although its confirmed settled prompt was NOT sent again. Planning and
goal assessment already skipped execution via the terminal projection, but did
not check the original Pi session completion receipt at that point.

The common Manager helper now reads the canonical immutable request, verifies its
registered method/snapshot/context, original terminal identity, exact Manager tool
contract, session binding and settled initial prompt digest, using the same
completion verifier extracted from the running-observation path. A valid completed
turn returns only its original fixed local result, without starting a Runtime or
rewriting execution evidence. Missing or contradictory claimed completion fails
closed with the existing ManagerExecutionError contract and no silent replay.

This is not a new queue, scheduler, authority, acceptance gate or model cache.
Only the exact previously captured Manager invocation is reusable. Changed inputs,
revoked methods and session mismatches cannot reuse an old result. Non-completed
and legacy paths retain their existing execution rules. Explicit ordinary Worker
session restore is untouched. Observation retains its original authority/reservation
and result publication gates.

## Verification scope

Public chat/intent and planning paths use the original producer, registry, method
capture, WorkerStore and subprocess RPC. The binary, witness and responses are
explicit offline fixtures and guarded against unexpected live fallback. No real
Pi/Provider acceptance, model fees, installation, push, merge or deployment.

Targeted: 59 passed; Ruff passed. Independent review: 44 passed, no blocking
findings, including revoked methods, aborted/missing/contradictory session evidence,
malformed status and malicious result-path projection. Planning/goal's existing
skip-run behavior is not counted as newly implemented functionality.

Frozen full backend: 554 passed, 32 failed, 5 errors, 21 skipped. Exact 37 failure/error identifiers match the 544-pass base; existing failures are not represented as green acceptance.
