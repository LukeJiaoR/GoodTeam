# Opt-in Pi Web Search (#701)

This increment attaches `goodteam_web_search` to the existing ordinary Pi RPC
runtime. It is not another Worker engine or a Researcher-specific path. Protected
Skill/Input/Correction runtimes stay excluded; their admission contracts must not
inherit this capability. Search is disabled by default.

## Explicit host-side job authorization

A caller of `WorkerJobStore.create_job` can supply:

```python
web_search={
    "provider": {"id": "<existing ProviderStore id>", "revision": 1},
    "model": "<approved search model>",
    "max_requests": 2,
    "timeout_seconds": 30,
}
```

Use an existing approved Responses provider and model. Its credentials are
resolved by the host, not handed to the extension. This configuration is separate
from the Pi reasoning model. There is no automatic provider selection, credential
creation, UI default enablement, or fallback to another paid provider. A request
allowance bounds Responses requests, not provider-internal search subcalls. It is **not** a currency budget or a claim that the
provider is free. The submitting caller must already have spending permission.

Pi receives a per-process loopback URL/token and the bundled extension. The tool
sends only its explicit public query (up to 4,000 characters); it does not send the
whole task or Context to the search provider. Pi remains the existing trusted
local runtime, not a sandbox: the extension is not a security boundary against a
malicious process with the same filesystem/user privileges.

The host uses OpenAI SDK `responses.create`, `tools=[{"type":"web_search"}]`,
`include=["web_search_call.action.sources"]`, `store=False`, no redirects and zero
SDK retries. A compatible API returning ordinary text without a completed search
call is recorded as `not_searched`, not silently accepted as searched evidence.
Sources, annotation offsets, response/search IDs, usage and original job/task/run
ownership are retained in the job's `web_search.json` receipt. The Pi assistant
result includes a source appendix, so the existing Worker result/Context path
receives clickable evidence even if the model omits citations. External search
content is explicitly untrusted.

## Recovery and Stop

A receipt is atomically persisted **before** contacting the upstream, with a
process lock protecting the per-job allowance. Identical trimmed queries replay
the saved receipt. Failed, interrupted or unknown outcomes consume allowance;
they are never automatically resent after timeout/restart. Different wording is
not an authorized workaround for an uncertain outcome. A changed policy for the
same persisted job is rejected. Stop disables new requests immediately; an
already accepted upstream request may still finish and be billed. Its result is
retained when the host survives. No claim of upstream cancellation/refund is made.

## Evidence / remaining acceptance

`tests/test_web_search.py` executes the actual extension in Node against the host,
with the real OpenAI SDK and a mock HTTP transport. It checks sources, attribution,
no automatic retry, durable replay, allowance and Stop. A fake Pi RPC process
checks runtime installation and source return. These tests spend nothing.

A real provider's support for Responses `web_search`, a live Pi/model choosing the
tool, and end-to-end Manager Context use still require approved live validation.
Do not mark #701 or the complete project loop accepted based only on these local
checks; carry the live combined path in #419. CI remains paused. No deployment or
provider/security settings are changed by this increment.

## Confirmed project-task queue

An authorized host may now create/update an existing `LocalTaskStore` task with
an explicit `web_search` policy of the shape above. This is not inferred from
Manager text, an external report, or a default Provider. Discovery-generated
new-task intents still reject this field. It does not add a search-settings UI.
The caller must already hold the specified search/spending authorization.

For ordinary Pi work, the task's existing `workspace_write` policy maps to the
existing isolated batch's `standard` Worker policy. The queue displays the exact
search allowance; original task intent/digest, proposal revision and immutable
batch tracks retain it. A changed allowance invalidates the old confirmation and
continuation task intent. Dispatch passes it to the original task-associated job,
whose existing replay check rejects replacing its search policy. No second job
ledger, model-selected budget, automatic enablement, or provider fallback exists.

Read-only tasks, non-Pi adapters, protected Skill/Input/Correction paths and the
role-replacing Workflow fallback cannot acquire this capability. They fail
rather than drop the policy or lower the execution policy. Existing custom-method
capture and isolation rejection remain enforced; only the pre-existing ordinary
method-free path is supported. Manager itself keeps its read-only execution
policy and consumes returned Worker evidence rather than receiving search tools.

`tests/test_queued_web_search.py` covers the real local task -> recommendation ->
queue -> batch-confirmation -> dispatch job construction and once-only result
publication into Manager conversation Context. Search HTTP/provider output is a
fixture; no model/paid endpoint or isolated process acceptance is claimed. The
existing SDK/Pi extension tests cover those transport seams independently.
