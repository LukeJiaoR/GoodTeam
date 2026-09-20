# Standard Skill packages and local version storage (S1 / #441)

This module provides the package/identity foundation of #438. It does **not**
install anything into a coding agent, change Role bindings, grant tools, start a
Worker or certify conformance. User-facing local import is #442, binding is
#443, task locks are #444, and Git fetching is #447.

## Package format

A package needs a UTF-8 `SKILL.md` with YAML frontmatter containing non-empty
`name` and `description`. Its other files and directories are preserved,
including arbitrary resource folders, empty directories, binary files,
LICENSE/NOTICE and executable script bytes. Nothing in the package is executed.
No GoodTeam manifest is required in the source package and no package-name
allowlist exists.

[Agent Skills specification](https://agentskills.io/specification) defines the
portable format. GoodTeam's identities, digest, limits and store schema below
are implementation contracts, not additional fields users must author.

Metadata uses [PyYAML SafeLoader](https://pyyaml.org/wiki/PyYAMLDocumentation),
with duplicate keys, merge keys, anchors/aliases, custom object tags and
non-JSON-compatible values rejected. Composition is bounded to 4096 nodes and
32 nested levels. This intentionally supports a bounded YAML subset: unsupported
syntax returns a diagnostic rather than being silently reinterpreted. Main text
and binary resources retain their exact original bytes, including CRLF/BOM.

Missing/unreadable metadata is an error. A nonstandard display name, source
directory mismatch or other non-dangerous format difference yields a warning;
it does not become a filesystem path or an official-compliance claim. Vendor
fields and dynamic-command syntax are retained with diagnostics, **never**
evaluated. Compatibility prose and `allowed-tools` do not grant permissions.
Missing license information is not invented.

Default configurable bounds: 512 files, 1024 total entries, 16 path components,
8 MiB per resource, 32 MiB total, 256 KiB SKILL.md and 32 KiB frontmatter. The
walker applies limits while reading, not only after loading the whole tree.

## Capture and content identity

`capture_package(authorized_root)` returns immutable file bytes and directory
names. Every source component is opened without following links. File reads are
anchored to directory descriptors, reject special files and multiply linked
files, and check for concurrent changes. Directory traversal, absolute resource
paths, links, case/Unicode normalization collisions and unsupported permission
bits are rejected. A failed capture never deletes the source directory.

The caller must authorize the actual canonical root before capture. On systems
where `/tmp` is a symlink, select its canonical authorized path explicitly; do
not infer that any user-supplied symlink is permitted. Package-relative links
are not followed.

The canonical `skill_package_manifest.v1` lists sorted relative paths, empty
folders, per-file content digests, sizes and executable intent. The package
digest includes the complete manifest and therefore all selected file bytes,
not only SKILL.md. Root location, timestamps and non-executable permission
variations do not affect content identity. References-only changes do.

Publication validates the captured bytes again and never rereads a now-mutated
source folder. External dependencies and dynamically fetched rules are **not**
covered by this package hash; later Runtime support must represent or reject
those dependencies. Identical bytes are not a promise of identical model output.

## Separate identities

- `SkillScope(kind, owner_id, project_key)` identifies a caller-authorized
  personal/project/builtin namespace. Scope kind is not installation source.
- `SkillSource(kind, locator, subdirectory, revision)` identifies the explicit
  source. Local sources may use a private canonical locator; Git records require
  credential-free HTTPS and an already resolved commit. This module does not
  validate network reachability or perform Git fetching.
- `skill-<hash>` derives from scope plus source identity, not display name.
- `version-<hash>` derives from installed identity, full content digest and source
  revision. Two commits with identical bytes keep distinct provenance versions.
- `sha256:<hash>` is content identity. Byte storage may be shared within a scope,
  but author, source and access records are never merged because hashes match.

Two same-name packages from different sources remain two installations. Two
owners cannot read each other's versions by passing the other owner's IDs to
their own scoped store. **Scope construction itself is not authentication**:
future API/CLI services must authorize owner/project before constructing it.
This does not introduce or claim a multi-tenant permission system.

## Store and atomic publication

```text
<explicit protected store root>/
  <scope namespace hash>/
    .lock
    index.json
    objects/<content hash>/
      manifest.json
      content/SKILL.md
      content/<all other package resources>
```

The root is explicitly supplied; no home scanning, project import or runtime
configuration reuse takes place. Known Worker-writable roots can be supplied
and are rejected as store locations. The service must still place storage on
mounts the actual Worker cannot change. Read-only modes are defense in depth,
not a substitute for OS/Runtime separation from the same user identity.

All namespace mutations take an OS file lock. A publish stages and fsyncs every
file/directory, atomically publishes the complete content object, then atomically
replaces **one** index containing installation, version and operation receipt.
This avoids claiming a transaction across multiple mutable metadata files.

A crash before index commit may leave a complete orphan object, but no visible
installation. Retrying validates/reuses the object and commits the index. A
crash after index commit but before the response replays the saved receipt.
There is no active version named `latest` and no fallback to another package.
Partial staging is cleaned only inside the operation's own temporary directory.

Every mutation requires a command key. Same key/same input replays the original
receipt and exact version; the accompanying installed-record view reflects the
current library, not a historical or Role-active selection. A different input
under the same key returns `operation_conflict` rather than creating a second
operation. New command keys for identical source/content still reuse the same
immutable version. The index is versioned and bounded; unknown schema, malformed
ownership or duplicate JSON keys fail closed, without rewriting state.

## Retention pins and APIs

`SkillStore` exposes `publish`, `list_installed`, `list_versions`, `get_version`,
`read_resource`, `pin`, `release_pin` and `retention_pins`. Reads verify manifest
and bytes before returning a resource. Callers select exact installed/version
IDs, not display names or arbitrary host paths.

Pins bind an explicit holder (future task/binding/snapshot ID) to an exact
version. Reusing a holder for a different version or releasing the wrong version
is a conflict. Pin state and its operation receipt commit in the same index.
There is deliberately **no deletion/garbage-collection API in S1**: application
cleanup cannot erase a pinned or historical object. #448 must build uninstall
and reference checks on this retention contract, not directly unlink objects.
Operator shell deletion remains outside the API guarantee and will be detected
as missing/corrupt bytes on a later read, never repaired with latest.

Example service use, after the caller has authorized both paths and scope:

```python
from pathlib import Path
from goodteam_os.skills import SkillScope, SkillSource, SkillStore, capture_package

package = capture_package(Path("/authorized/source/my-review"))
store = SkillStore(Path("/protected/goodteam-skills"), SkillScope("user", "operator"))
result = store.publish(
    package,
    SkillSource("local", "/authorized/source/my-review"),
    command_key="confirmed-import-001",
)
# Metadata exists; no role was bound and no model was invoked.
```

## Verification boundary

Tests cover real filesystem capture and binary preservation, malicious/ambiguous
YAML, path/link/resource bounds, unchanged content in different directories,
provenance separation, immutable old versions, cross-scope reads, duplicate keys,
concurrent installs, retention conflicts, failed staging, index failures before
and after commit, and a real process crash releasing its namespace lock.

These are package/store tests, not real Skill execution. CI must run the full
backend and required regression paths for this control-plane foundation. No
claim of successful #440 runtime conformance or #449 product acceptance follows
from a parser/store PASS.
