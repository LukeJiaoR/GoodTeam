# Archived design: Reviewer Profiles and Invocation Evidence

> Archived 2026-09-06. This July 2026 proposal is retained for design history. The current implemented contract is [`../../reviewer-profiles.md`](../../reviewer-profiles.md).

Status: superseded
Original date: 2026-07-04
Original scope: design only

## Context

GoodTeam should borrow the useful contract from adjacent multi-model reviewer tools without copying their command surface. The useful idea is simple: one worker may produce the artifact, while other workers review it from different dimensions. GoodTeam must keep the control plane, ledger, approval gates, and evidence model as the source of truth.

## What to borrow

- Real reviewer provenance: a review must say which configured adapter produced it.
- Distinct review dimensions: correctness, security, architecture, maintainability, product fit, and release risk should be separable profiles.
- Raw evidence: stdout, stderr, result paths, preflight, model, timing, and exit status should be preserved where available.
- Explicit fallback: fallback output must never look like normal provider-backed review.

## What not to borrow

- Do not copy another project's command surface.
- Do not introduce autonomous multi-agent loops.
- Do not hardcode a single reviewer pair.
- Do not make external tools the source of truth.
- Do not weaken human approval gates.

## Reviewer profile

A reviewer profile is a routing and prompt contract. It should include:

- stable id
- label
- adapter
- optional model
- focus areas
- required report sections
- required evidence fields
- fallback policy
- timeout expectation

Example profile names:

- `codex-correctness-security`
- `gemini-architecture-maintainability`
- `claude-product-operator`

These are examples, not a mandatory team shape.

## Invocation evidence

Every worker status should eventually include an `invocation_evidence` object.

For provider-backed workers it should record:

- kind
- adapter
- provider label
- model
- command summary
- preflight summary
- stdout path
- stderr path
- result path
- exit code
- timeout seconds
- capture mode
- attempts
- failure class
- disposition
- fallback flag
- fallback reason
- started and completed timestamps

For fake, noop, and human workers it should explicitly record that no real external invocation happened.

## Review Workbench impact

Review Workbench should eventually show:

- which provider or adapter produced each review
- whether the review was real, fallback, fake, noop, or human
- reviewer focus dimensions
- local evidence handles for result and raw output
- warnings for fallback or missing evidence

The UI should continue to treat recommendations as advisory: recommendation shown, human decides.

## Minimal first implementation PR

After this design, the smallest safe implementation is:

- add `invocation_evidence` to worker status payloads
- cover provider-backed and non-provider adapters in tests
- preserve all existing top-level status fields
- do not change orchestration, approvals, UI, or parallelism

## Acceptance criteria

This design PR is doc-only and changes no runtime behavior. The first implementation PR should make worker provenance explicit without changing how workers are dispatched.
