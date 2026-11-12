# Ordinary Role methods and the existing Web Search tool (#445 / #701)

Methods and tools are separate capabilities of the original Pi invocation. This
increment removes the blanket prohibition on combining them only for a newly
captured `ordinary_pi_methods.v1` standard Pi task. It does not add a search
permission, provider, Runtime, scheduler, HTTP service type or paid allowance.

The producer must already supply an explicit existing `search_policy`: pinned
provider revision, model, bounded request count and timeout. The original
`create_job` / `create_or_reuse_job` and `_create_track_job` paths validate and
capture that policy in the immutable task request. Missing policy stays missing;
`None` is omitted to preserve historical request digests. A Skill body cannot
supply or expand policy, and an existing job cannot be replayed with a changed
policy. Strict jobs, read-only jobs and unsupported Runtime modes remain closed.

The existing Pi Runtime installs both existing extensions and retains both
per-instance capability tokens in its environment. `JobWebSearch` continues to
own provider resolution, budget consumption, unknown outcomes, cached results
and source evidence. Before consuming a new query, its active callback checks
the original method/job identity, exact search policy and current method
availability. Revocation or tampering stops new work; previously recorded search
results remain replayable without another request or budget refund. No method
resource bytes or task Context are automatically added to a search query.

Abort and Runtime-side cancellation stop both tool channels without waiting for
HTTP listener shutdown. Resource reads cannot deliver after Stop; new searches
cannot start. Final close recycles both listeners through the existing Runtime
lifecycle. A prepared or searched result remains evidence, not task completion,
method adherence, stronger isolation or approval.

## Validation boundaries

The representative integration uses real original snapshots, the resource broker,
Pi Runtime and a local fake RPC subprocess. Listener startup and the hosted-search
requester are explicit fixtures; there is no external search or paid model call.
It verifies both extensions/tokens, original policy, resource access, one
fixture-search receipt, non-duplicating replay, tampering/revocation rejection,
separate jobs, strict/read-only rejection and Stop. A second representative case
uses the original dispatch job producer to verify policy capture/replay and
refusal to upgrade an old strict request. Existing SDK/extension tests retain
their HTTP mocks and do not issue external requests.

Real retained-Git/Pi integration still has the separately recorded trusted-Git
environment prerequisite. Real Provider/Pi and complete project Loop acceptance
remain unrun and separate from these development fixtures.
