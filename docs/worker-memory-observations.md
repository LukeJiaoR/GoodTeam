# Ordinary Worker memory observations

Tracked by [#534](https://github.com/LukeJiaoR/GoodTeam/issues/534).
This is a retained metadata contract for newly created ordinary Worker jobs.
The existing delivery-summary API exposes a bounded read-only projection for
the exact attempt inspector. It introduces no provider acknowledgement, new
retrieval policy, or adapter permission.
Protected task-input, correction and Skill authority remain separate protocols.

## What each observation establishes

- Capture binds the persisted pickup-packet digest to the job, run, role and
  project scope. Its timestamp records capture, not job creation or dispatch.
- Preparation validates the packet once and uses that same value for the
  rendered memory fragment and its references. It records the exact fragment
  and complete prompt digests, validation metadata, and preparation time.
- Each transport entry has a fresh identity and references that preparation.
  The record is persisted before entering the existing CLI/Pi call. It means
  the host reached a call boundary, not that a provider received or read bytes.
- Writeback records the result-byte digest, observation time, and the actual
  append event IDs and event timestamps when known. The event timestamp is
  distinct from the later host observation time, including on replay.
  A prepared/transport reference is attached only
  when that execution path actually returned it.

Provider receipt and provider read remain `unknown`. A no-op builtin job does
not acquire a fabricated transport entry. A settled Pi session that does not
send a prompt does not acquire one either. Existing CLI retries have separate
transport identities even though their job ID is unchanged.

The original capture digest and actual preparation packet digest can differ.
That difference is recorded rather than presented as the original packet.
Existing validation can remove superseded context; it cannot add new facts to
the frozen packet. Summary dependency IDs describe the summary source set and
do not prove that every event's full text was rendered or read.

## Storage and failure boundary

`memory_handoff.py` stores bounded metadata in operator state, keyed by hashes
of the canonical project root and job ID. It uses the existing anchored,
non-symlink-following file primitives and per-job locking. Operator state must
be outside the project root, matching the containment requirement already used
by protected task snapshots. Ordinary jobs configured with operator state inside
the project must move that state through an explicit operator decision; this
change neither changes permissions nor performs such migration.

Enrollment precedes job publication. Capture and receipt-history proof files
prevent retained partial state from being treated as legacy history. A missing,
corrupt or mismatched retained proof fails closed. Failure to persist required
pre-dispatch metadata stops that call. These failures are not provider transient
errors and must not trigger a duplicate external call. A receipt write failure
after memory append preserves known writeback counts and does not rerun a
completed Worker.

The metadata ledger is capped at 1 MiB and 1,024 entries per job and is not
silently truncated. Capture/proof reads are capped at 16 KiB. It has no automatic
repair/import API. Preserve incomplete files for diagnosis rather than deleting
proof files to silence errors. Local proof files are not signatures: losing or
rolling back all enrollment and proof together cannot be detected by this
scheme. Unenrolled historical jobs remain unknown and are not backfilled.

Metadata contains bounded identities, digests, schema/version, times, known
status/reason enums and references from the validated packet or actual WAL
events. It does not copy memory text,
prompts, result text, task text, free-form source paths, exception messages,
credentials or provider session material into the observation store. Existing
job/status JSON shapes gain no internal receipt IDs or raw context.

## Exact-attempt Work display

`goodteam.attempt-memory.v1` is attached only to the server-selected attempt in
`delivery_summary.attempts`. The project root, project key when present, run,
job ID, role, adapter and optional execution ID must agree with retained proof.
All receipt relationships and the complete bounded history are validated before
the projection retains the latest eight preparations and eight writebacks, with
explicit omitted counts. It never looks up the latest record for a role.

The reader uses anchored, read-only opens and the existing lock inode; GET does
not create locks/directories, enroll old jobs, repair proof or write memory.
Unenrolled jobs and protected task-input/correction/Skill protocols display
unknown. Incomplete or mismatched retained proof displays unavailable.

The UI shows host preparation counts/times/digests, captured versus prepared
packet match, source/citation counts, call-entry count, and separate result and
audit-provenance writebacks. Appended events, replayed own-source events and
other-source text deduplication remain distinct. Incomplete counts are lower
bounds; omitted observations are visible. This does not measure memory usage,
provider receipt, provider reads or comprehension. Raw memory/prompt/result
content and private store paths are never included in this projection.

## WAL writeback outcomes

`memory_writeback.py` observes the existing role-wide text-deduplication policy
while holding the role lock across deduplication, append and failure observation.
It retains the event returned by a successful append. If append raises, one
bounded read compares the exact verified pre-WAL prefix with the resulting WAL.
One matching appended event establishes an observed WAL commit even when
checkpoint or projection publication failed. Other ambiguous outcomes stay
unknown; the helper does not retry them.

- New commit: the current call appended that event.
- Same-source text replay: the existing event belongs to this job's source;
  no new append or projection repair is attributed to the replay.
- Other-source text deduplication: another source already contains that text;
  its event is referenced as a duplicate, never as a new event from this job.

Requested horizon and actual referenced-event horizon remain distinct when
text-only deduplication crosses horizons. `counts_complete=false` means known
counts are lower bounds, not evidence that an uncertain append wrote nothing.
Writeback observations are capped at 128; a digest and omitted count cover entries
beyond the retained metadata limit without changing legacy result parsing.

The WAL replacement and derived projection completion remain separate facts.
An observed replacement after an fsync error is not a power-loss durability
guarantee. This is not an exactly-once external execution protocol.

## Unchanged input policy and verification limits

This slice does not inject the packet's `short_term` bucket into prompts.
Existing relevant-event retrieval can still select short-term events. Direct
short-term injection needs its own budget, expiry, deduplication and safety
policy before changing Worker behavior.

Isolated local tests exercise the actual preparation, subprocess boundary,
writeback, storage-failure and retry functions with synthetic data. They do not
prove any real provider read memory, demonstrate paid model quality, or complete
the parked multi-task/multi-project runtime acceptance.
