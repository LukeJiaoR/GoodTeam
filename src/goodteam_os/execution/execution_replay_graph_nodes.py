from __future__ import annotations

from typing import Any

from goodteam_os.execution.execution_replay_graph_utils import (
    add_node,
    add_warning,
    dict_items,
    dict_value,
    event_payload,
    events_for,
    first_event_payload_value,
    latest_event,
    surface_source_event,
    warning_code,
)


def build_replay_graph_nodes(
    manifest: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    context = _NodeContext(manifest)
    context.build()
    return context.nodes, context.warnings


class _NodeContext:
    def __init__(self, manifest: dict[str, Any]):
        self.manifest = manifest
        self.events = dict_items(manifest.get("ledger_events"))
        self.nodes: list[dict[str, Any]] = []
        self.warnings: list[dict[str, str]] = []

    def build(self) -> None:
        self._add_intake()
        self._add_surface("task_contract", "task_contract", "Task contract", "task_contract_missing")
        self._add_surface("runtime_policy", "runtime_policy", "Runtime policy", "runtime_policy_missing")
        self._add_worker_job()
        self._add_event_nodes("sandbox_created", "sandbox_created", "Sandbox created")
        self._add_artifacts()
        self._add_event_nodes("sandbox_destroyed", "sandbox_destroyed", "Sandbox destroyed")
        self._add_pr()
        self._add_ci()
        self._add_review()
        self._add_surface("github_broker_status", "broker_status", "Broker status", "broker_status_missing")
        self._add_merge()
        self._add_finalize()
        self._add_memory()
        self._add_reviewer_decision()
        self._add_incomplete_warning()

    def _add_intake(self) -> None:
        event = latest_event(self.events, "intake_created")
        issue_ref = str(self.manifest.get("source_issue", ""))
        if not event and not issue_ref:
            return
        add_node(
            self.nodes,
            "intake",
            "intake",
            "Intake",
            status="recorded",
            event=event,
            data={"source_issue": issue_ref, "source": dict_value(self.manifest.get("source"))},
        )

    def _add_surface(self, key: str, node_type: str, label: str, missing_code: str) -> None:
        surface = dict_value(self.manifest.get(key))
        available = bool(surface.get("available"))
        warnings = [] if available else [{"code": missing_code, "message": missing_code}]
        add_node(
            self.nodes,
            node_type,
            node_type,
            label,
            status="available" if available else "missing",
            event=surface_source_event(surface, self.events),
            data={"schema_version": str(surface.get("schema_version", "")), "available": available},
            warnings=warnings,
        )
        if not available:
            add_warning(self.nodes, self.warnings, missing_code, after_node_id=node_type)

    def _add_worker_job(self) -> None:
        event = latest_event(self.events, "worker_job_created")
        worker_job_id = first_event_payload_value(self.events, "worker_job_id")
        if not event and not worker_job_id:
            return
        add_node(
            self.nodes,
            "worker_job",
            "worker_job",
            "Worker job",
            status="created",
            event=event,
            data={
                "worker_job_id": worker_job_id,
                "adapter": event_payload(event).get("adapter", ""),
                "role": event_payload(event).get("role", ""),
            },
        )

    def _add_event_nodes(self, transition: str, node_type: str, label: str) -> None:
        for index, event in enumerate(events_for(self.events, transition), start=1):
            node_id = node_type if index == 1 else f"{node_type}:{index}"
            add_node(
                self.nodes,
                node_id,
                node_type,
                label if index == 1 else f"{label} {index}",
                status="recorded",
                event=event,
                data=event_payload(event),
            )

    def _add_artifacts(self) -> None:
        artifact_index = self._add_recorded_artifacts()
        if artifact_index:
            return
        for expected in dict_items(dict_value(self.manifest.get("task_contract")).get("expected_artifacts")):
            artifact_index += 1
            add_node(
                self.nodes,
                "artifact" if artifact_index == 1 else f"artifact:{artifact_index}",
                "artifact",
                "Expected artifact" if artifact_index == 1 else f"Expected artifact {artifact_index}",
                status="expected",
                event=None,
                data={
                    "name": str(expected.get("name", "")),
                    "kind": str(expected.get("kind", "")),
                    "path": str(expected.get("path", "")),
                    "required": bool(expected.get("required", False)),
                },
            )

    def _add_recorded_artifacts(self) -> int:
        artifact_index = 0
        for event in self.events:
            payload = event_payload(event)
            transition = str(payload.get("transition", ""))
            if transition not in {"execution_completed", "diff_artifact_persisted"}:
                continue
            path = str(payload.get("result_path") or payload.get("artifact_path") or "").strip()
            if not path:
                continue
            artifact_index += 1
            add_node(
                self.nodes,
                "artifact" if artifact_index == 1 else f"artifact:{artifact_index}",
                "artifact",
                "Artifact" if artifact_index == 1 else f"Artifact {artifact_index}",
                status="recorded",
                event=event,
                data={
                    "path": path,
                    "kind": "pr_diff" if transition == "diff_artifact_persisted" else "worker_result",
                    "transition": transition,
                },
            )
        return artifact_index

    def _add_pr(self) -> None:
        pr = dict_value(self.manifest.get("pr"))
        broker_pr = dict_value(dict_value(self.manifest.get("github_broker_status")).get("pr"))
        number = pr.get("number") if pr.get("number") is not None else broker_pr.get("number")
        if number is None:
            return
        add_node(
            self.nodes,
            "pr",
            "pr",
            "Pull request",
            status=str(broker_pr.get("state") or "recorded"),
            event=latest_event(self.events, "pr_opened"),
            data={
                "ref": pr.get("ref", ""),
                "number": number,
                "branch_name": pr.get("branch_name") or broker_pr.get("branch", ""),
                "commit_sha": pr.get("commit_sha") or broker_pr.get("commit_sha", ""),
            },
        )

    def _add_ci(self) -> None:
        observations = dict_items(self.manifest.get("ci_observations"))
        broker_ci = dict_value(dict_value(self.manifest.get("github_broker_status")).get("ci"))
        if not observations and not broker_ci:
            return
        ci_summary = dict_value(self.manifest.get("ci_summary"))
        add_node(
            self.nodes,
            "ci",
            "ci",
            "CI",
            status=str(broker_ci.get("observed_state") or ci_summary.get("state") or "unknown"),
            event=latest_event(self.events, "ci_observed"),
            data={"summary": ci_summary, "observations": observations},
        )

    def _add_review(self) -> None:
        observations = dict_items(self.manifest.get("review_observations"))
        broker_review = dict_value(dict_value(self.manifest.get("github_broker_status")).get("review"))
        if not observations and not broker_review:
            return
        add_node(
            self.nodes,
            "review",
            "review",
            "Review",
            status=str(broker_review.get("state") or "unknown"),
            event=latest_event(self.events, "review_recorded") or latest_event(self.events, "pr_comment_recorded"),
            data={"summary": dict_value(self.manifest.get("review_summary")), "observations": observations},
        )

    def _add_merge(self) -> None:
        merge = dict_value(self.manifest.get("merge_summary"))
        if merge.get("merged"):
            add_node(self.nodes, "merge", "merge", "Merge", status="merged", event=latest_event(self.events, "merge_completed"), data=merge)

    def _add_finalize(self) -> None:
        event = latest_event(self.events, "provenance_written")
        if event is not None:
            add_node(self.nodes, "finalize", "finalize", "Finalize provenance", status="recorded", event=event, data=event_payload(event))

    def _add_memory(self) -> None:
        refs = dict_items(self.manifest.get("memory_provenance_refs"))
        if refs:
            add_node(
                self.nodes,
                "memory",
                "memory",
                "Memory provenance",
                status="complete",
                event=latest_event(self.events, "provenance_written"),
                data={"refs": refs, "integrity": dict_value(self.manifest.get("memory_provenance_integrity"))},
            )

    def _add_reviewer_decision(self) -> None:
        event = latest_event(self.events, "review_decision_recorded")
        if event is not None:
            add_node(
                self.nodes,
                "reviewer_decision",
                "reviewer_decision",
                "Reviewer decision",
                status=str(event_payload(event).get("decision", "recorded")),
                event=event,
                data=event_payload(event),
            )

    def _add_incomplete_warning(self) -> None:
        reason = str(self.manifest.get("incomplete_reason", "")).strip()
        if reason:
            add_warning(self.nodes, self.warnings, warning_code(reason), message=reason)
