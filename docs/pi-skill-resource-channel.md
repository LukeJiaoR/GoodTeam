# Pi method-resource channel (#445)

This slice provides the missing Pi extension/host resource transport over the
existing `SkillContextProvider` broker. It does not register a production adapter,
change ordinary dispatch, or supply admission. Ordinary Skill jobs still fail
closed with `skill_runtime_not_connected`.

The host pins a deep-copied content reference and original Pi invocation. Model
arguments contain only a locked skill ID and relative resource path; the Pi tool
call ID is supplied by the extension. Reads reuse task/package/revocation and
Runtime session checks and immutable broker receipts. A repeated call ID cannot
be rebound to different resource bytes. The loopback endpoint uses a fresh
per-instance capability; the model receives no context selector or package root.
Start/close are serialized, and Stop during authorization/read suppresses late
content. Errors do not expose private paths or exception text. Binary resources
are explicitly unsupported by this text tool.

Only `broker_read_completed` is reported. This does not establish tool delivery,
actual model reading, method adherence or successful task execution. No new Loop,
queue or result ledger is added.

## Remaining admission work

A production Pi envelope must bind the context and channel lifecycle to the
original job/run/attempt, establish its real isolation/discovery/permission
boundary, deliver the existing complete `worker_input`, and retain real input,
resource and termination/result evidence. There is deliberately no config flag
or production selector enabling this transport in the present change. Passing a
fixture authority is contract testing, not Runtime admission. Existing Reviewer
contracts and the paused #446/#476 trials are unchanged.

## Validation scope

Tests use actual task snapshots and Skill content/broker with explicit fixture
authority, a socket-free HTTP handler fixture, and the actual Pi extension loaded
in Node with fetch mocked. They prove pinned resource selection, bearer rejection,
replay/conflict, Stop, parameter filtering and default closed dispatch, not real
HTTP sockets, Pi, Provider or complete project Loop acceptance.
