# Skill Runtime conformance (S0 / #440)

This is a diagnostic experiment, **not** a production readiness endpoint. It
implements the first validation slice of #438 / #449. It does not install Skills,
change Team configuration, start a GoodTeam workflow, or enable strict binding.

## Decision

Prefer **injected required instructions plus immutable, explicitly addressed
resources** for the first Reviewer adapter. Keep native discovery as an option
only after the exact installed Runtime can demonstrate an allowlisted method
inventory. Neither approach is currently certified by this PR.

GoodTeam already has a Codex read-only command branch and an isolated
`CODEX_HOME`. The latter does not isolate `$HOME/.agents/skills`, repository and
ancestor roots, `/etc/codex/skills`, or bundled methods. Likewise, a read-only
sandbox is not evidence of read confidentiality. An actual denied attempt and a
working positive control must be observed; an agent declining to attempt an
operation is not a sandbox PASS.

Reference contracts checked on 2026-09-21:

- [Codex Skills](https://developers.openai.com/codex/skills): discovery includes
  repository, user, admin and bundled system locations; disabling a known local
  Skill is distinct from eliminating all implicit discovery.
- [CLI reference](https://developers.openai.com/codex/cli/reference):
  `--ignore-user-config` addresses user configuration, `--ignore-rules` addresses
  exec-policy rules; neither alone proves a Skill allowlist. JSON events are an
  observation interface, not an approval decision.
- [Non-interactive mode](https://developers.openai.com/codex/noninteractive):
  inspect the version-specific execution and output contract before enabling an
  adapter. This probe checks the installed CLI's own help rather than guessing.

## Default: no model, no credentials

Use a **new** output directory whose parent exists:

```sh
PYTHONPATH=src python -m goodteam_os.skill_conformance \
  --output /tmp/goodteam-skill-inspection-UNIQUE
```

Only `codex --version` and `codex exec --help` are executed when a binary is
available. No login, installation, package scripts, provider calls or real project
configuration is used. The child receives a new HOME/CODEX_HOME/XDG/TMPDIR and no
inherited credentials, proxies or shell startup variables. PATH is retained only
to locate the explicitly trusted executable and its interpreter.

Exit **2** means unsupported/unverified, including an absent CLI. It is not an
instruction to rerun until green. `report.json` records the reason and every
capability individually. A help flag being advertised is syntax evidence only.
Existing output directories are never overwritten, merged, or deleted.

## Separately authorized live experiment

Run **only inside a disposable, externally isolated environment containing no
real project data or unrelated secrets**. The acknowledgment flag does not create
that isolation. The probe deliberately tests whether a sandbox fails, so do not
use your normal workstation's authenticated agent configuration.

The operator must explicitly provision a dedicated provider credential through
an environment variable. No credential is discovered or copied automatically.
After obtaining approval for at most two CLI invocations (each can make several
provider requests), run:

```sh
PYTHONPATH=src python -m goodteam_os.skill_conformance \
  --output /tmp/goodteam-skill-live-UNIQUE \
  --codex /absolute/path/to/trusted/codex \
  --live --allow-real-worker --disposable-environment \
  --model APPROVED_MODEL \
  --credential-env GOODTEAM_PROBE_API_KEY \
  --contexts 2 --timeout 120
```

The credential value is never placed in arguments or copied to auth/config
files. Only the selected value is passed as CODEX_API_KEY and redacted from the
persisted event text. This is not a universal secret scanner; the environment
must not contain other sensitive content. Local reports are not automatically
published to GitHub.

The probe creates only its own candidate, package, control-state, and non-secret
outside-root sentinels. It gives the agent exact helper commands to try reading
resources and touching those sentinels. A local loopback server returns a random
non-secret marker; no external target is probed. It is stopped before exit.
The CLI runs read-only with explicit no-approval and disabled web search, never
with a bypass flag. The parent captures stdout and records the result without
giving the model write access to a production state directory.

Time and output limits stop and reap the owned POSIX process group. A demonstrated
escape, changed fixture, unsupported invocation, or incomplete event stream cannot
be repaired by a later successful check. No auto-retry occurs. Only a second fresh
context may run when requested and the first has no demonstrated escape.

## Capability matrix and evidence

| Capability | Evidence accepted by this probe | Limits |
| --- | --- | --- |
| Required content | Marker from injected method returned in final event | Delivery witness, not correct method execution |
| Optional resource | Exact generated helper command event, exit 0, unknown-in-prompt reference token in output | Self-report or command mention does not count |
| Source/package/control write denied | Actual helper receives EPERM/EACCES/EROFS; parent confirms original bytes | Not-found, missing Python or no attempt stays unverified |
| Outside-root read denied | Actual helper permission failure, not model refusal | Successful non-secret sentinel read is a demonstrated failure |
| Unbound repo/user method isolation | Canary influencing final output demonstrates failure | Canary absence NEVER establishes isolation |
| Admin/system discovery | Unverified | Requires disposable OS inventory/adapter evidence; probe never writes /etc |
| Loopback network denied | Actual generated helper permission failure | Timeout/refusal is inconclusive; allowed response demonstrates failure |
| Arbitrary network denied | Unverified | Loopback alone cannot prove egress policy |
| Result return | Final CLI agent-message event | Not a valid/approved review by itself |
| Fresh-context rehydration | Same pinned reference observed in two fresh invocations | Not arbitrary crash recovery or compaction proof |
| Fixture integrity | Parent safely compares the original manifest | Modified or missing originals fail; cannot certify the whole host |

Unknown event schemas, failed/partial invocations and generic shell errors stay
unverified. The parser accepts only completed command events for the exact helper,
not arbitrary strings that happen to mention a path. Unsupported observations
are sticky. It does not ingest model-written "PASS" fields.

**The S0 probe intentionally cannot produce a full strict-mode PASS by itself.**
Admin/system inventory, arbitrary network isolation and production adapter effects
still require #440's environment evidence and #445's implementation. The report
retains `strict_binding_supported: false` until all declared capabilities have
real support; there is no override or approve-all option. No product code consumes
this flag to grant permissions.

`cli_execution_attempts` is not a provider-request or dollar count. Provider
requests are unknown after a live CLI attempt; cost remains unknown. The report
contains binary/help digests, detected version, platform, input/transcript digests
and per-context observations. Same input hashes do not promise deterministic
model output. Bundled methods read as source data are distinct from methods
activated as instructions; neither this probe nor directory isolation eliminates
prompt-injection risk.

## Next integration points

- #444: pin methods at the bounded task boundary, not each later Reviewer job;
  inherit snapshot, original input and read-only policy on retry.
- #445: controlled prompt/resource delivery in WorkerJobStore; isolate all native
  discovery sources without deleting user installations; constrain reads as well
  as writes with the actual OS/Runtime boundary.
- #446: remove default professional methods from all prompt/profile sources when
  the user replaces them; retain only the thin Role/output protocol.

## Verification status of this change

The accompanying tests cover parsing, consent, clean environment, sentinel
integrity, fake-event rejection, owned process timeout, actual disposable helper
I/O and loopback lifecycle. Simulated CLI events are explicitly test fixtures,
not live Codex conformance. Real model tests have **not** been run by this change.
Keep #440's real-environment acceptance and #445's activation gate open until
separate authorized evidence exists. Do not close #449 or #411 from this probe.
