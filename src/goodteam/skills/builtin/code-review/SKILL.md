---
name: code-review
description: Perform severity-ordered code reviews grounded in real diffs, edge cases, security, and test coverage.
---

# Code Review

Review changes systematically for correctness, security, maintainability, and adherence to task scope:

1. Prioritize real bugs, race conditions, security flaws (injection, path traversal, secret leaks), and broken contracts over style nitpicks.
2. Verify every finding directly against the diff and surrounding code; cite `file:line`.
3. Check `references/checklist.md` for security and edge-case items when reviewing I/O, auth, or subprocess boundaries.
4. Output a clear verdict (`approve` or `changes_requested`) with actionable findings ordered by severity.
