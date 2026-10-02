# Task-owned provider bridge candidate (#445)

Status: **actual HTTP/pipe forwarding implemented and tested against a local
model-free server; production admission and native model execution remain
blocked**. This independent increment is stacked on #475's fail-closed fix,
which is itself stacked on #473. It does not merge either PR, use #474, replace
the host's managed route, or enable the blocked macOS native CLI path.

## Concrete path

The existing pinned Codex 0.155.1 Linux ARM64 envelope remains the intended
Worker boundary. Its only provider peer is a temporary, secret-free relay.
The relay receives `POST /v1/responses`, sends a bounded JSON/base64 frame over
the owning parent's attached stdin/stdout, and returns the parent's bounded
response. The host-side `BridgeRelayPump` passes that body to
`TaskProviderBridge`. Only the bridge resolves the effective upstream route and
existing bearer through a typed `HostProviderAuthority`. That authority is not
implemented or registered in production by this increment.

The relay has no outbound HTTP client, credential lookup, model CLI, host
configuration or Docker API. It forwards no Worker headers. Its pipes carry no
host endpoint, account authorization, credential path or bearer. Only a locked
Responses body, a sanitized response, and request/cancel identities cross them.
The host bridge performs the upstream HTTP call rather than returning provider
readiness metadata. Its real transport is exercised offline below.

Neither component creates a Docker network, image, container, persistent
credential or public HTTP listener. The relay starts only when explicitly
invoked by an owning controller; the product does not invoke it. Its explicit
loopback mode is used for offline probes. Container mode needs separately
approved isolation and network wiring before it may bind its private endpoint.

## Enforced local boundaries

- Parent-owned `BridgeScope` binds the exact task, Worker root, TaskSkillLock,
  source snapshot digest, fresh context, model, expiry and finite request/byte/
  deadline/output-token exposure. A Worker cannot choose an account, upstream
  URL, model, authorization or an old Responses context.
- Private Operator storage outside the Worker has an immutable authorization
  manifest and a stable `flock` budget. Each request allocation is fsynced before
  external connect/write. Failed calls, redirects, rate limits, cancellation
  and restarts never refund it. Fresh contexts share that authorization's
  budget; an existing context cannot be resumed. Changing its task/input/model/
  route witness or limits cannot reset the authorization ID.
- One owned transport per context; no redirects or automatic retries. The
  default is **one upstream request**, a **20-second deadline**, **2 MiB** each
  request/response, and **2048 requested output tokens**. All are finite. The
  bridge rejects tools, nontext input and previous-response continuation. It
  requests low reasoning, default service tier, no background/store, and no
  parallel tools. This initial bridge supports the method-free Reviewer only;
  bound Skills cannot gain read/script permissions from it.
- Route authorization is rechecked before forwarding, during response reads,
  and by a bounded watcher while the HTTP call is blocked. Revocation or
  deadline retires the context and shuts down its registered socket. A cancel
  never clears the connection socket in a way that permits implicit reconnect.
- Worker HTTP disconnect propagates through the relay to that exact host
  request. Closing the owning relay process/pipe retires the bridge. Foreign
  cancellation frames and overlapping requests fail closed. Local shutdown is
  reported confirmed only after the owned operation exits; unresolved transport
  shutdown remains an error.
- Upstream cookies/location/auth headers and raw error bodies are not relayed.
  Errors use fixed codes. Direct bearer reflection in a successful body is
  rejected. Authentication exists only in host memory while calling its trusted
  provider; no credential is persisted to the ledger, pipes or container.

## Precise limits

This is not a USD hard cap. Upstream request allocations bound local forwarding
attempts, not the provider's billing counters. A requested token limit/low effort
is not a verified tariff or proof the custom provider enforces the limit.
Unknown tariff and remote work retirement remain unsupported. No Dodo or
hypothetical account management/retirement API is added.

Responses/SSE are bounded and buffered before relay delivery. This keeps direct
bearer reflection out of a partially delivered response, but prevents incremental
token display. Use the default 20-second deadline below the pinned CLI's
30-second idle timeout for the initial small, one-request review. A timeout can
consume a paid request while yielding no report. There is no automatic retry.

The local socket may stop while a provider continues generation after accepting
the request. The offline mock explicitly does that after client disconnect.
`BridgeStop.remote_retirement_confirmed` is always false; it cannot satisfy the
existing strict `CodexReviewAdmission.finish` retirement requirement or promote
a result to `verified_runtime`. A subsequent self-use Linux composition must
expose these limits and keep reports advisory/method-free rather than weaken
the strict Skill admission contract.

## Remaining integration and exact setup boundary

1. Implement/review the host authority that obtains the **effective managed
   provider** and permitted account/model, then seals those actual route values
   for host forwarding. A user `config.toml` hash, alias, features listing,
   `verified=true`, or JSON route manifest is insufficient. The host macOS CLI
   will not execute a model, so its route diagnostics are not being mistaken for
   a sealed subsequent native `exec` configuration. There is no fallback to a
   different provider if managed requirements select another route.
2. Inspect the immutable cached Linux image/native discovery roots and perform
   actual native model-free inventory/egress checks. Preserve the existing
   image/binary/guard/seccomp contract; passing these loopback tests does not
   establish Docker/Linux or native Codex conformance.
3. Before any Docker setup, obtain approval for **one new task-owned internal
   IPv4 Docker network**, only two ephemeral peers (relay and Reviewer), no
   published host ports, existing cached image only, no pull/build, no host HOME/
   credentials/socket mounts, and attached parent pipes. Do not reuse unrelated
   networks or change their peers, daemon settings, host firewall, credentials
   or security policy. Bind that exact relay container/IP/network into the
   existing `CodexContract`; inspect ownership and remove only owned peers and
   the new network on teardown. This setup is not performed by this PR.
4. Compose a separate, honestly bounded self-use Linux authority through #473's
   existing startup/run/retry injection after the above checks. It must retain
   task/source binding, single Reviewer capture and advisory provenance. The
   bridge does not install such an authority merely because a fixture works.
5. A new paid trial needs approval for the **actual effective provider/model**,
   one request/context, default 20 seconds/2048 requested output tokens, no retry,
   with the tariff/expected USD budget disclosed or explicit acceptance that
   tariff/USD ceiling/remote retirement are unavailable. No new trial occurs
   in this increment. The earlier failed native trial is not reused as success.

## Model-free evidence

Tier 3/4 coverage uses real localhost HTTP, independent quota-sharing processes,
a real isolated relay child, pipes and client disconnects. The provider is an
explicit `contract_fixture` loopback server. Fixture authority cannot borrow a
remote HTTPS provider or production provenance. Default production construction
without a typed host authority rejects before storage or forwarding.

```bash
GOODTEAM_DISABLE_TG_NETWORK=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  .venv/bin/python -m pytest tests/test_codex_bridge.py tests/test_codex_relay.py -q
GOODTEAM_DISABLE_TG_NETWORK=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  .venv/bin/python scripts/ci/codex_bridge_probe.py \
  --workspace /explicit/disposable/probe-root --output /explicit/bridge-evidence.json
```

The probe writes actual observed local HTTP request counts, allocation bounds,
local shutdown and the mock's later remote completion. It reports zero native
Codex invocations/external model calls and no host credential reads or Docker/
network changes. Exact focused/full results and runner CI failures belong in
the draft PR. Native execution, effective host authority and real-provider
accounting/cancellation remain **NOT RUN / unverified**.
