# Manager-specific Pi coordination contract

## Production path

New Pi Manager calls from the existing registered-project producer capture
`manager_pi_methods.v1`, original `manager_message` identity and the existing
Manager Role method packages. Ordinary chat, project judgment and recovery use
the same producer/WorkerJobStore path. Legacy jobs and already captured
markerless locks retain their original contract; a custom-empty Role is not
silently replaced by defaults.

The original Pi session Runtime selects this contract only for the verified
Manager method input. It exposes only `goodteam_skill_resource`, which reads
resources from this invocation's bound method package. Project Context/status
arrive in the existing immutable input; coordination decisions return through
existing structured actions and their host authorization checks. Manager has no
project `read`, `grep`, `find`, `ls`, `bash`, file-writing, web-search or MCP tool.
Researcher/Planner and ordinary development Worker permissions are not changed.
Historical generic Pi read-only tasks are not silently activated.

The production command disables automatic MCP, extensions, skills, templates,
context files and approval tools; fixed system/append prompts prevent independent
SYSTEM/APPEND_SYSTEM discovery. Only the host's two explicit extensions are
loaded. The extension checks exact available and active tool sets and sends a
nonce-bound witness over the original authenticated method resource channel.
The host requires it before the first model prompt. A tool-call hook blocks any
other tool and blocks even method-resource calls before witnessing.

The RPC transport independently enforces an allowlist. Shell/export/session
switch/model change/compaction commands cannot bypass the model tool surface.
Every model-input command also rechecks the current method authority. Stop
invalidates the witness; abort/clear-queue remain available to stop the same job.
This is a tool-level role boundary, **not an OS filesystem sandbox**, confidential
path isolation, or evidence that a model followed a method.

## Restart and identity

Manager recovery loads the exact original session with `--session` into a fresh
process. The original session header's identity and cwd must match the captured
job, and metadata must retain the exact tool contract. Every process gets a new
nonce and must supply a fresh witness. No same-process `switch_session` can
reuse a ready bit. Original settled-operation detection prevents a second
initial prompt; ambiguous prior operations retain the original refusal.

Official source inspected: earendil-works/pi v1.1.0, commit
abe508e1b89912adde45528136c3221eb69acdd7. The CLI supports a replacement `--tools`
allowlist and `--session`; MCP is a separate tool-selection exception and is
therefore explicitly disabled. Session-header cwd affects Pi resource loading,
so it is verified before restoration rather than inferred from the file path.

## Evidence and limits

Tests exercise the real registered producer, task capture, Role packages,
WorkerJobStore, method broker, Pi RPC subprocess and original restore lifecycle.
The subprocess is the repository's offline Pi double; listener startup and
witness are supplied test seams. The actual extension module also runs under
Node with a small SDK double. These show production wiring and rejection
behavior, not that an installed Pi SDK loaded the extension.

No real Pi installation or completed Provider invocation is claimed. A public-entry
fixture initially routed to the installed Codex CLI, which failed during
thread/start before establishing a session; the failure and the added strict
fixture-only transport guards are retained in validation. No bypass was attempted.
Real SDK tool registration, model behavior and continuous project acceptance
remain to be verified. No install, permissions change, push, merge or deployment
occurred. The old generic read-only/strict Worker boundaries remain in place.

Final validation: 19 focused tests and Ruff passed after the fixture-only
transport guards. Independent review passed 20 checks, including original
Worker regressions and malformed restore headers. Full backend: 532 passed /
32 failed / 5 errors / 21 skipped, exactly the same 37 failure/error identities
as the 523-pass live-human baseline. The complete suite is not green.

Remaining production boundary: the running-observation-specific entry retains
its original pipe-only Stop/launch handshake and still rejects session/Skill
transports. This patch does not remove that gate. Connecting it to Pi requires
linearizing the existing live authorization with the first model RPC write,
not merely process startup. Ordinary Manager conversation/project judgments
and their captured invocation restore are the paths delivered here; full
supervision and real continuous acceptance are not claimed complete.
