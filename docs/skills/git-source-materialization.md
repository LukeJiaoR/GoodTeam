# Git Source Materialization

Issue #447 remains incomplete. This increment provides an offline package
materializer, not Git download or installation from a URL. There is no new API,
UI, ref resolver, credential reader, subprocess, or network path in production.
It does not enable Reviewer execution or change #440/#445/#446 acceptance.

## Implemented Contract

`skills.sources.git.capture_git_package` accepts a resolved `SkillSource` and a
mapping of complete Git object IDs to decompressed object bodies. The caller
explicitly selects the package subdirectory. It verifies the commit, traversed
trees, and selected blobs using Git's type/length header and SHA-1 or SHA-256.
Missing objects fail; capture never fetches or substitutes current branch data.

The returned value is the existing `SkillPackage`, suitable for the existing
store publication contract. File bytes and executable flags are retained.
Attributes are data: export-ignore, export-subst, filters, hooks, and scripts
are not evaluated. A selected symbolic link, gitlink (submodule), or LFS pointer
is rejected instead of silently omitting the required resource. LFS detection
includes the legacy `hawser` and `git-media` version aliases and leading
whitespace accepted by Git LFS. Unselected subtrees are not read.

Object count, examined tree entries, metadata bytes, path depth, resource count,
and resource byte budgets bound traversal/materialization. Invalid UTF-8 names,
unsafe paths, duplicate entries, incomplete tree records, object type mismatches,
and mismatched hashes fail before a package can be returned. Common package
validation still checks metadata and filesystem name collisions. Diagnostics
do not include object content or private paths.

The input mapping is already resident in the trusted caller. These limits do
**not** bound acquisition, decompression, or allocation performed before this
function. Object identity proves consistency with the selected commit, not
repository ownership, signature trust, or where that commit was obtained.

## Remaining #447 Work

- Controlled HTTPS acquisition, including actual destination enforcement across
  DNS resolution, redirects, proxy/config rewriting, and final connections.
- Download-time limits on transfer, object expansion, storage, time and child
  resources; cleanup and cancellation of only the operation's owned staging.
- Default branch/tag/commit resolution and source provenance, without implicitly
  reusing personal credentials. Private authentication stays unsupported until
  an explicit target-scoped credential channel is available and verified.
- Bounded multi-package discovery and explicit selection.
- Durable fetching/validating/confirmation/failure/cancellation state and
  idempotent installation of the exact preview bytes through Library.
- Library/API/browser integration, isolated HTTPS fixtures, and the remaining
  recovery/network acceptance cases required by the issue.

No public fetch endpoint should call this materializer until the acquisition
boundary above is implemented and validated. Passing these offline tests is not
evidence that a Git URL can be safely fetched or installed through the product.

## Verification

Focused command (use the repository development environment):

```sh
PYTHONPATH=src .venv/bin/python -m pytest tests/test_skill_git_objects.py tests/test_skill_packages.py tests/test_skill_store.py -q
.venv/bin/ruff check src/goodteam_os/skills/sources tests/test_skill_git_objects.py
```

The integration test uses actual Git-created SHA-1 and SHA-256 objects in a
temporary bare repository with isolated HOME/config and no network. It compares
the result with local package capture, including export-ignore attributes,
license text and an executable script, then publishes and reads the original
SKILL.md through SkillStore. It does not invoke the script or a model. Remaining
tests mutate object bytes, remove objects, change commits, compare empty
directories at the depth boundary with local capture, and exercise the
resource/type/path/budget failures. Production acquisition and browser evidence
remain NOT RUN for this increment.
