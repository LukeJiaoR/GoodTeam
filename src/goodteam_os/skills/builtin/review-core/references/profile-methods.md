# Optional legacy profile methods

These methods were previously placed into reviewer task text by built-in
profiles. They are now available as optional package content for new
Skill-backed Code Review tasks, rather than a hidden protocol requirement.

## Correctness and security

Focus: Behavioral correctness; Security regressions; Data handling and input
validation; Test coverage for risky paths.

Suggested report organization: Summary; Findings; Evidence; Risk assessment;
Recommendation.

## Architecture and maintainability

Focus: Module boundaries; API and contract stability; Long-term maintainability;
Complexity and coupling.

Suggested report organization: Summary; Architecture findings; Maintainability
risks; Evidence; Recommendation.

## Product and operator review

Focus: User workflow impact; Operator ergonomics; Release readiness;
Documentation and handoff clarity.

Suggested report organization: Summary; Operator impact; Product risks; Evidence;
Recommendation.

An advisory recommendation is not a gate decision. Keep the caller's exact
output protocol and explicit task requirements even when selecting a lens.
