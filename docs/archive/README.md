# Documentation Archive

This directory stores superseded or retired GoodTeam documents that still have historical value.

Archived documents are **not current product contracts**. Keep them when they preserve useful design rationale, implementation sequencing, migration context, or acceptance evidence that is not better represented elsewhere.

## Archive rules

Move a document here when:

- a stable reference now fully supersedes an older proposed design,
- an implementation plan is complete and its current-sounding location would mislead contributors,
- a point-in-time report is useful as historical evidence but no longer describes the current product,
- a design was intentionally abandoned but the rationale is worth retaining.

Delete rather than archive when the file is only a stale generated snapshot, duplicate presentation artifact, or disposable output with no durable design/evidence value.

When reading archived material:

1. Treat status labels, screenshots, test counts, model names, routes, and UI descriptions as point-in-time facts.
2. Prefer stable manuals in the parent `docs/` directory for current behavior.
3. Prefer code and tests when an archived design disagrees with executable behavior.
4. Do not revive an archived plan by editing it in place; create a new issue/plan that links back to the historical source.

Git history remains the ultimate record of moved or deleted documentation.
