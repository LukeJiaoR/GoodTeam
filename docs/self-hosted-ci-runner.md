# Self-hosted CI runner

Status: Phase 1 implementation for issue #393
Owner: repository maintainers

## Decision and security boundary

Routine pull-request CI uses one repository-scoped Linux x64 runner:

| Property | Value |
| --- | --- |
| Host | `186.241.77.137` (shared with YUVA CI) |
| Runner | `goodteam-ci-01` |
| Service account | `goodteam-ci` (unprivileged; no `sudo` or `docker` group) |
| Install directory | `/data/goodteam-ci/actions-runner` |
| Work directory | `/data/goodteam-ci/actions-runner/_work` |
| Selector | `[self-hosted, linux, x64, goodteam-ci]` |
| Parallel GoodTeam jobs | One; the single runner queues jobs naturally |

The label is a workload class, not a machine identity. The runner is registered
only to `LukeJiaoR/GoodTeam`. Its account and home contain no provider,
production, deployment, repository-write, or OpenWiki credentials. Routine CI
has only `contents: read`, does not select a GitHub Environment, and has no
automatic GitHub-hosted fallback.

The first self-hosted job accepts a pull request only when its head repository
is the current repository. Browser acceptance depends on that guarded job;
frontend checks share the browser job's install/build, the backend waits for
that job, and
the always-running gate repeats the same-repository guard. A fork PR therefore
cannot schedule code on this persistent host; a maintainer must first put the
reviewed patch on a same-repository branch. The workflow does not use
`pull_request_target`.

OpenWiki remains on `ubuntu-latest`. It is manual-only because it has provider
secrets and repository/PR write permission, and those capabilities must never
reach `goodteam-ci`.

## Choosing a runner for new workflows

These rules also apply to temporary workflows on feature or diagnostic branches:

| Workload | Runner and permissions |
| --- | --- |
| Routine tests, tracked-source archives, read-only inspection, public dependency downloads | `[self-hosted, linux, x64, goodteam-ci]`; explicit read-only permissions, no hosted fallback |
| OpenWiki | `ubuntu-latest`, manual-only; existing provider secrets and PR-write permissions stay here |
| New tasks needing provider secrets, a GitHub Environment, repository writes, or deployment access | A separately reviewed isolated environment; do not use `goodteam-ci` |
| Native ARM64 or Docker inspection | A matching separately provisioned environment; the existing x64 account cannot run these checks |

No privileged self-hosted runner is currently provisioned. An additional label
on the existing runner is not isolation. Provision and review a separate VM or
an environment rebuilt after each job before migrating privileged work. Keep
permissions explicit at workflow and job level; job overrides must not grant
write access on `goodteam-ci`. Source-only checkout must set
`persist-credentials: false` and must not execute code from the inspected ref.

`tests/test_ci_workflow.py` checks every committed workflow for the fixed
read-only runner boundary. OpenWiki is the only hosted exception. A future
runner exception needs an explicit policy/test update alongside its isolation
and provisioning evidence. These checks run in PR CI when workflow files change;
they are not GitHub-side enforcement for arbitrary branch pushes. Agents must
validate temporary workflows locally before pushing them and follow the same
policy even when the branch will never become a PR.

### Read-only source diagnostic

Use **Read-only source diagnostic** instead of creating a temporary workflow
just to export source. Dispatch the workflow from `main`; `source_ref` selects
a branch, tag, or exact commit in this repository. Prefer an exact commit when
capturing review evidence. An empty input uses the workflow's own commit.

```bash
gh workflow run source-diagnostic.yml --repo LukeJiaoR/GoodTeam --ref main \
  -f 'source_ref=<commit-sha>'
```

The workflow accepts dispatches only from the default branch, checks out the
selected source without persisting credentials, and runs a fixed inline archive
script. It does not install dependencies or execute repository scripts. Each run
uses its own directory under `RUNNER_TEMP` and uploads a seven-day artifact:
tracked-source tar, resolved commit/tree IDs, tracked-file manifest, SHA-256,
and a statement of the export's scope. Untracked workspace files, `.git`, and
runner caches are not included. This is source transport, not test or runtime
acceptance evidence. It shares the single CI runner and may queue behind PR CI.

Before pushing any new or temporary workflow, run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_ci_workflow.py tests/test_source_diagnostic.py -q
```

## Shared-host capacity boundary

`goodteam-ci-01` and `yuva-ci-01` are separate repository-scoped services and
separate Unix accounts. Both services are assigned to
`goodteam-shared-ci.slice`, which applies aggregate limits across repositories:

- `CPUQuota=600%` (six of the host's eight cores in total);
- `MemoryHigh=6G` and `MemoryMax=7G` across both runner services;
- `MemorySwapMax=8G` and `TasksMax=4096`.

This bounds unsafe cross-repository concurrency while allowing either idle
runner's capacity to be used by the other. Do not install a second GoodTeam
runner service or raise these limits without measuring simultaneous YUVA and
GoodTeam jobs. The host's system Docker daemon is not available to the
GoodTeam account; GoodTeam CI does not require Docker or a database. The browser job runs
headless Chromium against an isolated temporary workspace with fake Workers.
Frontend unit tests/lint run after browser acceptance in the same job when the
frontend is affected. The backend starts only after that job succeeds. HTTP
recovery runs once in the browser job and is excluded from the later backend run.

## Dependencies and caches

The host provides Git, a checksum-pinned CPython 3.12.11 build, npm, and standard
Python build tooling. Each backend job verifies Python 3.12 and creates a fresh
virtual environment in `RUNNER_TEMP`; `actions/setup-node` installs/selects
Node 20 in the runner tool cache. The PR job logs are the runtime-version proof.
GitHub-backed dependency caches are disabled. Persistent runner-local pip and
npm caches remain available while reproducibility stays anchored by
`pip install -e ".[dev]"` and `npm ci`.

Browser jobs create their own Python environment and use the lockfile-pinned
Playwright version to download Chromium into the unprivileged runner cache.
Chromium system libraries must be available on the host; CI does not run sudo
or install system packages. The browser suite refuses an existing server, uses
a fresh workspace/registry/port, runs every case once without automatic
retries, and uploads report/state/trace evidence for 14 days.

The lifecycle implementation in `ops/ci-runner/runner_cleanup.py` replaces the
old broad `RUNNER_TEMP` sweep and the home-cache `find -delete` calls. **Existing
runners require the hook update below; merging the PR does not install it.**
Until that update, CI retains its `RUNNER_TEMP` fallback and the old installed
hook. The checked-out command guard already works without hook installation.

### Resource ownership and recovery

Both hooks validate the dedicated account's runner registration (`agentName`
and repository URL), reject symlinked roots, and serialize through a nonblocking
lock. The start hook creates `/data/goodteam-ci/.ci-lifecycle/jobs/<random-id>/`
with a repository/runner/UID marker and the worker PID, Linux process start time,
boot ID, run ID, attempt, and job name. It exports job-local `TMPDIR`, Python
environment paths, evidence paths, and dependency-cache paths via `GITHUB_ENV`.
The Playwright fixture already honors `TMPDIR` through `os.tmpdir()` and removes
its own workspace on normal server shutdown.

| Resource | Reclamation owner and policy |
| --- | --- |
| Marked job `tmp/`: virtual environments, Python/Node temp files, browser workspace | Completion hook after workflow artifact uploads, regardless of result. Active references prevent deletion. |
| Missed marked job temporary resources after worker/host termination | Next start/completion boundary, after 24 hours, matching worker no longer exists, and no process references the directory. A recorded completion permits earlier retry once the worker exits. PID reuse and reboot are distinguished. |
| HTTP recovery evidence in marked job `evidence/` | Preserve locally for 14 days, including when upload fails. GitHub artifact retention remains 14 days. |
| Browser HTML/JSON reports, traces, screenshots in the checkout | Existing `always()` artifact upload owns durable retention (14 days); `actions/checkout` owns workspace cleanup. These are outside the helper's deletion scope. |
| Marked pip/npm/Playwright caches | Regenerable downloads only; aggregate 4 GiB boundary cap, oldest whole cache evicted first; each cache generation expires after 30 days. Whole-cache eviction avoids partial browser/package formats. Live jobs/references prevent eviction. |
| GoodTeam runner `_diag/Runner_*-utc.log` and `Worker_*-utc.log` | Registered GoodTeam runner path, matching generated filename and UID only. Closed logs expire after 14 days; 512 MiB budget warns if protected/unknown logs still exceed it. |
| Runner's own `RUNNER_TEMP`, tracked-source export, action internals | [Actions Runner's native temp manager](https://github.com/actions/runner/blob/v2.337.0/src/Runner.Worker/TempDirectoryManager.cs); the custom hook does not sweep this directory. |
| Checkout dependencies/build outputs and Actions Node tool cache | Checkout and Actions own these; this helper does not delete them. |
| Containers, networks, volumes, business databases, other services | GoodTeam CI creates none and has no Docker access. This helper makes no Docker calls and does not claim to reclaim these resources. |

Markers must have been created by this lifecycle; existing unknown directories
and legacy home pip/npm/Playwright caches are never adopted or automatically
deleted. Recursive removal refuses foreign owners, filesystem boundaries, and
mounts, and uses Python's symlink-safe deletion. In-use checks inspect fds, cwd,
executables, mapped files, and surviving job children's temp/cache environment
paths; checks are refreshed at deletion. Guarded commands reap their own
descendants before returning. Native Actions Runner still owns orphan cleanup
for unguarded actions after the completion hook; resources referenced by those
processes are retained for a later safe boundary. If a process cannot be inspected, cleanup fails closed;
it cannot prove that the resource is idle. Completion failures/timeouts emit a
warning and do not replace the original job result. Recovery needs another job
boundary; there is no timer or idle-host janitor.

### Capacity and evidence retention

The start hook blocks new jobs below **8 GiB free or 15% free** on the dedicated
runner home's filesystem. The CI workflow also wraps dependency installs,
environment creation, builds, tests, and browser downloads with the `run`
command. It checks before spawning and every five seconds and exits 75 on low
disk. Each workflow entry uses `exec`, so the step PID that Actions signals is
the helper PID. Environment/path setup precedes that final `exec`; a failed
environment install still fails the step. SIGINT/SIGTERM handlers only record a
lock-free flag; the monitor checks it every 50 milliseconds independently of
the five-second disk poll. No Event/Condition lock is acquired in a handler.

The fresh helper is a Linux child subreaper and creates one command child. Its
cleanup reads only its own descendant tree through `/proc/<pid>/task/*/children`,
validates parent relationships and PID/start-time/boot identity before signaling each owned PID, and
reaps adopted descendants. This includes Playwright's detached webServer
session and orphaned/double-forked children after an early command exit. It
refuses to start in a process that already has children or lacks the readable
native children interface; it never turns a missing listing into an empty tree.
No host-wide process kill scan is used. Cleanup gives TERM one second, then
KILL/reaping one second, including on ordinary command failure. It fails visibly
if owned descendants cannot exit in that window. Ordinary failure codes and
SIGINT/SIGTERM codes are preserved when cleanup succeeds.
Alerts appear as Actions error/warning annotations and in the step summary.

Cache capacity is enforced at boundaries, so a running install can temporarily
exceed 4 GiB. The disk guard bounds continued allocation, with a five-second
observation window; it is not a filesystem quota. Recent or open evidence is
never evicted to meet a budget. Local job evidence has a 1 GiB warning budget,
and recent runner logs have a 512 MiB warning budget; their minimum retention
takes priority. If those budgets or protected caches cannot be reclaimed, an
annotation identifies it and the disk threshold prevents further exhaustion.
Uploads themselves, checkout/setup-node/action preparation, and other host
services are outside the command monitor. The start hook runs before workflow
steps but after Actions' own action preparation.

Defaults are configurable with the dedicated runner's
`/data/goodteam-ci/hooks/cleanup-policy.json`. Use only known keys with positive
integer values; byte values are bytes and day/hour values are durations:

```json
{
  "cache_max_bytes": 4294967296,
  "cache_keep_days": 30,
  "evidence_keep_days": 14,
  "evidence_max_bytes": 1073741824,
  "logs_keep_days": 14,
  "logs_max_bytes": 536870912,
  "stale_temp_hours": 24,
  "min_free_bytes": 8589934592,
  "min_free_percent": 15,
  "disk_check_seconds": 5
}
```

Keep the policy readable by `goodteam-ci`, without credentials. Evidence/log
limits intentionally alert instead of overriding retention. Review `/data`
usage weekly; no system, Docker, or volume prune is part of this lifecycle.

### Updating an existing GoodTeam runner (separate deployment)

After independent review and merge, install the three reviewed files
`job-boundary-cleanup.sh`, `job-completed-cleanup.sh`, and `runner_cleanup.py`
from `ops/ci-runner/` into `/data/goodteam-ci/hooks/`, root-owned with group
`goodteam-ci` and mode 0750. Confirm **GoodTeam's runner is idle** first. Preserve
the existing `.env` and every unrelated entry, and update only these two values:

```text
ACTIONS_RUNNER_HOOK_JOB_STARTED=/data/goodteam-ci/hooks/job-boundary-cleanup.sh
ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/data/goodteam-ci/hooks/job-completed-cleanup.sh
```

Restart only `actions.runner.LukeJiaoR-GoodTeam.goodteam-ci-01.service` during
that separately authorized deployment. No re-registration, token, YUVA service
restart, shared slice change, Docker group, or package installation is needed.
The provisioner's existing-service path does not upgrade hooks; do not rerun
the whole provisioner to apply this patch. A fresh provision installs the new
files automatically. [GitHub's hook documentation](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/run-scripts)
requires a runner restart after `.env` changes. Both shell hooks have a
120-second timeout because Actions does not provide a hook timeout.

The runner kernel must support `PR_SET_CHILD_SUBREAPER` and provide readable
`/proc/<pid>/task/<tid>/children` (check the deployment shell's own
`/proc/$$/task/$$/children` before rollout). Both are tested before launching a
guarded command. Local sandbox verification emulates only the omitted children
listing with actual PID/PPID metadata; kernel adoption, identities, signals,
Playwright-created sessions, port closure, and reaping are exercised directly.

Post-install acceptance must observe `Set up runner` and `Complete runner`,
one success/failure/cancellation, retained upload evidence, marked temp
reclamation, and disk/cache usage on the actual GoodTeam runner. These are
deployment checks, not claims from isolated local validation. This code task
does not perform host cleanup or change existing runner jobs/configuration.

## Provisioning and operations

Before provisioning, confirm both repository runners are idle. Copy
`ops/ci-runner/` to the host, create a short-lived repository runner
registration token, and run the provisioner as root. Pass the token through the
hidden prompt or a protected process environment; never store it in the repo,
shell history, issue, or PR.

The provisioner pins Actions Runner 2.337.0 and CPython 3.12.11, verifies both
official SHA-256 values, creates the dedicated account and directories,
installs the cleanup hooks, registers with an explicit non-root PATH, and places
both CI services in the aggregate systemd slice.

Operational checks:

```bash
systemctl status actions.runner.LukeJiaoR-GoodTeam.goodteam-ci-01.service
systemctl status actions.runner.LukeJiaoR-ziwei.yuva-ci-01.service
systemctl status goodteam-shared-ci.slice
systemctl show goodteam-shared-ci.slice -p CPUQuotaPerSecUSec -p MemoryHigh -p MemoryMax -p MemorySwapMax
df -h /data
free -h
```

To stop GoodTeam CI without affecting YUVA:

```bash
systemctl stop actions.runner.LukeJiaoR-GoodTeam.goodteam-ci-01.service
```

Do not delete the runner directory until the repository registration has been
removed. Re-registration requires a new short-lived token.

## Acceptance evidence

Record the real PR URL, Actions run/job URLs, runner labels, tool versions,
successful backend/full and frontend jobs, final `CI / gate`, slice accounting,
and post-run disk/memory observations here before issue #393 is closed.
