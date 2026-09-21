# Missing task Skill registry

The ownership index is authoritative; immutable objects alone are not command
receipts. If `index.json` is absent while the namespace's `objects` directory has
any entries, all task-lock reads, starts, job snapshot derivations and preflight
checks fail with `snapshot_missing`. A new service instance does not change that.
No current Team resolver is called, no new index is fabricated, and existing
objects and package retention pins are preserved.

This includes an interrupted first prepare that published an object but never
published its ownership index. The service cannot distinguish that orphan from
a previously committed index that was lost. It therefore blocks rather than
adopting the orphan or recapturing the latest configuration. There were no pins
acquired before the first prepare index; this is not a runnable half-snapshot.
Preparing entries whose index **does** survive retain the existing deterministic
pin/commit retry protocol and do not re-resolve project settings.

Recovery requires trusted, owner-matching control-plane metadata from a complete
backup or an explicit operator repair outside this API. Do not delete retained
objects or pins, manufacture an empty index, or copy another owner's index as a
workaround. There is no automatic repair or GC in S4a. Entire storage loss cannot
be repaired from a client-held reference; resource/schema/digest verification
remains mandatory in S4b (#461) and Runtime (#445).

`tests/test_task_skill_registry_loss.py` checks read, late snapshot, preflight,
same-task replay and new-task start after index loss, including service restart
and preserved bytes/pins. A separate interruption case checks ambiguous orphan
handling. These are foundation tests, not evidence that production dispatch has
been connected. The overall delivery boundary is in `task-skill-locks.md`.
