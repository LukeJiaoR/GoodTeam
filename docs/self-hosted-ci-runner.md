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
is the current repository. Backend and frontend depend on that guarded job, and
the always-running gate repeats the same-repository guard. A fork PR therefore
cannot schedule code on this persistent host; a maintainer must first put the
reviewed patch on a same-repository branch. The workflow does not use
`pull_request_target`.

OpenWiki remains on `ubuntu-latest`. It is manual-only because it has provider
secrets and repository/PR write permission, and those capabilities must never
reach `goodteam-ci`.

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
GoodTeam account; GoodTeam CI does not require Docker, a database, or browser
infrastructure.

## Dependencies and caches

The host provides Git, a bootstrap Python, npm, and standard Python build
tooling. `actions/setup-python` installs/selects Python 3.12 and
`actions/setup-node` installs/selects Node 20 in the runner tool cache for each
job; the PR job logs are the runtime-version proof. Their GitHub-backed
dependency caches are disabled. Persistent runner-local pip and npm caches
remain available while reproducibility stays anchored by
`pip install -e ".[dev]"` and `npm ci`.

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

The provisioner pins Actions Runner 2.337.0 and verifies its official SHA-256.
It creates the dedicated account and directories, installs the cleanup hooks,
registers the runner, and places both CI services in the aggregate systemd
slice.

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
