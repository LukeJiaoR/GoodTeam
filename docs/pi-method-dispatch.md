# Ordinary Role methods through retained dispatch workspaces (#445)

This increment connects the original `dispatch_tracks` / `_create_track_job`
producer to the ordinary Pi method consumer. It does not introduce a new pool,
Loop, task ledger, or permission mode.

For a newly confirmed task containing only ordinary standard Pi / pi-rpc tracks,
the existing task-start confirmation captures `ordinary_pi_methods.v1`. The
existing Role binding and method snapshot supply the content. Noncustom tasks
remain on the original legacy route; no loaded method is invented. A historical
confirmation without this marker remains strict, including a task interrupted
before its first Worker job was created. Read-only and Code Review tasks are not
converted into standard writing tasks.

The original dispatcher prepares its retained isolated Git workspace and passes
the same run/job/workspace mapping. A method request records that it requires this
workspace. The consumer validates the original mapping with the existing retained
workspace service, pins it in the original operator request storage, and launches
Pi there. Missing workspace, another job/run/path, changed mapping on replay, or a
direct Pi Runtime constructed with a different cwd is rejected before RPC. The
immutable Worker request is not rewritten. Status and Stop retain their original
identity-only path even when the method or worktree can no longer be used.

A newly published Skill job already has a queued status; the existing dispatcher
now executes that queued job rather than treating it as a saved result. Running
or terminal attempts retain the original recovery behavior. Worker results,
verification, file attribution, integration and write-domain checks remain owned
by the same dispatcher. A worktree is not an OS sandbox and no stronger isolation
claim is made.

## Verification boundaries

- Real registration, Team binding, task-start capture, Worker publication and
  dispatch selection were exercised through the original public producer.
- A representative retained-mapping fixture verifies the exact run/job/path,
  immutable workspace receipt and direct Runtime cwd rejection. This fixture
  does not prove Git baseline capture or process confinement.
- The real retained-Git / fake-Pi subprocess integration case was attempted and
  hit the existing `Trusted Git executable was not available.` refusal before
  Runtime startup. The baseline isolation test fails at the same check. The
  integration case reports NOT RUN / skipped on this exact environmental
  refusal; unexpected errors remain failures. The trust gate was not changed.
- The preceding ordinary method consumer's fake-process tests remain separate
  from this retained-Git integration requirement. No paid provider, real Pi,
  browser, production deployment or full project Loop acceptance was performed.
