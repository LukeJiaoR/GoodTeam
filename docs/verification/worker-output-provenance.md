# Invocation-bound Worker output (#700)

Project outcome: a Manager can incorporate new Worker output while its original
confirmed batch is still active, rather than seeing only process liveness.

The existing Pi execution loop reacts to `turn_end` by taking a bounded local
snapshot of the Runtime's persisted session JSONL. It validates the configured
session root and matching session header, imports only complete records, and
retains stable entry IDs in the original `pi_entries.jsonl`. It sends no new RPC
and performs no model request. Failed observation does not resend a prompt or
change execution status. Final RPC entry replay retains its own cursor; metadata
updates use the existing terminal-event lock.

Each imported record is stamped with its original job, run/execution attempt,
and session digest. The existing invocation Context and running-observation
packet expose up to three assistant-text entries (2,000 characters each).
Thinking, tool output, raw session identity and provider configuration are not
projected. Output is untrusted project evidence, not instructions or authority.
New stable output can reach the existing bounded Manager judgment; duplicate
replay does not change its semantic wakeup key. No new scheduler is introduced.

An output belongs to an invocation, **not to a specific steer/follow-up command**.
RPC acceptance, output receipt, task completion and reviewed delivery remain
separate. Cross-job resumed session output, legacy entries without provenance,
foreign-session entries and superseded attempts are not attributed to the current
invocation. Late output may remain readable history but never revives a stopped
grant. Snapshots above 2 MB fail closed rather than ingesting an unbounded transcript.

## Verification boundaries

Offline tests exercise original persistence -> invocation Context -> existing
running observation -> injected bounded Manager judgment, stable replay,
identity mismatches, partial tails, path boundaries and Stop. These are fixtures,
not paid Provider, real Pi session or full project Loop acceptance. Those remain
open under #700/#419. CI remains paused.
