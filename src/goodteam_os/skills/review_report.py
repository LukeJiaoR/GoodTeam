"""Bounded advisory report protocol, validated against parent-captured sources.

No identifier or source supplied by the model grants authority. Matching an
excerpt proves only that the reference exists in the captured input, not that
its interpretation, a claimed check, or a finding is independently correct.
"""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Mapping

from .models import SkillError, canonical_json, content_digest, resource_path

REPORT_SCHEMA = "goodteam.code-review-report.v1"
MAX_REPORT_BYTES = 256 * 1024
MAX_ITEMS = 128
SEVERITIES = frozenset({"critical", "high", "medium", "low", "info"})
CHECK_STATUSES = frozenset({"checked", "not_run", "inconclusive"})


def _error(code: str, message: str):
    raise SkillError(code, message)


def _object(value: object, keys: set[str], field: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        _error("review_report_shape", f"{field} must contain exactly the documented fields.")
    return value


def _text(value: object, field: str, *, maximum: int = 8192) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > maximum:
        _error("review_report_text", f"{field} must contain nonempty bounded UTF-8 text.")
    return value


def _items(value: object, field: str, *, nonempty: bool = False) -> list:
    if not isinstance(value, list) or len(value) > MAX_ITEMS or (nonempty and not value):
        _error("review_report_items", f"{field} must be a bounded list with the required entries.")
    return value


def _identifier(value: object, field: str) -> str:
    import re

    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value) is None:
        _error("review_report_identifier", f"{field} requires a short report-local identifier.")
    return value


def _unique(items: list[dict], field: str) -> None:
    if len({item["id"] for item in items}) != len(items):
        _error("review_report_duplicate_id", f"{field} contains duplicate report-local identifiers.")


def _evidence_ids(value: object, known: set[str], field: str, *, required: bool) -> list[str]:
    ids = _items(value, field, nonempty=required)
    for item in ids:
        _identifier(item, field)
    if len(set(ids)) != len(ids) or any(item not in known for item in ids):
        _error("review_report_evidence_reference", f"{field} must refer to distinct evidence in this report.")
    return list(ids)


def decode_report(data: bytes) -> dict:
    """Do not repair Markdown fences, missing keys, nonfinite values or JSON."""
    if not isinstance(data, bytes) or not data or len(data) > MAX_REPORT_BYTES:
        _error("review_report_size", "The final report must be nonempty and within its byte limit.")

    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("nonfinite value")

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
        canonical_json(value).encode("utf-8")
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise SkillError("review_report_json", "The final report must be one finite, duplicate-free UTF-8 JSON object.") from exc
    if not isinstance(value, dict):
        _error("review_report_shape", "The final report must be a JSON object.")
    return value


def validate_report(data: bytes, *, sources: Mapping[str, bytes]) -> dict[str, Any]:
    """Validate only against fixed bytes provided by the control plane.

    ``sources`` is NOT a map of filesystem paths to open. No filesystem, network,
    callback or model-generated identity is consulted by this validator.
    """
    payload = decode_report(data)
    _object(payload, {"schema_version", "summary", "findings", "evidence", "checks"}, "report")
    if payload["schema_version"] != REPORT_SCHEMA:
        _error("review_report_schema", "The final report uses an unsupported report protocol.")
    summary = _text(payload["summary"], "summary", maximum=16384)
    evidence = []
    for raw in _items(payload["evidence"], "evidence", nonempty=True):
        item = _object(raw, {"id", "path", "line_start", "line_end", "quote", "observation"}, "evidence entry")
        local_id = _identifier(item["id"], "evidence.id")
        path = item["path"]
        if not isinstance(path, str) or path not in sources:
            _error("review_report_source", "Evidence must address a source in the captured review input.")
        resource_path(path)
        start, end = item["line_start"], item["line_end"]
        if type(start) is not int or type(end) is not int or start < 1 or end < start:
            _error("review_report_source_range", "Evidence requires an ordered, positive source line range.")
        try:
            source = sources[path]
            lines = source.decode("utf-8").splitlines(keepends=True)
        except (UnicodeError, AttributeError) as exc:
            raise SkillError("review_report_source", "The captured source is not supported UTF-8 text.") from exc
        if end > len(lines):
            _error("review_report_source_range", "Evidence line range is outside the captured source.")
        quote = _text(item["quote"], "evidence.quote", maximum=16384)
        exact = "".join(lines[start - 1:end])
        if quote not in {exact, exact.rstrip("\r\n")}:
            _error("review_report_source_mismatch", "Evidence excerpt does not match its captured source lines.")
        evidence.append({"id": local_id, "path": path, "line_start": start, "line_end": end,
                         "quote": quote, "observation": _text(item["observation"], "evidence.observation"),
                         "source_digest": content_digest(source), "reference_status": "matched_input"})
    _unique(evidence, "evidence")
    known = {item["id"] for item in evidence}
    findings = []
    for raw in _items(payload["findings"], "findings"):
        item = _object(raw, {"id", "severity", "title", "detail", "evidence_ids", "recommendation"}, "finding")
        if not isinstance(item["severity"], str) or item["severity"] not in SEVERITIES:
            _error("review_report_severity", "A finding must use a documented severity.")
        findings.append({"id": _identifier(item["id"], "finding.id"), "severity": item["severity"],
                         "title": _text(item["title"], "finding.title", maximum=1024),
                         "detail": _text(item["detail"], "finding.detail"),
                         "evidence_ids": _evidence_ids(item["evidence_ids"], known, "finding.evidence_ids", required=True),
                         "recommendation": _text(item["recommendation"], "finding.recommendation")})
    _unique(findings, "findings")
    checks = []
    for raw in _items(payload["checks"], "checks", nonempty=True):
        item = _object(raw, {"name", "status", "evidence_ids", "detail"}, "check")
        if not isinstance(item["status"], str) or item["status"] not in CHECK_STATUSES:
            _error("review_report_check", "A check must be checked, not_run or inconclusive.")
        checks.append({"name": _text(item["name"], "check.name", maximum=1024), "status": item["status"],
                       "detail": _text(item["detail"], "check.detail"),
                       "evidence_ids": _evidence_ids(item["evidence_ids"], known, "check.evidence_ids",
                                                     required=item["status"] == "checked")})
    if not any(item["status"] == "checked" for item in checks):
        _error("review_report_no_checks", "A report must describe at least one source-backed check; an unperformed review is not a result.")
    return {"schema_version": REPORT_SCHEMA, "summary": summary, "findings": findings,
            "evidence": evidence, "checks": checks, "advisory": True,
            "judgment_verification": "not_independently_verified"}


def report_output_instructions(source_names: list[str]) -> str:
    """Output format only; no mandatory review methodology or fabricated finding."""
    example = {
        "schema_version": REPORT_SCHEMA, "summary": "Describe the review outcome and limitations.",
        "findings": [],
        "evidence": [{"id": "E1", "path": source_names[0], "line_start": 1, "line_end": 1,
                      "quote": "Exact source line, not this placeholder.", "observation": "Describe what the source shows."}],
        "checks": [{"name": "Name the check actually performed.", "status": "checked", "evidence_ids": ["E1"],
                    "detail": "Describe the basis and limits of the check."},
                   {"name": "Name an unperformed check when relevant.", "status": "not_run", "evidence_ids": [],
                    "detail": "Explain why it was not performed."}],
    }
    finding = {"id": "F1", "severity": "medium", "title": "Specific issue", "detail": "Explain the issue.",
               "evidence_ids": ["E1"], "recommendation": "Describe a concrete remedy."}
    return ("\n## Controlled advisory result channel\n"
            "Return exactly one JSON object as your final response, with no Markdown fences or surrounding prose. "
            "Do not write a report file or change any approval, merge, deployment or control-plane state. "
            "Do not include task, Worker, context, version or method identities in the report; the parent binds them. "
            "An empty findings list is valid; never manufacture a finding. Describe checks and limitations. "
            "Source references must use the logical source names below and exact line excerpts. "
            "A checked claim is still your assessment, not an independently observed test execution.\n"
            + "Sources: " + canonical_json(deepcopy(source_names)) + "\n"
            + "Report shape: " + canonical_json(example) + "\n"
            + "Finding shape (only when a finding exists): " + canonical_json(finding) + "\n")
