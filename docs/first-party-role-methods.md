# Built-in Role Skill baselines

Every Team role and the Manager have versioned defaults. QA retains its browser evidence method alongside its acceptance testing baseline.
The role package map in `src/goodteam_os/skills/role_packages.py` defines them.
Default-mode teams pick up these packages on new supported tasks.
A custom binding, including an empty selection, overrides the defaults.
Retries read the original task snapshot.

Ordinary Pi dispatch loads the assigned role's methods. Read-only Pi dispatch
supports Research and Planner. The native Manager loads its own pinned methods.
Trusted native Reviewer starts load the existing review-core package. Runtimes
that require protocol-only review retain that contract.

## Original Pi integration

> Historical scope of the original Pi package integration. #738 adds a pinned
> native Manager loader for these packages; current Manager behavior is described
> in [Manager on Codex app-server](manager-app-server.md). Ordinary Pi Workers
> continue to use the integration below.

Sources: GoodTeam #445 (per-invocation Role methods), #686 (discussion, delegation and continuation), #696 (Planner professional handoff), #701 (authorized search and evidence). These newly authored method assets are not copies of stable_role_prompt. The old prompt-based default experiment was withdrawn and is not part of this candidate.

The original four standard packages include SKILL.md and reference checklists: bounded-work (shared), manager-coordination, evidence-research, planning-handoff. Version role-methods.v1 and the exact package digest identify each source; original SkillStore installation/version IDs and task snapshots retain exact bytes. Role-to-package mapping is explicit and independent of Team Runtime configuration. Shared bounded-work is reused across orchestrator, research and planner. These are methodology packages, not new permissions, a scheduler, or a permanent worker identity.

New registered-project standard Pi dispatches whose Roles all have these methods capture defaults through existing TaskSkillPlan.default_items. Custom selections remain authoritative, including custom-empty. Unsupported Roles and adapters retain their previous path rather than silently acquiring invented methods. Existing dispatch request markers, task locks and jobs determine replay; old requests are not upgraded. Once captured, the original task version is replayed without looking up latest packages. Read-only and strict jobs remain unchanged.

The same generic SkillContextProvider/PiMethodInput provides required text and controlled package resource reads. References remain available through the existing resource catalogue/broker. Package-name special cases are not added to the loader. No new runtime, pool or task schema is introduced.

This does NOT claim the ordinary project Manager or read-only Research/Planner producers have been changed. The packages can be consumed by the supported standard Pi route only. Their current production call sites still need a separate precise audit; changing a task's execution policy to make a test pass is not acceptable. Method text is not enforcement of the Manager's tool boundary.

Validation uses actual stores, snapshots, original job creation, Pi input construction and broker resource reads. The dispatch regression stops before retained Git workspace creation and therefore proves producer capture/replay, not real Git/Pi/provider execution. No model invocation, search bill, deployment or external publication is made. Real method delivery, observed resource use and model adherence are separate claims; #445/#419 remain open.
