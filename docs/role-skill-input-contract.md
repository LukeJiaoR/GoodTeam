# Ordinary Role Skill input preparation (#445)

## Implemented boundary

`SkillContextProvider.worker_input(reference)` projects an already captured,
verified content context into a bounded private adapter input. It preserves the
original Role, thin Role contract, exact delegated task (including purpose,
scope and acceptance supplied by the shared delegation contract), relevant
Context, constraints and expected artifacts. Required method bodies are complete;
on-demand methods expose only their versioned resource catalog until requested.
The projection never serializes the full Worker request, Operator root or runtime
configuration. Explicit user task text is preserved, not sanitized or rewritten.

The method uses `private_input` for current admission, task scope, immutable bytes
and revocation checks. The complete rendered input has its own byte limit and
fails rather than truncating. No lookup of current Team settings happens at this
point. Code Review retains its dedicated protocol and cannot use this projection.

An admitted adapter must bind the existing `read_resource` operation to this
same content reference; model arguments may select only a listed skill/resource,
not another task or content reference. A broker receipt is not proof of runtime
read or method compliance. This change adds no tool, runtime, scheduler, provider
call or permission switch.

## Remaining activation work

Ordinary production dispatch still returns `skill_runtime_not_connected`. This
is a preparation-layer increment, **not completion of #445**. A real adapter's
#440 isolation/admission evidence, actual required-content delivery and observed
optional reads remain necessary before activation. Default Role base packages,
Manager base methods and generic runtime resource selection also remain follow-up
work; this patch does not reinterpret legacy prompts as loaded Skills.

The regression test uses actual immutable stores and Worker publication with a
model-free `contract_fixture` authority. It covers two Roles using the same
versioned packages without mixing task Context; required versus on-demand bytes;
repeat input stability; context/path rejection; and default admission/budget
failure. No native runtime, paid model or full continuous-project Loop was run.
CI stays paused. The composed project path remains separate acceptance in #419.
