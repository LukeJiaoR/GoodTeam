from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class ResourceKind(StrEnum):
    MARKDOWN_ARTIFACT = "markdown_artifact"
    PROPOSED_MARKDOWN_ARTIFACT = "proposed_markdown_artifact"
    TASK_LEDGER = "task_ledger"
    RUNTIME_RESOURCE = "runtime_resource"
    WORKER_JOB_STORE = "worker_job_store"
    MEMORY = "memory"
    PROVENANCE = "provenance"
    SMOKE_REPORT = "smoke_report"
    EXTERNAL_REFERENCE = "external_reference"


class ResourceScope(StrEnum):
    RUN = "run"
    PROJECT_RUNTIME = "project_runtime"
    WORKSPACE_STATE = "workspace_state"
    EXTERNAL = "external"


class ResourceCapability(StrEnum):
    READ = "read"
    PREVIEW = "preview"
    EDIT = "edit"
    REVIEW = "review"
    APPROVE = "approve"
    DIFF = "diff"
    DOWNLOAD = "download"
    BOARD_SOURCE = "board_source"
    PROVENANCE_SOURCE = "provenance_source"
    WORKER_SOURCE = "worker_source"


@dataclass
class ResourceDescriptor:
    key: str
    kind: ResourceKind
    scope: ResourceScope
    path: str = ""
    url: str = ""
    mime: str = ""
    title: str = ""
    producer: str = ""
    consumer: str = ""
    capabilities: list[ResourceCapability] = field(default_factory=list)
    legacy_artifact: bool = False
    base_key: str = ""


REVIEW_ARTIFACT_KEYS = {
    "takeover",
    "prd",
    "rfc",
    "fix_plan",
    "qa_report",
    "staging_report",
    "release_notes",
}


def _capabilities(*values: ResourceCapability) -> list[ResourceCapability]:
    return list(dict.fromkeys(values))


def _descriptor(
    *,
    key: str,
    kind: ResourceKind,
    scope: ResourceScope,
    path: str | Path = "",
    url: str = "",
    mime: str = "",
    title: str = "",
    producer: str = "",
    consumer: str = "",
    capabilities: list[ResourceCapability] | None = None,
    legacy_artifact: bool = True,
    base_key: str = "",
) -> ResourceDescriptor:
    return ResourceDescriptor(
        key=key,
        kind=kind,
        scope=scope,
        path=str(path) if path else "",
        url=url,
        mime=mime,
        title=title,
        producer=producer,
        consumer=consumer,
        capabilities=list(capabilities or []),
        legacy_artifact=legacy_artifact,
        base_key=base_key,
    )


def _store_resource(state: Any, descriptor: ResourceDescriptor) -> ResourceDescriptor:
    if not hasattr(state, "resources") or getattr(state, "resources") is None:
        state.resources = {}
    state.resources[descriptor.key] = descriptor
    if descriptor.legacy_artifact and descriptor.path:
        state.artifacts[descriptor.key] = descriptor.path
    return descriptor


def register_markdown_artifact(
    state: Any,
    key: str,
    path: str | Path,
    *,
    title: str = "",
    producer: str = "",
    reviewable: bool = True,
) -> ResourceDescriptor:
    capabilities = _capabilities(
        ResourceCapability.READ,
        ResourceCapability.PREVIEW,
        ResourceCapability.EDIT,
        ResourceCapability.DOWNLOAD,
    )
    if reviewable:
        capabilities.extend([ResourceCapability.REVIEW, ResourceCapability.APPROVE, ResourceCapability.DIFF])
    return _store_resource(
        state,
        _descriptor(
            key=key,
            kind=ResourceKind.MARKDOWN_ARTIFACT,
            scope=ResourceScope.RUN,
            path=path,
            mime="text/markdown",
            title=title or key.replace("_", " ").title(),
            producer=producer,
            capabilities=_capabilities(*capabilities),
        ),
    )


def register_proposed_markdown_artifact(
    state: Any,
    key: str,
    path: str | Path,
    *,
    base_key: str,
) -> ResourceDescriptor:
    return _store_resource(
        state,
        _descriptor(
            key=key,
            kind=ResourceKind.PROPOSED_MARKDOWN_ARTIFACT,
            scope=ResourceScope.RUN,
            path=path,
            mime="text/markdown",
            title=f"Proposed {base_key.replace('_', ' ').title()}",
            capabilities=_capabilities(
                ResourceCapability.READ,
                ResourceCapability.PREVIEW,
                ResourceCapability.DIFF,
                ResourceCapability.DOWNLOAD,
            ),
            base_key=base_key,
        ),
    )


def register_task_ledger(state: Any, path: str | Path) -> ResourceDescriptor:
    return _store_resource(
        state,
        _descriptor(
            key="task_ledger",
            kind=ResourceKind.TASK_LEDGER,
            scope=ResourceScope.PROJECT_RUNTIME,
            path=path,
            mime="application/json",
            title="Task Ledger",
            consumer="agile_board",
            capabilities=_capabilities(ResourceCapability.READ, ResourceCapability.BOARD_SOURCE),
        ),
    )


def register_runtime_resource(
    state: Any,
    key: str,
    path: str | Path,
    *,
    kind: ResourceKind = ResourceKind.RUNTIME_RESOURCE,
    mime: str = "",
    capabilities: list[ResourceCapability] | None = None,
    title: str = "",
    consumer: str = "",
) -> ResourceDescriptor:
    """Register a typed project-runtime extension resource.

    Concrete helpers such as ``register_task_ledger`` encode product-specific
    defaults. This generic entry point remains the supported seam for runtime
    kinds added during the resource-registry migration without bypassing the
    typed descriptor and legacy projection rules.
    """
    return _store_resource(
        state,
        _descriptor(
            key=key,
            kind=kind,
            scope=ResourceScope.PROJECT_RUNTIME,
            path=path,
            mime=mime,
            title=title or key.replace("_", " ").title(),
            consumer=consumer,
            capabilities=capabilities or _capabilities(ResourceCapability.READ, ResourceCapability.DOWNLOAD),
        ),
    )


def register_external_reference(
    state: Any,
    key: str,
    url: str,
    *,
    title: str,
    capabilities: list[ResourceCapability] | None = None,
) -> ResourceDescriptor:
    """Register a typed URL-only resource without adding an artifact path.

    External integrations use this boundary to declare capabilities while
    keeping URL references out of the local-file compatibility projection.
    """
    return _store_resource(
        state,
        _descriptor(
            key=key,
            kind=ResourceKind.EXTERNAL_REFERENCE,
            scope=ResourceScope.EXTERNAL,
            url=url,
            title=title,
            capabilities=capabilities or _capabilities(ResourceCapability.READ),
            legacy_artifact=False,
        ),
    )


def resource_to_dict(resource: ResourceDescriptor) -> dict[str, Any]:
    return {
        "key": resource.key,
        "kind": str(resource.kind),
        "scope": str(resource.scope),
        "path": resource.path,
        "url": resource.url,
        "mime": resource.mime,
        "title": resource.title,
        "producer": resource.producer,
        "consumer": resource.consumer,
        "capabilities": [str(capability) for capability in resource.capabilities],
        "legacy_artifact": resource.legacy_artifact,
        "base_key": resource.base_key,
    }


def resource_from_dict(payload: dict[str, Any]) -> ResourceDescriptor:
    return ResourceDescriptor(
        key=str(payload.get("key", "")),
        kind=ResourceKind(str(payload.get("kind", ResourceKind.RUNTIME_RESOURCE))),
        scope=ResourceScope(str(payload.get("scope", ResourceScope.PROJECT_RUNTIME))),
        path=str(payload.get("path", "")),
        url=str(payload.get("url", "")),
        mime=str(payload.get("mime", "")),
        title=str(payload.get("title", "")),
        producer=str(payload.get("producer", "")),
        consumer=str(payload.get("consumer", "")),
        capabilities=[ResourceCapability(str(value)) for value in payload.get("capabilities", [])],
        legacy_artifact=bool(payload.get("legacy_artifact", True)),
        base_key=str(payload.get("base_key", "")),
    )


def resources_to_json(resources: dict[str, ResourceDescriptor]) -> dict[str, dict[str, Any]]:
    return {key: resource_to_dict(resource) for key, resource in resources.items()}


def resources_from_json(payload: dict[str, Any]) -> dict[str, ResourceDescriptor]:
    return {key: resource_from_dict(dict(value, key=str(value.get("key", key)))) for key, value in payload.items()}


def legacy_artifacts_view(resources: dict[str, ResourceDescriptor]) -> dict[str, str]:
    return {
        key: resource.path
        for key, resource in resources.items()
        if resource.legacy_artifact and resource.path
    }


def resource_path(
    resources: dict[str, ResourceDescriptor],
    key: str,
    *,
    capability: ResourceCapability | None = None,
) -> Path | None:
    resource = resources.get(key)
    if resource is None or not resource.path:
        return None
    if capability is not None and capability not in resource.capabilities:
        return None
    return Path(resource.path)


def derive_resources_from_legacy_artifacts(
    artifacts: dict[str, str],
    *,
    run_id: str,
    workspace: Path,
) -> dict[str, ResourceDescriptor]:
    resources: dict[str, ResourceDescriptor] = {}
    run_root = (workspace / "artifacts" / "runs" / run_id).resolve()
    for key, artifact_path in artifacts.items():
        path = Path(artifact_path)
        resolved = (workspace / path).resolve() if not path.is_absolute() else path.resolve()
        suffix = resolved.suffix.lower()
        if key == "task_ledger":
            descriptor = _descriptor(
                key=key,
                kind=ResourceKind.TASK_LEDGER,
                scope=ResourceScope.PROJECT_RUNTIME,
                path=artifact_path,
                mime="application/json",
                title="Task Ledger",
                consumer="agile_board",
                capabilities=_capabilities(ResourceCapability.READ, ResourceCapability.BOARD_SOURCE),
            )
        elif key.endswith("_proposed"):
            descriptor = _descriptor(
                key=key,
                kind=ResourceKind.PROPOSED_MARKDOWN_ARTIFACT,
                scope=ResourceScope.RUN,
                path=artifact_path,
                mime="text/markdown",
                title=f"Proposed {key[:-9].replace('_', ' ').title()}",
                capabilities=_capabilities(
                    ResourceCapability.READ,
                    ResourceCapability.PREVIEW,
                    ResourceCapability.DIFF,
                    ResourceCapability.DOWNLOAD,
                ),
                base_key=key[:-9],
            )
        elif key in REVIEW_ARTIFACT_KEYS:
            descriptor = _descriptor(
                key=key,
                kind=ResourceKind.MARKDOWN_ARTIFACT,
                scope=ResourceScope.RUN,
                path=artifact_path,
                mime="text/markdown",
                title=key.replace("_", " ").title(),
                capabilities=_capabilities(
                    ResourceCapability.READ,
                    ResourceCapability.PREVIEW,
                    ResourceCapability.EDIT,
                    ResourceCapability.REVIEW,
                    ResourceCapability.APPROVE,
                    ResourceCapability.DIFF,
                    ResourceCapability.DOWNLOAD,
                ),
            )
        elif suffix in {".md", ".markdown"} and run_root in [resolved, *resolved.parents]:
            descriptor = _descriptor(
                key=key,
                kind=ResourceKind.MARKDOWN_ARTIFACT,
                scope=ResourceScope.RUN,
                path=artifact_path,
                mime="text/markdown",
                title=key.replace("_", " ").title(),
                capabilities=_capabilities(
                    ResourceCapability.READ,
                    ResourceCapability.PREVIEW,
                    ResourceCapability.DOWNLOAD,
                ),
            )
        else:
            descriptor = _descriptor(
                key=key,
                kind=ResourceKind.RUNTIME_RESOURCE,
                scope=ResourceScope.PROJECT_RUNTIME,
                path=artifact_path,
                title=key.replace("_", " ").title(),
                capabilities=_capabilities(ResourceCapability.READ, ResourceCapability.DOWNLOAD),
            )
        resources[key] = descriptor
    return resources
