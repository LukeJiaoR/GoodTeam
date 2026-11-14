# Project setup for isolated Workers

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
`network_access` accepts only explicit booleans (including the YAML parser's
`true`/`false` strings), never Python truthiness.
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

## Installation-only environment and filesystem boundary

The setup wrapper retains the configured command, per-job output logs, failure
receipt and process group. It starts the original Worker only after setup exits
successfully. It does not require a particular benchmark project or a container.

Setup gets a fresh temporary HOME, CODEX_HOME and temporary directory per
invocation. It does not inherit the Worker's provider credentials. Inherited
variables are limited to PATH, LANG, LC_ALL, LC_CTYPE and TZ. Configured setup.env
accepts those plus CI, npm_config_cache, PIP_CACHE_DIR, YARN_CACHE_FOLDER,
PNPM_HOME, CARGO_HOME and RUSTUP_HOME. Other variables produce an explicit setup
failure, not a silent fallback. Existing commands that require additional
environment variables need compatibility review; this is not a zero-impact
change to arbitrary custom setup commands.

The native setup profile denies general filesystem reads, retains Codex's
minimal tool/runtime reads, and permits the captured workspace, dedicated
temporary directories and explicit cache directories. It does not grant the
whole host HOME or general temporary directory. Cache roots must be dedicated
and contain no credentials. Roots overlapping the source/HOME ancestors or
known authentication directories are rejected. A Cargo cache must not be the
host's existing .cargo directory containing registry credentials. The explicit
authentication exclusions are defense in depth, not an exhaustive classifier
of secrets stored at arbitrary paths. Do not place credentials in workspace,
toolchain or approved cache paths.

The sandbox mechanism follows the documented workspace-only profile:
https://learn.chatgpt.com/docs/permissions . Unsupported permission policies must
fail through the existing setup failure path; no unsandboxed fallback is added.

This is a candidate implementation, not an isolation attestation. Exact host
versions, custom toolchains outside minimal runtime paths, real installation,
credential-read denial, Stop and recovery still need actual verification.
Only static checks have been performed; no test suite, model call or live
installation was run for this change.
