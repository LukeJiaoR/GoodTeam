from __future__ import annotations

import json
from typing import Any

from goodteam_os.execution.execution_replay_graph_nodes import build_replay_graph_nodes
from goodteam_os.execution.execution_replay_graph_utils import (
    EXECUTION_REPLAY_GRAPH_SCHEMA_VERSION,
    KNOWN_NODE_TYPES,
    dict_items,
    event_payload,
)


def execution_replay_graph_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    nodes, warnings = build_replay_graph_nodes(manifest)
    edges = _edges(nodes)
    graph = {
        "available": True,
        "schema_version": EXECUTION_REPLAY_GRAPH_SCHEMA_VERSION,
        "summary": {
            "final_status": str(manifest.get("final_status", "")),
            "incomplete_reason": str(manifest.get("incomplete_reason", "")),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "warning_count": len(warnings),
            "complete": _is_complete(manifest, warnings),
        },
        "nodes": nodes,
        "edges": edges,
        "timeline": _timeline(nodes),
        "nodes_by_type": _nodes_by_type(nodes),
        "raw_event_refs": _raw_event_refs(dict_items(manifest.get("ledger_events"))),
        "warnings": warnings,
    }
    return _json_ready(graph)


def _edges(nodes: list[dict[str, Any]]) -> list[dict[str, str]]:
    main_nodes = [node for node in nodes if node["type"] != "warning"]
    edges = []
    for source, target in zip(main_nodes, main_nodes[1:], strict=False):
        edges.append(
            {
                "source": str(source["id"]),
                "target": str(target["id"]),
                "kind": "sequence",
                "label": f"{source['type']} -> {target['type']}",
            }
        )
    for warning in [node for node in nodes if node["type"] == "warning"]:
        data = warning.get("data") if isinstance(warning.get("data"), dict) else {}
        after = str(data.get("after", ""))
        if after:
            edges.append(
                {
                    "source": after,
                    "target": str(warning["id"]),
                    "kind": "warning",
                    "label": "fallback warning",
                }
            )
    return edges


def _timeline(nodes: list[dict[str, Any]]) -> list[dict[str, str]]:
    timeline = []
    for node in nodes:
        refs = node.get("refs") if isinstance(node.get("refs"), dict) else {}
        transitions = refs.get("transitions")
        transition = str(transitions[0]) if isinstance(transitions, list) and transitions else ""
        timeline.append(
            {
                "node_id": str(node["id"]),
                "type": str(node["type"]),
                "status": str(node["status"]),
                "occurred_at": str(node.get("occurred_at", "")),
                "transition": transition,
            }
        )
    return timeline


def _nodes_by_type(nodes: list[dict[str, Any]]) -> dict[str, list[str]]:
    nodes_by_type = {node_type: [] for node_type in KNOWN_NODE_TYPES}
    for node in nodes:
        nodes_by_type.setdefault(str(node["type"]), []).append(str(node["id"]))
    return nodes_by_type


def _raw_event_refs(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "event_id": str(event.get("event_id", "")),
            "transition": str(event_payload(event).get("transition", "")),
            "created_at": str(event.get("created_at", "")),
        }
        for event in events
    ]


def _is_complete(manifest: dict[str, Any], warnings: list[dict[str, str]]) -> bool:
    return str(manifest.get("final_status", "")) == "completed" and not warnings


def _json_ready(value: Any) -> Any:
    return json.loads(json.dumps(value))
