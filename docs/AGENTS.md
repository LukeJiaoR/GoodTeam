# docs/
> L2 | Parent: [AGENTS.md](../AGENTS.md)

产品、架构、验证与开发流程的源码文档。

AGENTS.md: 本目录的成员与职责边界。
archive/: 历史文档。
audit/: 审计记录。
dogfood/: 自用验证记录。
ledger/: 账本设计文档。
plans/: 项目计划。
review-workbench/: Reviewer 工作台文档。
skills/: Skill 文档。
superpowers/: 工作流设计文档。
verification/: 验证证据。
README.md: GoodTeam Documentation。
acceptance-coverage-display.md: Original acceptance coverage in Review Workbench。
active-goal-intake-composition.md: Compose existing running-project intake with Role/Loop work。
agent-sandbox-v0.md: Agent Sandbox v0。
architecture-commercialization-review.md: GoodTeam Architecture and Commercialization Review。
architecture.md: 项目、任务队列、Worker 和候选证据的职责。
automatic-candidate-review.md: Automatic exact-candidate review。
backlog-human-board.md: Backlog judgment through the existing Human board。
backlog-wait-receipt.md: Recover a committed waiting decision after acknowledgement loss。
backlog.md: 历史待办与当时的决策。
batch-result-context.md: Known terminal mixed-batch feedback。
beautiful-ui-product-redesign.md: 产品界面改版的范围、实现与历史验证。
blocked-project-judgment.md: Project judgment when no authorized task is ready。
bounded-goal-continuation.md: Bounded Goal continuation (first vertical slice)。
candidate-check-receipts.md: Explicit candidate checks and acceptance visibility。
candidate-validation.md: Merge Candidate Validation。
codex-providers.md: Codex 和 Pi 共用 Provider 的设置、锁定与原生适配。
compact-settings-knowledge.md: #677 Settings and Knowledge implementation candidate。
config-reference.md: Project Config Reference。
confirmed-scope-additions.md: Human-confirmed later work in the existing project grant。
context-continuity.md: Bounded conversation and task continuity。
continuation-activity-visibility.md: Show the human the actual continuation situation。
continuation-authority.md: Continuation authority semantics。
conversation-continuity.md: Conversation continuity。
conversation-feedback.md: Conversation feedback (selective follow-up to #430 and #431)。
correction-worker-attempts.md: Correction Worker host protocol。
cross-page-workspace.md: Cross-page workspace。
cross-project-attention.md: Cross-project attention (#397)。
current-progress.md: Current Progress。
current-result-handoff.md: Current result handoff in Review Workbench。
current-single-machine-scope-audit.md: Current single-machine development scope audit。
delivery-backlog-judgment.md: Keep remaining authorized work visible after one delivery。
delivery-feedback-drain.md: Drain every authorized delivered outcome through the existing Manager loop。
development-branch-hygiene.md: Development Branch Hygiene。
discovery-context-refresh.md: Preserve completed discovery decisions and reconsider valid new facts。
execution-identity.md: Execution Identity Reference。
explainer.md: GoodTeam Explainer。
first-party-role-methods.md: 各角色的内置基线、覆盖规则与加载入口。
functional-ux-redesign-2026-10-11.md: GoodTeam 功能驱动的体验重设计研究。
fundraising-readiness-sprint.md: Fundraising Readiness Sprint Plan。
goal-history-pagination.md: Goal history pagination。
goodteam-overview-for-non-technical-readers.md: GoodTeam 项目说明（非技术版）。
http-transport-limits.md: Dashboard HTTP transport limits。
investigation-source-context.md: Historical investigation source and current Context。
iteration-context-inputs.md: Iteration planning and bounded context。
live-smoke-playbook.md: Live Smoke Playbook。
m0-product-acceptance.md: M0 product acceptance: implementation is not delivery。
mainline-progress.md: Mainline Progress。
manager-app-server.md: Codex 和 CodeBuddy 的 Manager 配置、会话与工具。
manager-autonomous-follow-up.md: Bounded Manager progress questions (#700)。
manager-comparison-context.md: Comparing unresolved work without authorizing it。
manager-completion-reuse.md: Reuse original Manager completions across the Loop。
manager-langgraph-postgres.md: Manager conversation and PostgreSQL migration。
manager-pi-running-observation.md: Running observation uses the captured Pi Manager contract。
manager-pi-tool-contract.md: Manager-specific Pi coordination contract。
manager-runtime-observation.md: Running-period Runtime observation。
memory-file-store.md: Role memory file-store consistency。
memory-model.md: Memory Model。
mixed-authorized-feedback.md: Mixed write and initial read-only result feedback。
mixed-runtime-methods.md: Mixed Runtime batches keep per-task Role methods。
model-scorecard.md: Model / agent scorecard — bug attribution for task routing。
multiple-investigation-feedback.md: Return multiple discovered investigations to the existing Manager loop。
observation-recovery-human-board.md: Integrate existing observation recovery with Human board questions。
operator-guide.md: 启动项目、派发任务及验证交付。
operator-manager-intake.md: Operator intake convergence (candidate)。
operator-plan-review.md: Operator plan-compliance gate。
ordinary-pi-method-input.md: Explicit ordinary Pi method input (#445)。
pending-write-review-feedback.md: Read known write-batch results before delivery acceptance。
per-role-method-capture.md: Preserve supported Role methods in an all-Pi mixed-Role batch。
pi-method-dispatch.md: Ordinary Role methods through retained dispatch workspaces (#445)。
pi-method-search.md: Ordinary Role methods and the existing Web Search tool (#445 / #701)。
pi-skill-resource-channel.md: Pi method-resource channel (#445)。
pi-terminal-protocol.md: Pi terminal protocol boundary (#700)。
planner-shared-feedback.md: Planner through the existing discovery and feedback path。
planning-context-refresh.md: Reconsider changed planning Context without abandoning the bounded loop。
prior-art-multi-agent-coordination.md: Prior art — multi-agent coordination (reference, not a plan)。
product-core-adapter-strategy.md: GoodTeam Product Strategy v0.1: Own the Engineering Org, Integrate the Tools。
product-ui-capability-parity.md: Product UI capability parity。
product-vision.md: GoodTeam — The First AI Engineering Org You Actually Own。
project-batch-quality.md: Manager 候选接受、QA、Code Review 与专项质量凭据。
project-batch-recovery.md: Recover or abandon an undelivered project batch。
project-chat-worker-controls.md: Explicit project-chat Worker control。
project-conflict-wait.md: Reuse the original queue while a sibling occupies a write domain。
project-continuation.md: Continue a project across delivered batches。
project-environment.md: Project environment and ordinary tasks。
project-live-human-feedback.md: Human answers reach Manager while sibling Workers continue。
project-loop-investigation.md: Project Loop: investigation, feedback and retained question board (#686)。
project-manager-conversation.md: Project Manager and task boundaries。
project-memory-rag.md: Project memory retrieval with PostgreSQL。
project-observation-judgment.md: Budgeted running-observation advice contract。
project-onboarding-runner.md: Project Onboarding Runner (Track 2A)。
project-outcome-discovery.md: Discover a bounded follow-up from a delivered project outcome。
project-registry-recovery.md: Project registry read failures。
project-running-observation.md: Running observation candidate on the original continuation。
project-shared-worker-pool.md: Keep independent approved work moving across batches。
project-sibling-feedback.md: Keep the original grant alive while sibling execution remains。
project-task-batches.md: 确认任务草稿、执行批次与读取结果。
provenance-ledger.md: Provenance Ledger。
readiness-executors.md: Project readiness executors。
readonly-pi-role-methods.md: Read-only Researcher / Planner methods through the original Pi Runtime。
readonly-pi-search.md: Authorized Web Search for read-only Researcher / Planner。
ready-work-human-context.md: Let Manager judge ready work alongside pending Human questions。
repository-context-provider.md: Repository Context Provider。
role-capability-discovery.md: Continuing Manager decisions use configured Role capability。
role-capability-intake.md: Ordinary Manager intake follows the configured Role capability。
role-capability-routing.md: Runtime source governs artifact routing。
role-loop-composition.md: Connect the existing ordinary Role methods to the Manager task loop。
role-runtime-source.md: Role Runtime configuration source (#445)。
role-skill-input-contract.md: 普通 QA 的 Role Skill、资源与运行证据。
running-human-board.md: Running Manager judgment to the existing Human board。
running-manager-observation.md: Running-period Manager advice consumer。
runtime-catalog.md: Runtime catalog and reasoning selection。
runtime-context-reliability.md: Runtime timeout and context freshness。
search-configuration-ui.md: Explicit queued-task Web Search configuration (#701)。
self-hosted-ci-runner.md: Former self-hosted CI runner。
status.md: GoodTeam Current Status。
supervised-correction-loop.md: Supervised correction rounds。
task-assistant-contract.md: Task Assistant Recovery And Draft Contract。
task-delegation-contract.md: Bounded Role task delegation and entry consistency。
task-input-context-view.md: Task input and method inspection。
task-input-snapshots.md: Captured issue task inputs。
task-scheduling-migration.md: Goal context through the existing task scheduler。
task-skill-dispatch.md: Task Skill production boundaries (S4b / #461)。
task-skill-locks.md: Task Skill locks (S4 / #444)。
task-skill-registry-recovery.md: Missing task Skill registry。
task-workspace-design.md: Task workspace。
team-control-plane.md: Team Control Plane: Roles, Workers, and the Decision Ledger。
testing-strategy.md: Lean Testing Strategy。
undelivered-feedback.md: Bounded discovery from failed/mixed batches without a delivered seed。
ux-ui-review-2026-09-21.md: GoodTeam UX / UI 整体梳理与三批改进。
web-search-runtime.md: Opt-in Pi Web Search (#701)。
work-reading-continuity.md: Work conversation reading continuity。
work-reading-position.md: Work reading position (#397)。
worker-backends.md: Worker Backends Reference。
worker-memory-observations.md: Ordinary Worker memory observations。
worker-setup.md: Project setup for isolated Workers。

[PROTOCOL]: Update this header on change, then check AGENTS.md
