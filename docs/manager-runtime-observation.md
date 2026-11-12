# Running-period Runtime observation

Manager ordinary and running-task conversations now enrich their existing scoped
Worker invocation records with a fresh read-only local session-status snapshot.
Only supported persistent-session Runtime current attempts are inspected. Old
recovery attempts, unrelated jobs and terminal history are not probed. The
control-target path does not opt into this observation.

Evidence distinguishes the saved job state from local process/heartbeat health,
streaming/compaction metadata and pending-message count. The observation carries
a timestamp, source and explicit interpretation. Missing, denied or malformed
session observations are unavailable, not proof of death or failure. No fallback
bypasses protected Runtime admission. Paths, provider/session identities, raw
transcripts and command payloads are excluded.

This uses session_status, not control/RPC enqueue, retry, dispatch or a paid model
call. It does not mutate task, continuation, authorization or delivery state.
Existing Manager conversation execution and resource admission remain unchanged.

This is a narrow #686/#700 prerequisite, not autonomous supervision acceptance:
there is no timer, automatic Manager wakeup, structured action selection, Worker
reply attribution or automatic resend. A healthy local process is not proof of
useful progress; a control ack is not a reply. #419 still needs the real continuous
project path, including no-seed feedback, and protected Runtime limitations remain.
