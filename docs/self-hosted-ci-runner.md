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

The start/completion hook removes only the current runner's job-temp children
and expires pip/npm cache files unused for 30 days. `actions/checkout` cleans
the repository workspace before checkout. Review `/data` usage weekly; do not
delete unscoped files or prune the host's Docker resources.

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
