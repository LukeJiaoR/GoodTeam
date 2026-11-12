# Read-only Researcher / Planner methods through the original Pi Runtime

## Delivered production path
New registered-project Researcher and Planner read_only tracks now capture their
actual Role packages through original TaskSkill capture, per-track snapshots and
Worker jobs. A dispatch_track_methods.v2 receipt records readonly_pi_methods.v1;
old requests and v1 routes are not upgraded. Target Role, task/track identities,
original grant and read-only execution policy remain unchanged. Actual project
work still goes through the existing queue/dispatcher, not a new Role runner.

This connects the earlier pi-read-only preparation using the now-established
Manager Runtime startup/restore witness mechanism. It does not copy Manager's
coordination-only role to Workers. Manager's manager_tools flag and policy remain
separate; Researcher/Planner can use read, grep, find, ls and bound Skill resource
reads to perform their bounded professional task.

The production CLI disables automatic extensions, MCP, discovered Skills, prompt
and context files, and fixes the tool registry. A trusted extension refuses tool
calls before an authenticated exact active/available-tool witness; the host waits
for that fresh nonce before model prompts. The same Runtime checks every RPC
against the allowlist and current method/task binding. Shell RPC, file-write tools,
model/session switching, compact/export and unapproved Search are not enabled.
Each restored process requires a fresh policy witness and the original session
header and exact read-only tool contract; it cannot switch to another job.
Code Review, existing strict jobs and historical read_only Pi jobs retain their
prior route/refusal. No permission or billing allowance is created by a Skill.

## Exact guarantee
This is **tool-level read-only, not an OS filesystem sandbox**. read/find/grep/ls
are not a confidential-path or project-directory confinement boundary. Pi's own
session storage remains Runtime I/O. --offline does not establish network
isolation. The nonce is startup correlation, not attestation against a malicious
local process. Project workspace preparation and original task authorization
checks remain; no actual Git trust rejection was bypassed. Do not claim stricter
protection or a successful installed Pi run from the deterministic tests.

## Verification
- New producer representative captures actual Researcher and Planner packages,
  exact immutable job attribution and private task contexts; rejects writable,
  wrong-role, Search, review and cross-session combinations; old read_only jobs
  remain unsupported. Prior source fails that representative (missing mode).
- Production producer/store/broker/Runtime exercised with a fixed offline fake
  RPC subprocess: prompt, settlement, original session restoration with fresh
  nonce, no duplicate prompt, and missing witness rejection. Workspace mapping
  and listener startup are explicit fixtures, not real Git/Pi acceptance.
- Actual extension module exercised under Node with SDK/fetch doubles: pre-witness
  tool rejection, exact registry and forbidden tools. No paid model or search.
- 14 related Manager/ordinary/mixed regressions passed; 3 producer/Runtime and 3
  policy/Node checks passed; Ruff passed. Independent review reran 5 Runtime/policy
  checks and found no blocker, while documenting all fixture boundaries.

Base full mixed-methods archive SHA256:
4f166165c26e1ad9ba428b5d96f977abd5d917282372747adb28b9e1e2deb03f.
No install, runtime setting/security change, push, PR, merge or deployment.
Real installed Pi SDK extension loading, observed real tool behavior/model
judgment and complete project-loop acceptance remain unrun under #419/#630.

Final full backend: 558 passed, 32 failed, 5 errors, 21 skipped. Exact 37 failure/error identities match the immediately preceding mixed-methods baseline (555 passed). The suite is not green; original environment/baseline failures remain.
