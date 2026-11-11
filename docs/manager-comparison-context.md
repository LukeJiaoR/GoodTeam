# Comparing unresolved work without authorizing it

Incremental #686 item 7: a Manager iteration choice may compare any task in its
supplied candidate inventory, including blocked, in-progress and review tasks.
This allows its rationale to explain dependencies, current execution and pending
review rather than ignoring those project facts.

Selected tasks must still be eligible. Unknown/out-of-scope comparison IDs are
rejected, selected IDs must appear among the unique compared IDs, and parallel
write-domain constraints apply unchanged to the selected group. Comparison does
not change ledger state, restart jobs, confer execution authority or turn review
into delivery. No empty selection or forced workflow is added.

This is NOT runtime supervision or permission to invoke planning while execution
owns the project. Existing planning-idle and execution gates are unchanged.
#686 items 1/7 still need observation-time judgment and richer follow-up choices;
#700 still needs supported Runtime control and reply-to-Context wiring. No-seed
feedback, real Runtime and combined continuous-project acceptance also remain
unverified. CI remains paused; there is no deployment or paid model call.
