# Explicit ordinary Pi method input (#445)

Base: `447b71ccc11b309ac056b89ca1c86b0e658de132`, the already-pushed
#445 resource channel and startup guard, based on main `602ad626`.

## User outcome and implementation

A newly captured standard Pi Worker can explicitly select
`method_loading_mode="ordinary_pi_methods.v1"` through the existing `create_job`
input. `run_job` retains the original job, task, Role and package snapshot, renders
complete required methods and the optional catalog, and delivers that input to
the existing Pi RPC session. The existing extension/broker serves pinned text
resources and closes with the session. No second Loop, dispatcher or worker pool
is introduced. Multiple Roles can reuse the same package without sharing job
input or sessions. The input still includes the original task and Context.

The selection is immutable. The original `dispatch_tracks` producer now captures
this selection in a NEW task confirmation when every track is standard Pi /
pi-rpc and no track is Code Review. Existing Role bindings supply the methods;
no custom binding still means the legacy path, not invented method content.
Historical confirmations and requests without the mode remain strict, even if
interrupted before Worker publication. Enforced read-only, Code Review,
cross-job resumed sessions and mixed Runtime/policy batches are not converted.

The same dispatcher prepares its retained isolated Git workspace. The new
method request requires that exact run/job/workspace mapping, and an immutable
operator receipt prevents substitution on replay. Both Worker execution and the
direct Pi Runtime entry revalidate the cwd; missing or foreign workspace is
rejected, never silently replaced with the project root. Other externally supplied
workspace modes remain unsupported. This producer/consumer wiring is a development
increment, not proof of real Manager delegation, complete #445 or project Loop
acceptance. See [dispatch details](pi-method-dispatch.md).

The ordinary method mode can also use the existing Web Search tool when its
original producer already supplies an explicitly authorized bounded search policy.
The policy stays in the original immutable request; the method cannot grant it.
Both tool channels share the original session lifecycle and Stop. See
[method/search composition](pi-method-search.md). Strict/read-only paths are unchanged.

## Lifecycle and evidence

- One nonblocking invocation lock prevents two executors from starting the same
  job. Ordinary Pi prompt/recovery and control receipts remain authoritative.
- Original job metadata stays immutable; Runtime status and failure are separate.
- An operator-owned immutable session receipt binds job/run/execution/project,
  session ID and session file. Recovery checks it before starting any RPC.
- Method input and resource access revalidate current package/task state.
- Status and Stop only require original invocation identity; revoking a method
  cannot remove Stop. Steer/follow-up revalidate methods at actual consumption.
- Content provenance is `ordinary_pi_methods`, explicitly not strict isolation
  conformance. A prepared/transported input is not proof of model adherence.
- Runtime permissions, cwd, preflight and cleanup remain the ordinary Pi path.
  No read-only upgrade, model spend, security setting change or production deploy.

## Verification boundaries and next work

The representative test exercises original creation, immutable input, real
existing Runtime code, a local fake RPC process, complete prompt digest, same-job
recovery and two Role identities. The resource broker is real; listener startup
is socket-free in this environment. It is not real HTTP/Pi/model acceptance.
Independent review exercises busy execution, foreign session recovery, method
revocation, follow-up refusal and continued Stop availability.

The producer checks now cover new-mode capture and replay, historical confirmation
compatibility, read-only rejection, exact retained mapping and direct-Runtime cwd
rejection. A mapping fixture is not Git isolation evidence. The original public
retained-Git / fake-Pi integration case was attempted but stopped at the existing
`Trusted Git executable was not available.` check in this environment, also
reproduced by a baseline isolation test. That case reports NOT RUN / skipped for
this exact refusal; no trust gate or security setting was changed.

Next validation: run the retained-Git integration case in an environment already
meeting the original trusted-executable checks, then separately obtain authorized
real Pi/Provider and product-level evidence. Mixed batches and unsupported
Runtime policies are not silently broadened. Full real project continuation and
product acceptance remain in #419.
