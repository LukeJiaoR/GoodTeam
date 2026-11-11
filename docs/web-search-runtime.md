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
