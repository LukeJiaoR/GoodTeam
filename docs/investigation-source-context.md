# Historical investigation source and current Context

This is an incremental part of #686 item 4, not completion of unified Worker
feedback (#686 item 2) or proof of a complete real-Runtime project loop.

A settled read-only investigation receipt retains the original dispatch source
commit in `project_head`. Later independent commits no longer make the original
report unreadable. Existing receipts captured at the original commit retain the
same schema and digest. Missing/invalid source revisions still fail closed.

The Manager discovery packet adds `investigation_context`, with the current
verified decision commit and each report's source commit and revision-match flag.
These are historical findings for judgment against current Context, not current
acceptance or delivery evidence. Task status and delivery verification are not
promoted. The original report bytes, job/dispatch/task binding, terminal evidence,
read-only attribution and clean checkout checks remain enforced.

A decision already in flight is still frozen. Authority, task intent, report
content, verified delivery and backlog changes during that decision still fail
publication rather than silently replace its input or launch duplicate work.

Remaining scope: generic/mixed-batch success/failure/partial feedback; narrowing
whole-backlog invalidation with relevant-task revalidation; running-job Manager
supervision; actual Runtime and combined-path acceptance under #419. No additional
paid model calls, production deployment or CI activation is part of this change.

Deterministic regression: `tests/test_project_investigation_feedback.py` exercises
HEAD advancement, explicit source/current Context, absent source rejection, and
existing no-double-publication, evidence/intent tampering and no-delivery-promotion
boundaries. Its dispatch/model/storage seams are fixtures, not real Runtime proof.
