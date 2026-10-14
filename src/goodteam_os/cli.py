from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from goodteam_os.dispatch import dispatch_tracks, parse_dispatch_track_spec, parse_dispatch_verify_spec
from goodteam_os.execution import ExecutionAPI
from goodteam_os.failure_patterns import FailurePatternLibrary
from goodteam_os.memory import RoleMemoryStore
from goodteam_os.projects import (
    DELIVERY_MODES,
    WORKER_MODES,
    check_project_target,
    init_project,
    load_project_target,
)
from goodteam_os.smoke import run_smoke_suite
from goodteam_os.operator import enqueue_github_issue
from goodteam_os.operator_queue import DurableOperatorQueue
from goodteam_os.service import DEFAULT_PORT, restart_service, service_state_dir, service_status, stop_service
from goodteam_os.supervisor import ComponentHeartbeat, run_operator_component, run_supervisor, run_telegram_component
from goodteam_os.tasks import LocalTaskStore, build_task_board, serialize_task_board
from goodteam_os.telegram import poll_telegram_approvals
from goodteam_os.web import serve, start_workflow
from goodteam_os.workers import WorkerJobStore

if TYPE_CHECKING:
    from goodteam_os.skills.codex_runtime import CodexReviewAdmission


def _candidate_check_arguments(command, *, require_base=False):
    command.add_argument("--criterion", type=int, action="append", required=True,
                         help="Zero-based original acceptance criterion; mapping is not semantic proof")
    command.add_argument("--seconds", type=int, default=30)
    command.add_argument("--review-base", required=require_base, default="",
                         help="Original exact base SHA for the admitted independent Reviewer.")
    command.add_argument("argv", nargs=argparse.REMAINDER)


def _operator_startup_arguments(command):
    command.add_argument("--operator-review-config", type=Path, help="Private explicit queue-item candidate-check configuration.")
    command.add_argument("--operator-review-config-sha256", default="", help=argparse.SUPPRESS)
    command.add_argument("--local-codex-policy", type=Path, help="Existing task-bound local Reviewer policy; Operator only.")
    command.add_argument("--trusted-codex-review-policy", type=Path, help="Existing task-bound trusted Reviewer policy; Operator only.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="goodteam")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start")
    start.add_argument("idea")
    start.add_argument("--project-key", default="GTO")
    start.add_argument("--workspace", type=Path, default=Path("."))
    start.add_argument("--model-config", type=Path, default=Path("models.yaml"))
    start.add_argument("--model-profile", default="")
    start.add_argument("--role-model", action="append", default=[])
    start.add_argument("--delivery-mode", choices=list(DELIVERY_MODES), default="")
    start.add_argument("--source-type", choices=["new_project", "existing_project"], default="new_project")
    start.add_argument("--project-config", type=Path)
    start.add_argument("--dry-run", action="store_true")

    serve_command = subparsers.add_parser("serve")
    serve_command.add_argument("--host", default="127.0.0.1")
    serve_command.add_argument("--port", type=int, default=8788)
    serve_command.add_argument("--workspace", type=Path, default=Path("."))
    serve_command.add_argument("--frontend-dist", type=Path, help=argparse.SUPPRESS)
    serve_command.add_argument("--ready-file", type=Path, help=argparse.SUPPRESS)
    serve_command.add_argument("--ready-token", default="", help=argparse.SUPPRESS)
    serve_command.add_argument("--heartbeat-file", type=Path, help=argparse.SUPPRESS)
    serve_command.add_argument("--heartbeat-token", default="", help=argparse.SUPPRESS)
    serve_command.add_argument("--local-codex-policy", type=Path, help="Explicit task-bound local Reviewer exposure policy.")
    serve_command.add_argument("--trusted-codex-review-policy", type=Path,
                               help="Private permission to reuse native Codex for one trusted, method-free review task.")

    restart = subparsers.add_parser("restart", help="Restart the background GoodTeam server")
    restart.add_argument("--host", default="127.0.0.1")
    restart.add_argument("--port", type=int, default=DEFAULT_PORT)
    restart.add_argument("--workspace", type=Path, default=Path("."))
    _operator_startup_arguments(restart)

    subparsers.add_parser("stop", help="Stop the background GoodTeam server")
    subparsers.add_parser("status", help="Show the background GoodTeam server status")

    task = subparsers.add_parser("task", help="Enqueue a known-project source task")
    task_subparsers = task.add_subparsers(dest="task_command", required=True)
    task_enqueue = task_subparsers.add_parser("enqueue")
    task_enqueue.add_argument("issue_url")
    task_enqueue.add_argument("--workspace", type=Path, default=Path("."))

    queue = subparsers.add_parser("queue", help="Inspect or drain the durable operator queue")
    queue_subparsers = queue.add_subparsers(dest="queue_command", required=True)
    for queue_command in ("list", "drain", "resume"):
        queue_subparsers.add_parser(queue_command)
    review_config = queue_subparsers.add_parser("review-config", help="Print explicit candidate-check configuration without running it")
    review_config.add_argument("--item-id", required=True)
    review_config.add_argument("--workspace", type=Path, default=Path("."))
    review_config.add_argument("--reviewer-model", required=True)
    _candidate_check_arguments(review_config, require_base=True)
    prepare_review = queue_subparsers.add_parser("prepare-review", help="Capture original execution identity without running its Builder")
    prepare_review.add_argument("--item-id", required=True)
    prepare_review.add_argument("--workspace", type=Path, default=Path("."))

    supervise = subparsers.add_parser("supervise", help=argparse.SUPPRESS)
    supervise.add_argument("--workspace", type=Path, required=True)
    supervise.add_argument("--host", required=True)
    supervise.add_argument("--port", type=int, required=True)
    supervise.add_argument("--frontend-dist", type=Path, required=True)
    supervise.add_argument("--state-dir", type=Path, required=True)
    supervise.add_argument("--identity", required=True)
    supervise.add_argument("--ready-file", type=Path, required=True)
    supervise.add_argument("--ready-token", required=True)
    _operator_startup_arguments(supervise)

    telegram_component = subparsers.add_parser("_telegram-component", help=argparse.SUPPRESS)
    telegram_component.add_argument("--workspace", type=Path, required=True)
    telegram_component.add_argument("--ready-file", type=Path, required=True)
    telegram_component.add_argument("--ready-token", required=True)
    telegram_component.add_argument("--heartbeat-file", type=Path, required=True)
    telegram_component.add_argument("--heartbeat-token", required=True)

    operator_component = subparsers.add_parser("_operator-component", help=argparse.SUPPRESS)
    operator_component.add_argument("--workspace", type=Path, required=True)
    operator_component.add_argument("--state-dir", type=Path, required=True)
    operator_component.add_argument("--ready-file", type=Path, required=True)
    operator_component.add_argument("--ready-token", required=True)
    operator_component.add_argument("--heartbeat-file", type=Path, required=True)
    operator_component.add_argument("--heartbeat-token", required=True)
    _operator_startup_arguments(operator_component)

    init = subparsers.add_parser("init", help="Scaffold a project config and run doctor")
    init.add_argument("--key", required=True, help="Short project key, e.g. MYAPP")
    init.add_argument("--name", default="", help="Human project name (defaults to --key)")
    init.add_argument("--repo", default=".", help="Path to the target repo")
    init.add_argument("--projects-dir", type=Path, default=Path("projects"))
    init.add_argument("--model-profile", default="balanced")
    init.add_argument("--worker-mode", choices=list(WORKER_MODES), default="fake")
    init.add_argument("--delivery-mode", choices=list(DELIVERY_MODES), default="mvp")
    init.add_argument("--force", action="store_true")

    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--project-config", type=Path, required=True)

    board = subparsers.add_parser("board")
    board.add_argument("--project-root", type=Path, required=True)

    workers = subparsers.add_parser("workers")
    workers.add_argument("--project-root", type=Path, required=True)

    worker_session = subparsers.add_parser("worker-session", help="Inspect or control a persistent worker session")
    worker_session_subparsers = worker_session.add_subparsers(dest="worker_session_command", required=True)
    for command in ("status", "steer", "follow-up", "abort"):
        session_command = worker_session_subparsers.add_parser(command)
        session_command.add_argument("--project-root", type=Path, required=True)
        session_command.add_argument("--job-id", required=True)
        if command in {"steer", "follow-up"}:
            session_command.add_argument("--message", required=True)
        if command == "abort":
            session_command.add_argument("--reason", default="Operator requested abort")

    failure_patterns = subparsers.add_parser("failure-patterns")
    failure_patterns.add_argument("--project-root", type=Path, required=True)
    failure_patterns.add_argument("--min-hits", type=int, default=3)
    failure_patterns.add_argument("--min-confirmed", type=int, default=3)
    failure_patterns.add_argument("--max-false-positive-rate", type=float, default=0.0)
    failure_patterns.add_argument("--min-llm-triage-samples", type=int, default=5)

    smoke = subparsers.add_parser("smoke")
    smoke.add_argument("--workspace", type=Path, default=Path("."))
    smoke.add_argument("--suite", default="contentops,self")
    smoke.add_argument("--cycles", type=int, default=2)
    smoke.add_argument("--autonomy", choices=["full"], default="full")
    smoke.add_argument("--worker-mode", choices=["fake", "noop", "codex"], default="fake")
    smoke.add_argument("--dry-run", action="store_true")

    dispatch = subparsers.add_parser("dispatch")
    dispatch.add_argument("--project-root", type=Path, required=True)
    dispatch.add_argument("--track", action="append", default=[])
    dispatch.add_argument("--verify", action="append", default=[])
    dispatch.add_argument("--constraints-file", type=Path)
    dispatch.add_argument("--research-report", type=Path)
    dispatch.add_argument("--allow-unsafe", action="store_true")
    dispatch.add_argument("--task-id", default="", help="Stable bounded direct-task identity for an explicit retry.")
    dispatch.add_argument("--review-base", default="", help="Exact base commit for one locked code_review track.")
    dispatch.add_argument("--review-head", default="", help="Exact head commit for one locked code_review track.")
    dispatch.add_argument("--review-model", default="", help="Exact model for that Codex review; does not grant provider access.")
    dispatch.add_argument("--local-codex-policy", type=Path, help="Explicit task-bound local Reviewer exposure policy.")
    dispatch.add_argument("--trusted-codex-review-policy", type=Path,
                          help="Private permission to reuse native Codex for one trusted, method-free review task.")

    inspect_local = subparsers.add_parser("local-codex-inspect", help="Inspect existing Codex without calling a model")
    inspect_local.add_argument("--codex", type=Path, required=True)
    inspect_local.add_argument("--codex-home", type=Path, required=True)
    inspect_local.add_argument("--model", required=True)

    memory_append = subparsers.add_parser("memory-append")
    memory_append.add_argument("--project-root", type=Path, required=True)
    memory_append.add_argument("--role", required=True)
    memory_append.add_argument("--horizon", required=True)
    memory_append.add_argument("--text", required=True)
    memory_append.add_argument("--source", default="")
    memory_append.add_argument("--supersedes", default="")

    tg_poll = subparsers.add_parser("tg-poll")
    tg_poll.add_argument("--workspace", type=Path, default=Path("."))
    tg_poll.add_argument("--project-key", default="")
    tg_poll.add_argument("--once", action="store_true")

    execution = subparsers.add_parser("execution")
    execution_subparsers = execution.add_subparsers(dest="execution_command", required=True)

    intake_github_issue = execution_subparsers.add_parser("intake-github-issue")
    intake_github_issue.add_argument("--repo", required=True)
    intake_github_issue.add_argument("--issue", type=int, required=True)
    intake_github_issue.add_argument("--project-root", type=Path, required=True)
    intake_github_issue.add_argument("--adapter", default="fake")
    intake_github_issue.add_argument("--role", default="implementation")

    bind_plan = execution_subparsers.add_parser("bind-plan", help="Bind a captured task to existing approved workflow intent")
    bind_plan.add_argument("--execution-id", required=True)
    bind_plan.add_argument("--project-root", type=Path, required=True)
    bind_plan.add_argument("--workflow-run", required=True)
    bind_plan.add_argument("--task-anchor", required=True, help="Exact unique task heading in the derived breakdown (fix plan for fix profile)")

    run_execution = execution_subparsers.add_parser("run")
    run_execution.add_argument("--execution-id", required=True)
    run_execution.add_argument("--project-root", type=Path, required=True)
    run_execution.add_argument("--review-base", default="", help="Original exact base for the frozen delivery plan.")
    run_execution.add_argument("--criterion", type=int, action="append", help="Original acceptance criterion index.")
    run_execution.add_argument("--seconds", type=int, default=30)
    run_execution.add_argument("--acknowledge-external-cost", action="store_true",
                              help="Acknowledge correction cost; does not grant runtime admission.")
    run_execution.add_argument("--local-codex-policy", type=Path, help="Existing task-bound local Reviewer policy.")
    run_execution.add_argument("--trusted-codex-review-policy", type=Path, help="Existing task-bound trusted Reviewer policy.")
    run_execution.add_argument("argv", nargs=argparse.REMAINDER)
    for command in ("pr", "ci", "replay", "review"):
        execution_command = execution_subparsers.add_parser(command)
        execution_command.add_argument("--execution-id", required=True)
        execution_command.add_argument("--project-root", type=Path, required=True)
    review_decision = execution_subparsers.add_parser("record-review-decision")
    review_decision.add_argument("--execution-id", required=True)
    review_decision.add_argument("--project-root", type=Path, required=True)
    review_decision.add_argument(
        "--decision",
        required=True,
        choices=["approve", "request_changes", "reject"],
    )
    review_decision.add_argument("--reviewer", required=True)
    review_decision.add_argument("--reason", default="")
    review_decision.add_argument("--validation-receipt", default="")
    review_decision.add_argument("--head", default="")
    correction = execution_subparsers.add_parser("start-correction-loop")
    correction.add_argument("--execution-id", required=True)
    correction.add_argument("--project-root", type=Path, required=True)
    correction.add_argument("--head", required=True)
    correction.add_argument("--validation-receipt", required=True)
    correction.add_argument("--max-rounds", type=int, default=3)
    correction.add_argument("--wall-seconds", type=int, default=900)
    stop_correction = execution_subparsers.add_parser("stop-correction-loop")
    stop_correction.add_argument("--execution-id", required=True)
    stop_correction.add_argument("--project-root", type=Path, required=True)
    stop_correction.add_argument("--loop-id", required=True)
    prepare_worker = execution_subparsers.add_parser("prepare-correction-worker")
    for flag in ("execution-id", "loop-id", "head", "validation-receipt", "operation-id"):
        prepare_worker.add_argument("--" + flag, required=True)
    prepare_worker.add_argument("--project-root", type=Path, required=True)
    prepare_worker.add_argument("--max-seconds", type=int, default=120)
    run_worker = execution_subparsers.add_parser("run-correction-worker")
    run_worker.add_argument("--execution-id", required=True)
    run_worker.add_argument("--project-root", type=Path, required=True)
    run_worker.add_argument("--job-id", required=True)
    run_worker.add_argument("--acknowledge-external-cost", action="store_true",
        help="Explicitly authorize one configured runtime attempt within its captured time budget; provider cost is unknown, not dollar-capped")
    validate = execution_subparsers.add_parser("validate-candidate")
    validate.add_argument("--execution-id", required=True)
    validate.add_argument("--project-root", type=Path, required=True)
    validate.add_argument("--head", required=True)
    validate.add_argument("--operation-id", required=True)
    _candidate_check_arguments(validate)
    validate.add_argument("--correction-worker", default="", help="Protected correction job selecting its isolated candidate worktree")
    continue_review = execution_subparsers.add_parser("continue-automatic-review", help="Resume the frozen automatic review without re-entering its inputs")
    continue_review.add_argument("--execution-id", required=True)
    continue_review.add_argument("--project-root", type=Path, required=True)
    for automatic_command in (validate, continue_review):
        automatic_command.add_argument("--local-codex-policy", type=Path, help="Existing task-bound local Reviewer policy.")
        automatic_command.add_argument("--trusted-codex-review-policy", type=Path, help="Existing task-bound trusted native Reviewer policy.")
        automatic_command.add_argument("--acknowledge-external-cost", action="store_true",
            help="Acknowledge correction runtime cost within the frozen budget; does not grant runtime admission.")
    stop_review = execution_subparsers.add_parser("stop-automatic-review", help="Stop subsequent automatic review and correction admissions")
    stop_review.add_argument("--execution-id", required=True)
    stop_review.add_argument("--project-root", type=Path, required=True)
    export_review_workbench = execution_subparsers.add_parser("export-review-workbench")
    export_review_workbench.add_argument("--execution-id", required=True)
    export_review_workbench.add_argument("--project-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None, *, codex_review_admission: CodexReviewAdmission | None = None) -> int:
    args = build_parser().parse_args(argv)
    operator_entry = args.command in {"restart", "supervise", "_operator-component"}
    if args.command in {"restart", "supervise"} and codex_review_admission is not None:
        raise ValueError("Subprocess startup requires an existing Reviewer policy path, not an in-memory authority.")
    operator_paths = {}
    candidate_reviews = None
    if operator_entry:
        from goodteam_os.operator_startup import load_config, startup_arguments
        startup_arguments(operator_review_config=args.operator_review_config,
            local_codex_policy=args.local_codex_policy, trusted_codex_review_policy=args.trusted_codex_review_policy,
            operator_review_config_sha256=args.operator_review_config_sha256)
        if args.operator_review_config is not None:
            candidate_reviews, config_digest = load_config(args.operator_review_config,
                                                          expected_digest=args.operator_review_config_sha256)
            operator_paths = {"operator_review_config": args.operator_review_config,
                              "local_codex_policy": args.local_codex_policy,
                              "trusted_codex_review_policy": args.trusted_codex_review_policy,
                              "operator_review_config_sha256": config_digest}
    # Embedding/operator startup supplies a real authority as code, never a
    # request flag, import path, package hook or persisted admission marker.
    if args.command == "local-codex-inspect":
        from goodteam_os.skills.codex_local import inspect_local_codex

        print(json.dumps(inspect_local_codex(args.codex, args.codex_home, args.model), indent=2))
        return 0
    local_policy = getattr(args, "local_codex_policy", None)
    trusted_policy = getattr(args, "trusted_codex_review_policy", None)
    if sum(value is not None for value in (local_policy, trusted_policy, codex_review_admission)) > 1:
        raise ValueError("Choose one Reviewer startup authority.")
    if trusted_policy is not None:
        from goodteam_os.skills.codex_trusted import TrustedCodexReviewAdmission

        codex_review_admission = TrustedCodexReviewAdmission(trusted_policy)
    if local_policy is not None:
        if codex_review_admission is not None:
            raise ValueError("Choose one Reviewer startup authority.")
        from goodteam_os.skills.codex_local import LocalCodexReviewAdmission

        codex_review_admission = LocalCodexReviewAdmission(local_policy)
    automatic_entry = args.command == "execution" and args.execution_command in {
        "run", "validate-candidate", "continue-automatic-review"}
    if codex_review_admission is not None and args.command not in {"dispatch", "serve"} and not automatic_entry and not operator_entry:
        raise ValueError("This Codex Reviewer composition is supported only by direct dispatch and serve, plus automatic review entry points.")
    from goodteam_os.skills.codex_runtime import review_worker_kwargs

    runtime_kwargs = review_worker_kwargs(codex_review_admission)
    if candidate_reviews is not None and any(config.reviewer_adapter != "codex"
            or config.reviewer_model != codex_review_admission.policy["model"] for config in candidate_reviews.values()):
        raise ValueError("Operator Reviewer selection must match the existing policy model and adapter.")
    if candidate_reviews is not None and args.operator_review_config.absolute().resolve().is_relative_to(
            codex_review_admission.root.resolve()):
        raise ValueError("Operator startup configuration must be outside the Worker project root.")
    if candidate_reviews is not None:
        queue = DurableOperatorQueue(getattr(args, "state_dir", None) or service_state_dir())
        for item_id in candidate_reviews:
            queued = queue.get(item_id)
            root = dict(queued.get("metadata", {})).get("project_root") if queued else None
            if root and args.operator_review_config.absolute().resolve().is_relative_to(Path(root).resolve()):
                raise ValueError("Operator startup configuration must be outside every selected Worker project root.")
    if args.command == "start":
        payload = start_workflow(
            workspace=args.workspace,
            idea=args.idea,
            project_key=args.project_key,
            model_config=args.model_config,
            model_profile=args.model_profile,
            delivery_mode=args.delivery_mode,
            source_type=args.source_type,
            project_config=args.project_config,
            role_model_overrides=args.role_model,
            dry_run=args.dry_run,
        )
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "serve":
        heartbeat = (
            ComponentHeartbeat(args.heartbeat_file, args.heartbeat_token).beat if args.heartbeat_file else None
        )
        serve(
            workspace=args.workspace,
            host=args.host,
            port=args.port,
            frontend_dist=args.frontend_dist,
            ready_file=args.ready_file,
            ready_token=args.ready_token,
            heartbeat_callback=heartbeat,
            **runtime_kwargs,
        )
        return 0
    if args.command == "supervise":
        return run_supervisor(
            workspace=args.workspace,
            host=args.host,
            port=args.port,
            frontend_dist=args.frontend_dist,
            state_dir=args.state_dir,
            identity=args.identity,
            ready_file=args.ready_file,
            ready_token=args.ready_token,
            **operator_paths,
        )
    if args.command == "_telegram-component":
        return run_telegram_component(
            workspace=args.workspace,
            ready_file=args.ready_file,
            ready_token=args.ready_token,
            heartbeat_file=args.heartbeat_file,
            heartbeat_token=args.heartbeat_token,
        )
    if args.command == "_operator-component":
        return run_operator_component(
            workspace=args.workspace,
            state_dir=args.state_dir,
            ready_file=args.ready_file,
            ready_token=args.ready_token,
            heartbeat_file=args.heartbeat_file,
            heartbeat_token=args.heartbeat_token,
            **({"candidate_reviews": candidate_reviews, **runtime_kwargs} if candidate_reviews is not None else {}),
        )
    if args.command == "restart":
        try:
            payload = restart_service(workspace=args.workspace, host=args.host, port=args.port, **operator_paths)
        except RuntimeError as exc:
            print(f"goodteam restart: {exc}", file=sys.stderr)
            return 1
        _print_service_status(payload)
        return 0
    if args.command == "stop":
        payload = stop_service()
        print(f"GoodTeam stopped (pid {payload['pid']})" if payload.get("stopped") else "GoodTeam is not running")
        return 0
    if args.command == "status":
        payload = service_status()
        if not payload.get("running"):
            print("GoodTeam is not running")
            return 1
        _print_service_status(payload)
        return 0
    if args.command == "task":
        try:
            item, created = enqueue_github_issue(
                workspace=args.workspace,
                state_dir=service_state_dir(),
                issue_url=args.issue_url,
            )
        except (RuntimeError, ValueError) as exc:
            print(f"goodteam task enqueue: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"created": created, "item": item}, indent=2))
        return 0
    if args.command == "queue":
        queue_store = DurableOperatorQueue(service_state_dir())
        if args.queue_command == "prepare-review":
            from goodteam_os.operator_startup import prepare_review_identity
            item = queue_store.get(args.item_id)
            if item is None:
                raise ValueError("Unknown operator queue item.")
            payload = prepare_review_identity(args.workspace, service_state_dir(), item)
        elif args.queue_command == "review-config":
            from goodteam_os.operator import LocalOperatorActions
            from goodteam_os.operator_startup import config_document
            item = queue_store.get(args.item_id)
            if item is None:
                raise ValueError("Unknown operator queue item.")
            actions = LocalOperatorActions(workspace=args.workspace, state_dir=service_state_dir())
            contract = actions._hydrate(item)["task_contract"]
            command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
            payload = config_document(item, contract, argv=command, criteria=args.criterion,
                base_sha=args.review_base, reviewer_model=args.reviewer_model, seconds=args.seconds)
        elif args.queue_command == "list":
            payload = {"summary": queue_store.summary(), "items": queue_store.list_items()}
        elif args.queue_command == "drain":
            payload = queue_store.set_desired_state("draining")
        else:
            payload = queue_store.set_desired_state("running")
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "init":
        result = init_project(
            projects_dir=args.projects_dir,
            key=args.key,
            name=args.name or args.key,
            repo_path=args.repo,
            model_profile=args.model_profile,
            worker_mode=args.worker_mode,
            delivery_mode=args.delivery_mode,
            force=args.force,
        )
        print(json.dumps(result, indent=2))
        print(
            f"\nWrote {result['config_path']}. Next:\n"
            f"  goodteam-os start \"Your idea\" --project-key {args.key.upper()} "
            f"--project-config {result['config_path']} --workspace ./run\n"
            f"  goodteam-os serve --workspace ./run",
            file=sys.stderr,
        )
        return 0
    if args.command == "doctor":
        target = load_project_target(args.project_config)
        print(json.dumps(check_project_target(target), indent=2))
        return 0
    if args.command == "board":
        payload = serialize_task_board(build_task_board(LocalTaskStore(args.project_root).list_tasks()))
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "workers":
        payload = {
            "project_root": str(args.project_root),
            "jobs": WorkerJobStore(args.project_root).list_jobs(),
        }
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "worker-session":
        store = WorkerJobStore(args.project_root)
        try:
            if args.worker_session_command == "status":
                payload = store.session_status(args.job_id)
            elif args.worker_session_command == "steer":
                payload = store.session_control(args.job_id, "steer", message=args.message)
            elif args.worker_session_command == "follow-up":
                payload = store.session_control(args.job_id, "follow_up", message=args.message)
            else:
                payload = store.session_control(args.job_id, "abort", reason=args.reason)
        except ValueError as exc:
            print(f"worker-session: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "failure-patterns":
        payload = FailurePatternLibrary(args.project_root).routing_readiness_report(
            min_hits=args.min_hits,
            min_confirmed=args.min_confirmed,
            max_false_positive_rate=args.max_false_positive_rate,
            min_llm_triage_samples=args.min_llm_triage_samples,
        )
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "smoke":
        payload = run_smoke_suite(
            workspace=args.workspace,
            suites=[suite.strip() for suite in args.suite.split(",") if suite.strip()],
            cycles=args.cycles,
            autonomy=args.autonomy,
            dry_run=args.dry_run,
            worker_mode=args.worker_mode,
        )
        print(json.dumps(payload, indent=2))
        return 1 if payload.get("status") in {"failed", "blocked"} else 0
    if args.command == "dispatch":
        tracks = [
            parse_dispatch_track_spec(track, base_dir=Path.cwd())
            for track in args.track
        ]
        if args.review_base or args.review_head:
            from goodteam_os.skills.review_sources import source_request

            if len(tracks) != 1 or tracks[0]["role"] != "code_review" or args.verify:
                raise ValueError("Exact Git review requires one code_review track and no shell verification command.")
            tracks[0]["review_target"] = source_request({"kind": "git_change", "base_sha": args.review_base, "head_sha": args.review_head})
        if args.review_model:
            if (not args.review_base or not args.review_head or len(tracks) != 1
                    or tracks[0]["role"] != "code_review" or tracks[0]["adapter"] != "codex"
                    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", args.review_model) is None):
                raise ValueError("An explicit review model requires one exact-source Codex Reviewer track.")
            tracks[0]["model"] = args.review_model
        verify_commands = dict(parse_dispatch_verify_spec(item) for item in args.verify)
        payload = dispatch_tracks(
            **({"codex_review_admission": codex_review_admission} if codex_review_admission is not None else {}),
            **({"logical_task_id": args.task_id} if args.task_id else {}),
            project_root=args.project_root,
            tracks=tracks,
            verify_commands=verify_commands,
            constraints_file=args.constraints_file,
            research_report_file=args.research_report,
            allow_unsafe=args.allow_unsafe,
        )
        print(json.dumps(payload, indent=2))
        return 0 if payload.get("all_ok") else 1
    if args.command == "memory-append":
        store = RoleMemoryStore(args.project_root)
        event_path = store.event_path(args.role)
        before_count = (
            sum(1 for line in event_path.read_text(encoding="utf-8").splitlines() if line.strip())
            if event_path.is_file()
            else 0
        )
        kwargs = {
            "horizon": args.horizon,
            "text": args.text,
            "source": args.source,
        }
        if args.supersedes:
            kwargs["supersedes"] = args.supersedes
        try:
            event = store.append_memory_event(args.role, **kwargs)
        except ValueError as exc:
            print(f"memory-append: {exc}", file=sys.stderr)
            return 2
        after_count = sum(1 for line in event_path.read_text(encoding="utf-8").splitlines() if line.strip())
        payload = {
            "status": "ok" if after_count > before_count else "deduped",
            "role": event["role_key"],
            "horizon": event["horizon"],
            "source": event.get("source", ""),
        }
        print(json.dumps(payload))
        return 0
    if args.command == "tg-poll":
        payload = poll_telegram_approvals(workspace=args.workspace, project_key=args.project_key, once=args.once)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "execution":
        try:
            payload = _run_execution_command(args, **runtime_kwargs)
        except ValueError as exc:
            print(f"execution: {exc}", file=sys.stderr)
            if str(getattr(exc, "code", "")).startswith("approved_plan_"):
                print("Use existing RFC/security (or fix-plan) reviews for current approved intent, then "
                      "execution bind-plan with its workflow run and exact task heading before the first Builder run. "
                      "Stale captured bindings cannot be silently retargeted; see docs/approved-plan-binding.md.",
                      file=sys.stderr)
            return 2
        print(json.dumps(payload, indent=2))
        return 0
    return 1


def _print_service_status(payload: dict[str, object]) -> None:
    started_at = payload.get("started_at")
    if isinstance(started_at, int | float):
        started_label = datetime.fromtimestamp(started_at).astimezone().isoformat(timespec="seconds")
    else:
        started_label = str(started_at or "unknown")
    print("GoodTeam service")
    supervisor = payload.get("supervisor", {})
    supervisor = supervisor if isinstance(supervisor, dict) else {}
    print("Running: yes")
    print(f"Supervisor: {supervisor.get('status', 'unknown')}")
    print(f"Desired state: {payload.get('desired_state', 'running')}")
    print(f"URL: http://{payload['host']}:{payload['port']}")
    print(f"PID: {payload['pid']}")
    print(f"Workspace: {payload['workspace']}")
    print(f"Frontend dist: {payload.get('frontend_dist', 'unknown')}")
    print(f"Log path: {payload['log']}")
    print(f"Started at: {started_label}")
    components = payload.get("components", {})
    if isinstance(components, dict):
        print("\nComponents")
        for name in ("web", "telegram", "operator"):
            component = components.get(name, {})
            component = component if isinstance(component, dict) else {}
            print(
                f"{name.title()}: {component.get('status', 'unknown')} "
                f"(ready={str(bool(component.get('ready'))).lower()}, restarts={component.get('restart_count', 0)})"
            )
    queue = payload.get("queue", {})
    if isinstance(queue, dict):
        active = queue.get("active")
        active = active if isinstance(active, dict) else {}
        metadata = active.get("metadata", {})
        metadata = metadata if isinstance(metadata, dict) else {}
        print("\nQueue")
        print(f"Depth: {queue.get('depth', 0)}")
        print(f"Active task: {active.get('id', 'none')}")
        if active:
            print(f"Project: {active.get('project_key', 'unknown')}")
            print(f"Phase: {active.get('phase', 'unknown')}")
            print(f"Issue: {active.get('source_url', '')}")
            print(f"PR: {metadata.get('pr_url', 'none')}")
            print(f"Head: {str(metadata.get('head_sha', ''))[:12] or 'none'}")
            print(f"Attempt: {active.get('attempt', 0)}")
            print(f"Waiting decision: {'yes' if active.get('status') == 'waiting' else 'no'}")
            print(f"Last failure: {active.get('last_failure_class', '') or 'none'}")


def _run_execution_command(args: argparse.Namespace, *, codex_review_admission=None) -> dict[str, object]:
    from goodteam_os.skills.codex_runtime import review_worker_kwargs

    api = ExecutionAPI(args.project_root, **review_worker_kwargs(codex_review_admission))
    if args.execution_command == "intake-github-issue":
        return api.intake_github_issue(
            repo=args.repo,
            issue_number=args.issue,
            adapter=args.adapter,
            role=args.role,
        )
    if args.execution_command == "bind-plan":
        from goodteam_os.execution.approved_plan import ApprovedPlanBinding
        value = ApprovedPlanBinding(args.project_root).register(args.execution_id,
            workflow_run_id=args.workflow_run, task_anchor=args.task_anchor)
        return {"status": "bound", "execution_id": args.execution_id, "binding": value}
    if args.execution_command == "run":
        command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        plan = None
        if command or args.review_base or args.criterion or args.seconds != 30:
            if not command or not args.review_base or not args.criterion:
                raise ValueError("The first delivery run requires --review-base, --criterion and an explicit check command")
            plan = {"base_sha": args.review_base, "argv": command, "criteria": args.criterion, "seconds": args.seconds}
        return api.run_execution(args.execution_id, candidate_review=plan, resume_candidate_review=True,
                                 acknowledge_external_cost=args.acknowledge_external_cost)
    if args.execution_command == "pr":
        return api.create_or_reuse_pr(args.execution_id)
    if args.execution_command == "ci":
        return api.observe_ci(args.execution_id)
    if args.execution_command == "replay":
        return api.replay(args.execution_id)
    if args.execution_command == "review":
        return api.get_review_workbench(args.execution_id)
    if args.execution_command == "start-correction-loop":
        return api.start_correction_loop(args.execution_id, receipt_id=args.validation_receipt, head_sha=args.head,
                                         max_rounds=args.max_rounds, wall_seconds=args.wall_seconds)
    if args.execution_command == "stop-correction-loop":
        return api.stop_correction_loop(args.execution_id, loop_id=args.loop_id)
    if args.execution_command == "prepare-correction-worker":
        return api.prepare_correction_worker(args.execution_id, loop_id=args.loop_id, head_sha=args.head,
            receipt_id=args.validation_receipt, operation_id=args.operation_id, max_seconds=args.max_seconds)
    if args.execution_command == "run-correction-worker":
        return api.run_correction_worker(args.execution_id, args.job_id,
            acknowledge_external_cost=args.acknowledge_external_cost)
    if args.execution_command == "validate-candidate":
        command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        optional = {"correction_worker": args.correction_worker} if args.correction_worker else {}
        return api.validate_and_review_candidate(args.execution_id, operation_id=args.operation_id, head_sha=args.head,
            argv=command, criteria=args.criterion, seconds=args.seconds, review_base=args.review_base,
            acknowledge_external_cost=args.acknowledge_external_cost, **optional)
    if args.execution_command == "continue-automatic-review":
        return api.advance_automatic_review(args.execution_id, acknowledge_external_cost=args.acknowledge_external_cost)
    if args.execution_command == "stop-automatic-review":
        return api.stop_automatic_review(args.execution_id)
    if args.execution_command == "record-review-decision":
        if args.validation_receipt or args.head:
            if not args.validation_receipt or not args.head:
                raise ValueError("Both --validation-receipt and --head are required")
            return api.attest_candidate_review(args.execution_id, receipt_id=args.validation_receipt,
                                              head_sha=args.head, reviewer=args.reviewer,
                                              decision=args.decision, reason=args.reason)
        return api.record_review_decision(
            args.execution_id,
            args.decision,
            args.reviewer,
            args.reason,
        )
    if args.execution_command == "export-review-workbench":
        return api.export_review_workbench_html(args.execution_id)
    raise ValueError(f"Unknown execution command: {args.execution_command}")


if __name__ == "__main__":
    raise SystemExit(main())
