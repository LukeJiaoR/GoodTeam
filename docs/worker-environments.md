# Prepared Worker environments

An isolated Worker starts from captured project source, which does not include
Git-ignored dependency directories. A local operator can register a prepared
environment once for a project. GoodTeam copies the selected directories into
each batch Worker's workspace, or once into a sequential Workflow task's shared
workspace. It never mounts one Worker's dependency directory into another.
The source checkout is not modified by this operation.

Prepare dependencies separately under the project's existing policy, in a
Git checkout with the same relevant inputs. GoodTeam does not run or verify the
preparation command. The operator is responsible for selecting every input
that can affect the prepared output, including manifests, lockfiles, package
manager configuration, runtime versions and any source used by install
scripts. A matching selection does not prove an installation is reproducible
or safe. Do not capture credentials or unrelated ignored files.

For example, adapt the paths and input patterns to the project and its package
manager:

```sh
goodteam worker-environment capture \
  --project-root /absolute/path/to/project \
  --prepared-root /absolute/path/to/prepared-checkout \
  --input package.json --input package-lock.json --input '**/package.json' \
  --artifact node_modules \
  --tool /absolute/path/to/node --tool /absolute/path/to/npm

goodteam worker-environment status \
  --project-root /absolute/path/to/project --environment-ref 'sha256:...'

goodteam worker-environment activate \
  --workspace /absolute/path/to/goodteam-workspace --project-key PROJECT \
  --environment-ref 'sha256:...'
```

Use the reference printed by `capture`. Tools must be the same executables on
the service's PATH. Each input pattern must match at least one captured project
file in both checkouts. Artifact paths must name ignored directories without
tracked files; absolute, escaping, dangling, and metadata-directory links are
rejected. Files that embed the prepared checkout's absolute path are rejected.
Some environments, including many Python virtual environments, cannot be
relocated this way and need their own supported preparation method.

After dependency inputs or tools change, prepare and capture a new environment.
When the project is idle, replace the selection explicitly using `activate`
with `--environment-ref <new-ref> --replace-from <current-ref>`. Existing
execution authority and recovery remain bound to their original configuration;
changing the selection does not authorize an old run to use the replacement.
Do not edit the local configuration concurrently with activation.

The project root, declared inputs, tool executable digests, host and template
contents are checked before use. Batch copies are checked before Worker launch;
sequential Workflow Workers are checked before each launch, so a previous
Worker changing a dependency or a declared input stops the next launch. A
missing or changed environment fails closed. Receipts record the selected
environment and do not claim its contents stayed unchanged after execution.

The prepared-environment path currently admits ordinary Codex Workers with
`read-only` or `workspace-write` sandbox mode. Set
`GOODTEAM_CODEX_SANDBOX_MODE=workspace-write` in the GoodTeam service environment
for writable Workers; read-only Workers retain their existing policy. Other
runtimes, including Pi method Workers, are not admitted to this path until their
sandbox behavior is verified. Existing protected candidate-check and review
paths are separate and do not automatically acquire this environment.

The isolated Git workspace and independent dependency copies prevent accidental
cross-Worker writes through those copies. They do not create an operating-system
read boundary: Workers on the same machine may be able to read other paths.
Actual access restrictions depend on the selected runtime sandbox and host.
GoodTeam does not automatically install missing dependencies or change
permissions to make an environment pass.
