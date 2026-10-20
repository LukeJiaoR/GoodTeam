# Project registry read failures

The project registry is stored at `GOODTEAM_PROJECTS_REGISTRY`, or at
`GOODTEAM_HOME/projects.json`, or by default at `~/.goodteam/projects.json`.

A genuinely missing file is an empty registry. Reading it or unregistering a
nonexistent project does not create it; the first registration creates it.

An existing registry that cannot be decoded, read, or validated is different.
`ProjectRegistry` logs a warning and raises `ProjectRegistryReadError`.
Registration and unregistration stop before the writer is called, preserving
the original file. GoodTeam does not delete, quarantine, truncate, or change
permissions on that file. Callers that already treat registry discovery as
optional may continue with their other project-discovery sources; the warning
still identifies the failed registry.

## Valid data

The registry must be a JSON object with a `projects` list of objects. Version 1
is supported; an omitted version remains compatible with older files. Each
project needs a nonempty string `config_path`. If present, `key`, `root`,
`state_path`, and `updated_at` must be strings. Unknown fields are retained.
Duplicate JSON keys, nonstandard `NaN` or infinity constants, and numbers that
overflow to infinity are rejected rather than silently discarding or rewriting
their values.

## Recovery

Inspect the path and reason in the warning first. Keep the original file for
recovery. Restore a known-good copy or repair the JSON and project entries after
reviewing them; do not empty the registry just to suppress the error. If the
problem is filesystem access, resolve it through the normal local account or
storage administration process.

After repair, repeat the original operation. The same registry instance rereads
the file; there is no cached corrupt state to clear. This behavior protects
failed reads, not concurrent writes from separate processes or storage failures
after a successful read.

## Regression coverage

`tests/test_project_registry.py` verifies original-byte preservation for invalid
JSON, UTF-8, schema, duplicate keys, nonstandard constants, excessive nesting,
and simulated read failures. It also covers a directory in place of the file,
missing-file initialization, normal registration/unregistration, legacy version
compatibility, unknown fields, and use after explicit repair. All fixtures use
temporary paths.
