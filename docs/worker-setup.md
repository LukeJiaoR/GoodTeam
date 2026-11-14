# Project setup for isolated Workers

> **Temporarily unavailable after the #730 security review.** Automatic setup
> now fails closed for both online and offline commands. The native
> `:workspace` sandbox permits host reads: deleting environment credentials or
> disabling network access does not prevent install hooks from reading
> credential files and leaving them in outputs for later exfiltration.
>
> New configuration and previously captured setup invocations are rejected with
> `worker_setup_read_isolation_unavailable`; installation is not silently skipped
> and the agent is not started through this wrapper. Workers without
> `worker_setup` keep their existing execution path. Restoring automatic setup
> requires an implemented and verified host-read-isolated runtime, not a boolean
> opt-in, credential denylist, or permission bypass.
>
> The configuration below documents the retained format, not a currently
> executable setup capability. `network_access` accepts only explicit booleans
> (including the YAML parser's `true`/`false` strings), never Python truthiness.

## Retained configuration contract

Configure the existing project installation command once in the local project
configuration. Each ordinary CLI Worker runs it in its own captured workspace
before starting the agent. The package manager can reuse a project cache while
installed dependencies and workspace package links belong to that Worker.

For an npm project with an already populated local cache, for example:

```yaml
worker_setup:
  command: ["npm", "ci", "--offline"]
  env:
    npm_config_cache: /absolute/path/to/project-cache
  cache_paths: ["/absolute/path/to/project-cache"]
```

Choose the command and cache paths for the actual project. `network_access: true`
can permit an explicitly configured installation to fetch missing packages.
GoodTeam does not select installation commands from model output. Package
installation scripts run as part of the configured command.

Setup uses the installed Codex sandbox and is part of the existing Worker
process group. Setup failure fails that job; the ordinary retry and Stop paths
apply. `setup-result.json`, `setup.stdout.txt` and `setup.stderr.txt` are kept in
the job directory, and the result is included in Worker status. Confirmed runs
retain their original setup command through recovery. There is no environment
registry, activation operation, template digest or project-wide environment lock.

Ordinary Codex Workers request native `workspace-write` sandboxing by default;
read-only roles retain `read-only`. This limits writes, but native workspace
sandboxing permits reads outside the workspace. It is not a container or a
cross-project confidentiality boundary. Dependency directories are not shared
as writable symlinks between Worker workspaces. Protected review/correction and
Pi method runtimes retain their existing protocols; this setup hook is for the
ordinary CLI path.
