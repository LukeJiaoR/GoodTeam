# Role memory file-store consistency

`RoleMemoryStore` remains the compatibility facade. `memory_storage.py` contains
only filesystem, locking, size-limit and atomic-publication primitives. Event
identity, active-event selection, retrieval and bounded context policy remain in
the facade; no new database or model dependency is required.

For ordinary Worker capture, preparation, per-call transport observations and
writeback event references, see [Worker memory observations](worker-memory-observations.md).

## Commit and read contract

All cooperating GoodTeam processes use the same stable `.store.lock` inode in a
role directory. A reentrant thread lock and POSIX `flock` cover deduplication,
WAL commit, checkpoint publication and derived-file repair. Locks are independent
across roles. They are never replaced or unlinked during normal operation.
A read of an absent role returns a pure empty view at the absence observation;
it does not create a role directory or lock. Concurrent first initialization
may occur after that observation, but the reader never proceeds to unlocked
file reads. Reads of existing roles acquire the stable lock, creating only the
lock leaf when upgrading an existing legacy role. `read_memory_index` repairs
existing roles; on an absent role it now returns an empty index without the old
initialization side effect. Explicit ensure/append/refresh/rebuild operations
remain the initialization paths.
Forked children discard inherited Python ownership and lock descriptors.

1. Under the role lock, read one validated WAL snapshot and check every available
   historical prefix proof. Reject malformed, lost or rolled-back history.
2. Persist an independent `checkpoint.json` for that verified prefix. This also
   establishes an empty checkpoint before the first WAL commit.
3. Deduplicate the request against the WAL. For a duplicate, repair incomplete
   publication and return the original event, including its original timestamp.
4. Write the complete old WAL bytes plus one new JSON line to a same-directory
   temporary file, flush and fsync it, atomically replace `events.jsonl`, and
   fsync the directory. Existing WAL bytes are preserved, not reserialized.
5. Advance the checkpoint, repair the compatibility markdown without deleting
   existing bytes, and publish the index last. Each file uses atomic replace.

The WAL replacement is the process-crash commit point. Successful return also
requires publication and fsync completion. An error or process loss after the
WAL replacement can mean the event committed without an acknowledgement; retrying
the same role/horizon/action/source/text/supersedes request returns it once.
This is not a multi-file filesystem transaction or an external-provider
exactly-once guarantee. Hardware/power-loss durability depends on the filesystem
honoring file and directory fsync and atomic rename.

All event readers, including browse, lookup, execution provenance and retrieval,
use the validated snapshot entrypoint. Prompt capture reads WAL and legacy
sources under the role lock. Model/embedding work runs outside the lock; before
returning context or ranked results the current revision is checked again. A
concurrent change returns an explicit unavailable/retry outcome rather than
emitting stale results. Frozen packets still only retain original active IDs;
validation does not add later facts or rewrite historical capture files.

The optional embedding cache is derived data. It is merged and atomically
published under the role lock after model computation; it does not authorize
memory events. No embedding model is installed or invoked implicitly by this
storage change.

## Checkpoints and migration

`memory_checkpoint.v1` records the event count, exact WAL byte length and SHA-256
of a verified committed prefix. The index is a separate, disposable projection.
Both available index evidence and the independent checkpoint must match a
prefix of the current WAL; ordinary appends are allowed. A newer checkpoint
still catches rollback when the index is lost or corrupt.

- A valid pre-upgrade index provides the first prefix proof. Maintenance or the
  next append creates the checkpoint under the same lock, without rewriting
  historical WAL bytes or removing notes.
- A genuinely new or legacy-notes-only role with no evidence of prior events
  can establish an empty checkpoint normally.
- A pre-upgrade nonempty WAL with neither a valid index nor a checkpoint is
  `history_unverifiable`. It is not automatically imported or certified by
  rebuilding its index. A corrupt independent checkpoint also fails closed.
- Restore the WAL and available proof files from a known matching backup when
  possible. Otherwise retain the original files for inspection and obtain an
  explicit decision to import their contents through a separately validated
  recovery/migration procedure. This change does not implement or silently
  approve that import. Deleting checkpoints to silence the error is not repair.

The Worker prompt and overview API expose unavailable status and stable reason
codes. Event readers raise a safe storage/revision error. Execution evidence
continues to expose its ledger references with `complete=false` and explicit
memory-unavailable status; an unavailable memory source does not erase the
execution ledger or become a valid reference.

Checkpoints are integrity evidence, not signatures. Rolling back the WAL and
all historical proof files together cannot be detected by this local scheme.
Uncooperative external writers do not participate in its locking contract.

## Recovery states

| Interrupted phase | Authoritative state | Recovery |
| --- | --- | --- |
| Before WAL replacement, including a fully written temporary file | Old WAL and old checkpoint | Ignore temporary files; request can append once |
| After WAL replacement, before checkpoint advance | New WAL preserving the old checkpoint prefix | Validate and advance checkpoint; replay publication |
| After checkpoint advance, before markdown/index | New WAL and new checkpoint | Repair derived views |
| During a derived-file replacement | New WAL; old or new whole derived file | Repair under the same role lock |
| WAL missing, truncated, malformed or contradicting a retained proof | History unavailable | Preserve evidence; do not rebuild to certify it |

Normal interrupted writes do not require a pending-intent journal: the retained
checkpoint is a committed prefix, and complete atomic WAL bytes distinguish
whether an append reached its commit point. Crash-left `.pending-*` files are
never used as source data; automatic orphan cleanup is not part of this change.

## Markdown and index compatibility

Markdown files mix handwritten and generated text without sufficient provenance
to safely delete selected historical bytes. `rebuild_role_memory` now repairs
rather than clears those files. It adds missing active event entries and retains
existing notes and historical event text. It does not claim to have removed
superseded wording from the human-viewable file.

The returned index reports `projection_policy=wal_generated_legacy_preserved`.
Machine summaries are bounded, derived from active WAL events and labeled with
`source_kind=wal_generated`, dependency event IDs and a summary digest. The
existing file hash continues to describe the legacy markdown bytes. Reading
legacy markdown explicitly labels it `legacy_mixed` and `machine_context=false`.
After a correction, mixed legacy summaries remain withheld from Worker context;
valid active WAL context and its partial-status explanation are preserved.

## Limits and evidence

This backend requires POSIX anchored filesystem operations and `flock`.
Unsupported platforms fail closed with `memory_storage_unsupported`; there is no
unlocked Windows fallback. Directory traversal and leaf opens reject symlinks,
non-regular files and unsafe hard links through the existing anchored filesystem
primitives. This is not a promise of protection against a privileged process
replacing lock files or filesystem components.

The WAL and embedding cache are limited to 32 MiB each; each markdown/index file
is limited to 8 MiB. A limit produces a safe error, not truncation. Since WAL
commit precedes projection publication, a projection-size error may require
repair after an already committed event. Existing oversized files are retained
but unavailable to this backend. Prompt budgets remain independent and bounded.

Synthetic local measurements with 8, 64 and 248 large historical records (a
superseding chain with one active record) used approximately 1, 8 and 31 MiB
WALs. End-to-end capture took 15, 146 and 413 ms; append plus publication took
37, 205 and 798 ms in that run. These are one-machine
measurements, not throughput or durability guarantees. Whole-WAL rewrite and
validation are O(WAL size); a transactional backend/compaction policy is future
work rather than hidden behind an unbounded file implementation.

Regression evidence includes two real writer processes and a reader, same-event
deduplication, role independence, five forced-exit boundaries, rollback with a
missing/corrupt index, legacy migration, malformed inputs, unsafe paths, bounded
reads/writes, and a concurrent correction during lock-free ranking. All fixtures
are synthetic. They do not exercise a paid model, real provider runtime, power
failure, network filesystem locking or live browser behavior.
