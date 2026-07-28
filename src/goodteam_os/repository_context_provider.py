from __future__ import annotations

import json
import math
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from goodteam_os.onboarding_store import OnboardingObservationStore, snapshot_path
from goodteam_os.repository_access import AnchoredRepository, RepositoryAccessError
from goodteam_os.repository_snapshot import (
    RepositorySnapshot,
    RepositorySnapshotError,
    compute_repository_snapshot_id,
    verify_repository_snapshot,
)


_SERVER_CEILINGS = {
    "max_model_turns": 24,
    "max_tool_calls": 80,
    "max_files_observed": 120,
    "max_total_observation_chars": 500000,
    "max_single_result_chars": 16000,
    "max_file_cumulative_chars": 48000,
    "max_elapsed_seconds": 900.0,
    "max_estimated_input_tokens": 180000,
    "max_estimated_output_tokens": 30000,
    "max_cost_usd": 5.0,
}
_INTEGER_LIMITS = frozenset(
    {
        "max_model_turns",
        "max_tool_calls",
        "max_files_observed",
        "max_total_observation_chars",
        "max_single_result_chars",
        "max_file_cumulative_chars",
        "max_estimated_input_tokens",
        "max_estimated_output_tokens",
    }
)
_FLOAT_LIMITS = frozenset({"max_elapsed_seconds", "max_cost_usd"})
_ALLOWED_TOOLS = (
    "list_tree",
    "search_text",
    "read_file_excerpt",
    "inspect_manifest",
    "inspect_git_history",
    "inspect_tests",
    "inspect_recent_changes",
)


class ContextProviderError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class OnboardingBudgetLimits:
    max_model_turns: int = 24
    max_tool_calls: int = 80
    max_files_observed: int = 120
    max_total_observation_chars: int = 500000
    max_single_result_chars: int = 16000
    max_file_cumulative_chars: int = 48000
    max_elapsed_seconds: float = 900.0
    max_estimated_input_tokens: int = 180000
    max_estimated_output_tokens: int = 30000
    max_cost_usd: float = 5.0

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class OnboardingBudget:
    def __init__(
        self,
        requested_limits: OnboardingBudgetLimits | dict[str, object] | None = None,
        *,
        monotonic_clock=time.monotonic,
    ) -> None:
        self._lock = threading.RLock()
        self._clock = monotonic_clock
        self._started_at = float(self._clock())
        self._elapsed_seconds_cached = 0.0
        self._last_model_turn = 0
        self._unique_turns: set[int] = set()
        self._tool_calls_used = 0
        self._total_observation_chars = 0
        self._observed_files: set[str] = set()
        self._observed_file_chars: dict[str, int] = {}
        self._usage_by_turn: dict[int, dict[str, float]] = {}
        self.limits = self._resolve_limits(requested_limits)
        self.close_requested = False

    def begin_model_turn(self, turn_number: int) -> None:
        with self._lock:
            turn = self._validate_turn_number(turn_number)
            self._refresh_elapsed()
            self._check_elapsed_limit()
            self._check_turn_independent_limits()
            if self._last_model_turn == 0:
                if turn != 1:
                    raise self._budget_invalid("model_turn")
            elif turn != self._last_model_turn and turn != self._last_model_turn + 1:
                raise self._budget_invalid("model_turn")
            if turn not in self._unique_turns and len(self._unique_turns) >= self.limits.max_model_turns:
                raise self._budget_exceeded("max_model_turns")
            self._unique_turns.add(turn)
            self._last_model_turn = max(self._last_model_turn, turn)
            self._update_close_requested()

    def begin_tool_call(self) -> None:
        with self._lock:
            self._refresh_elapsed()
            self._check_elapsed_limit()
            self._check_tool_independent_limits()
            if self._tool_calls_used >= self.limits.max_tool_calls:
                raise self._budget_exceeded("max_tool_calls")
            self._tool_calls_used += 1
            self._update_close_requested()

    def result_char_allowance(self) -> int:
        with self._lock:
            self._refresh_elapsed()
            self._check_elapsed_limit()
            remaining_total = self.limits.max_total_observation_chars - self._total_observation_chars
            if remaining_total <= 0:
                return 0
            return max(0, min(self.limits.max_single_result_chars, remaining_total))

    def record_observation(
        self,
        *,
        path: str | None = None,
        chars: int,
        count_toward_file: bool,
    ) -> None:
        with self._lock:
            self._refresh_elapsed()
            self._check_elapsed_limit()
            self._check_observation_independent_limits(path, count_toward_file)
            if isinstance(chars, bool) or not isinstance(chars, int) or chars < 0:
                raise self._budget_invalid("chars")
            if self._total_observation_chars + chars > self.limits.max_total_observation_chars:
                raise self._budget_exceeded("max_total_observation_chars")
            if path is not None:
                normalized = _safe_path_label(path)
                if normalized not in self._observed_files:
                    if len(self._observed_files) >= self.limits.max_files_observed:
                        raise self._budget_exceeded("max_files_observed")
                    self._observed_files.add(normalized)
                if count_toward_file:
                    next_value = self._observed_file_chars.get(normalized, 0) + chars
                    if next_value > self.limits.max_file_cumulative_chars:
                        raise self._budget_exceeded("max_file_cumulative_chars")
                    self._observed_file_chars[normalized] = next_value
            self._total_observation_chars += chars
            self._update_close_requested()

    def record_model_usage(
        self,
        *,
        turn_number: int,
        estimated_input_tokens: int = 0,
        estimated_output_tokens: int = 0,
        actual_input_tokens: int | None = None,
        actual_output_tokens: int | None = None,
        cost_usd: float | None = None,
    ) -> None:
        with self._lock:
            turn = self._validate_turn_number(turn_number)
            self._refresh_elapsed()
            self._check_elapsed_limit()
            self._check_model_usage_independent_limits()
            if turn not in self._unique_turns:
                self.begin_model_turn(turn)
            estimated_input = _validate_limit_value("estimated_input_tokens", estimated_input_tokens, integer=True)
            estimated_output = _validate_limit_value("estimated_output_tokens", estimated_output_tokens, integer=True)
            actual_input = (
                estimated_input
                if actual_input_tokens is None
                else _validate_limit_value("actual_input_tokens", actual_input_tokens, integer=True)
            )
            actual_output = (
                estimated_output
                if actual_output_tokens is None
                else _validate_limit_value("actual_output_tokens", actual_output_tokens, integer=True)
            )
            actual_cost = 0.0 if cost_usd is None else _validate_limit_value("cost_usd", cost_usd, integer=False)
            next_usage = {
                "input_tokens": float(actual_input),
                "output_tokens": float(actual_output),
                "cost_usd": float(actual_cost),
            }
            previous = self._usage_by_turn.get(turn, {"input_tokens": 0.0, "output_tokens": 0.0, "cost_usd": 0.0})
            projected_input = self._effective_input_tokens() - previous["input_tokens"] + next_usage["input_tokens"]
            projected_output = self._effective_output_tokens() - previous["output_tokens"] + next_usage["output_tokens"]
            projected_cost = self._effective_cost_usd() - previous["cost_usd"] + next_usage["cost_usd"]
            if projected_input > self.limits.max_estimated_input_tokens:
                raise self._budget_exceeded("max_estimated_input_tokens")
            if projected_output > self.limits.max_estimated_output_tokens:
                raise self._budget_exceeded("max_estimated_output_tokens")
            if projected_cost > self.limits.max_cost_usd:
                raise self._budget_exceeded("max_cost_usd")
            self._usage_by_turn[turn] = next_usage
            self._update_close_requested()

    def as_dict(self) -> dict[str, object]:
        with self._lock:
            return {
                "close_requested": self.close_requested,
                "cost_usd_used": self._effective_cost_usd(),
                "elapsed_seconds": self._elapsed_seconds_cached,
                "estimated_input_tokens_used": int(self._effective_input_tokens()),
                "estimated_output_tokens_used": int(self._effective_output_tokens()),
                "files_observed": len(self._observed_files),
                "model_turns_used": len(self._unique_turns),
                "observed_file_chars": dict(self._observed_file_chars),
                "tool_calls_used": self._tool_calls_used,
                "total_observation_chars_used": self._total_observation_chars,
            }

    def _resolve_limits(
        self,
        requested_limits: OnboardingBudgetLimits | dict[str, object] | None,
    ) -> OnboardingBudgetLimits:
        if requested_limits is None:
            return OnboardingBudgetLimits()
        if isinstance(requested_limits, OnboardingBudgetLimits):
            candidate = requested_limits.as_dict()
        elif isinstance(requested_limits, dict):
            candidate = dict(requested_limits)
        else:
            raise self._budget_invalid("requested_limits")
        resolved = OnboardingBudgetLimits().as_dict()
        for name, value in candidate.items():
            if name not in resolved:
                raise self._budget_invalid(name)
            resolved[name] = _validate_requested_limit(name, value)
        return OnboardingBudgetLimits(**resolved)

    def _effective_input_tokens(self) -> float:
        return sum(item["input_tokens"] for item in self._usage_by_turn.values())

    def _effective_output_tokens(self) -> float:
        return sum(item["output_tokens"] for item in self._usage_by_turn.values())

    def _effective_cost_usd(self) -> float:
        return sum(item["cost_usd"] for item in self._usage_by_turn.values())

    def _elapsed_seconds(self) -> float:
        return self._elapsed_seconds_cached

    def _refresh_elapsed(self) -> None:
        self._elapsed_seconds_cached = max(0.0, float(self._clock()) - self._started_at)

    def _check_elapsed_limit(self) -> None:
        if self._elapsed_seconds() > self.limits.max_elapsed_seconds:
            raise self._budget_exceeded("max_elapsed_seconds")

    def _check_turn_independent_limits(self) -> None:
        if (
            self._total_observation_chars >= self.limits.max_total_observation_chars
            or self._effective_input_tokens() >= self.limits.max_estimated_input_tokens
            or self._effective_output_tokens() >= self.limits.max_estimated_output_tokens
            or self._effective_cost_usd() >= self.limits.max_cost_usd
        ):
            raise self._budget_exceeded("limit_reached")

    def _check_tool_independent_limits(self) -> None:
        if (
            self._total_observation_chars >= self.limits.max_total_observation_chars
            or self._effective_input_tokens() >= self.limits.max_estimated_input_tokens
            or self._effective_output_tokens() >= self.limits.max_estimated_output_tokens
            or self._effective_cost_usd() >= self.limits.max_cost_usd
        ):
            raise self._budget_exceeded("limit_reached")

    def _check_observation_independent_limits(
        self,
        path: str | None,
        count_toward_file: bool,
    ) -> None:
        if self._total_observation_chars >= self.limits.max_total_observation_chars:
            raise self._budget_exceeded("max_total_observation_chars")
        if path is not None:
            normalized = _safe_path_label(path)
            if normalized not in self._observed_files and len(self._observed_files) >= self.limits.max_files_observed:
                raise self._budget_exceeded("max_files_observed")
            if count_toward_file and self._observed_file_chars.get(normalized, 0) >= self.limits.max_file_cumulative_chars:
                raise self._budget_exceeded("max_file_cumulative_chars")
        if (
            self._effective_input_tokens() >= self.limits.max_estimated_input_tokens
            or self._effective_output_tokens() >= self.limits.max_estimated_output_tokens
            or self._effective_cost_usd() >= self.limits.max_cost_usd
        ):
            raise self._budget_exceeded("limit_reached")

    def _check_model_usage_independent_limits(self) -> None:
        if (
            self._effective_input_tokens() >= self.limits.max_estimated_input_tokens
            or self._effective_output_tokens() >= self.limits.max_estimated_output_tokens
            or self._effective_cost_usd() >= self.limits.max_cost_usd
        ):
            raise self._budget_exceeded("limit_reached")

    def _update_close_requested(self) -> None:
        dimensions = {
            "model_turns": (len(self._unique_turns), self.limits.max_model_turns),
            "tool_calls": (self._tool_calls_used, self.limits.max_tool_calls),
            "files_observed": (len(self._observed_files), self.limits.max_files_observed),
            "total_observation_chars": (
                self._total_observation_chars,
                self.limits.max_total_observation_chars,
            ),
            "elapsed_seconds": (self._elapsed_seconds(), self.limits.max_elapsed_seconds),
            "estimated_input_tokens": (
                self._effective_input_tokens(),
                self.limits.max_estimated_input_tokens,
            ),
            "estimated_output_tokens": (
                self._effective_output_tokens(),
                self.limits.max_estimated_output_tokens,
            ),
            "cost_usd": (self._effective_cost_usd(), self.limits.max_cost_usd),
        }
        self.close_requested = any(limit > 0 and used / limit >= 0.8 for used, limit in dimensions.values())

    def _validate_turn_number(self, turn_number: int) -> int:
        if isinstance(turn_number, bool) or not isinstance(turn_number, int) or turn_number <= 0:
            raise self._budget_invalid("model_turn")
        return turn_number

    def _budget_invalid(self, limit_name: str) -> ContextProviderError:
        return ContextProviderError(
            "onboarding_budget_invalid",
            "Onboarding budget configuration is invalid.",
            details={"limit": limit_name},
        )

    def _budget_exceeded(self, limit_name: str) -> ContextProviderError:
        return ContextProviderError(
            "onboarding_budget_exceeded",
            "Onboarding budget limit reached.",
            details={"limit": limit_name},
        )


class RepositoryContextProvider:
    raw_argv_dispatch = False
    uses_reflection_dispatch = False

    def __init__(
        self,
        *,
        workspace: Path,
        project_key: str,
        root: Path,
        snapshot: RepositorySnapshot,
        observation_store: OnboardingObservationStore,
        budget: OnboardingBudget,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve(strict=False)
        self.project_key = str(project_key)
        self.root = Path(root).expanduser().resolve()
        self.snapshot = snapshot
        self.observation_store = observation_store
        self.budget = budget
        self._lock = threading.RLock()
        self._tool_handlers = {
            "list_tree": self._handle_list_tree,
            "search_text": self._handle_search_text,
            "read_file_excerpt": self._handle_read_file_excerpt,
            "inspect_manifest": self._handle_empty,
            "inspect_git_history": self._handle_empty,
            "inspect_tests": self._handle_empty,
            "inspect_recent_changes": self._handle_empty,
        }
        self._validate_constructor_binding()

    def tool_names(self) -> tuple[str, ...]:
        return tuple(self._tool_handlers)

    def call(self, tool: str, request: dict[str, object], *, model_turn: int) -> dict[str, object]:
        turn = self._validate_model_turn(model_turn)
        request_payload = _sanitize_request(request)
        tool_call_id = f"tool_{uuid.uuid4().hex}"
        self.budget.begin_model_turn(turn)
        if tool not in self._tool_handlers:
            envelope = self._build_envelope(
                tool_call_id=tool_call_id,
                tool=tool,
                model_turn=turn,
                request=request_payload,
                status="denied",
                observations=[],
                truncated=False,
                response_metadata={"error": {"code": "context_tool_not_allowed"}},
            )
            self.observation_store.append(envelope)
            raise ContextProviderError(
                "context_tool_not_allowed",
                "Repository context tool is not allowed.",
            )
        self.budget.begin_tool_call()
        try:
            with self._lock:
                self._verify_bindings()
                buffered = self._tool_handlers[tool](dict(request))
                observations = _normalize_observations(buffered.get("observations", []), tool_call_id=tool_call_id)
                returned_chars = _count_string_leaves(observations)
                if returned_chars > self.budget.result_char_allowance():
                    raise ContextProviderError(
                        "onboarding_budget_exceeded",
                        "Onboarding budget limit reached.",
                        details={"limit": "max_single_result_chars"},
                    )
                for path, chars, count_toward_file in _observation_budget_events(observations):
                    self.budget.record_observation(
                        path=path,
                        chars=chars,
                        count_toward_file=count_toward_file,
                    )
                self._verify_bindings()
                envelope = self._build_envelope(
                    tool_call_id=tool_call_id,
                    tool=tool,
                    model_turn=turn,
                    request=request_payload,
                    status="ok",
                    observations=observations,
                    truncated=bool(buffered.get("truncated", False)),
                    response_metadata={
                        "observation_count": len(observations),
                        "returned_chars": returned_chars,
                    },
                )
                self.observation_store.append(envelope)
                return envelope
        except ContextProviderError as exc:
            self._persist_error_envelope(
                tool_call_id=tool_call_id,
                tool=tool,
                model_turn=turn,
                request=request_payload,
                code=exc.code,
            )
            raise
        except (RepositoryAccessError, RepositorySnapshotError) as exc:
            code = getattr(exc, "code", "repository_snapshot_changed")
            self._persist_error_envelope(
                tool_call_id=tool_call_id,
                tool=tool,
                model_turn=turn,
                request=request_payload,
                code=code,
            )
            raise ContextProviderError(code, "Repository snapshot verification failed.") from exc

    def _validate_constructor_binding(self) -> None:
        if not isinstance(self.snapshot, RepositorySnapshot):
            raise ContextProviderError(
                "onboarding_snapshot_mismatch",
                "Repository snapshot does not match the onboarding binding.",
            )
        if self.observation_store.snapshot_id != self.snapshot.snapshot_id:
            raise ContextProviderError(
                "onboarding_snapshot_mismatch",
                "Repository snapshot does not match the onboarding binding.",
            )
        if self.observation_store.project_key != self.project_key or self.observation_store.root != self.root:
            raise ContextProviderError(
                "onboarding_snapshot_mismatch",
                "Repository snapshot does not match the onboarding binding.",
            )
        self._verify_bindings()

    def _verify_bindings(self) -> None:
        verify_repository_snapshot(self.root, self.snapshot)
        artifact = snapshot_path(self.workspace, self.project_key, self.snapshot.snapshot_id)
        try:
            payload = json.loads(artifact.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ContextProviderError(
                "onboarding_snapshot_mismatch",
                "Repository snapshot does not match the onboarding binding.",
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ContextProviderError(
                "onboarding_artifact_conflict",
                "Published onboarding snapshot is invalid.",
            ) from exc
        if not isinstance(payload, dict):
            raise ContextProviderError(
                "onboarding_artifact_conflict",
                "Published onboarding snapshot is invalid.",
            )
        try:
            published = RepositorySnapshot(**payload)
        except TypeError as exc:
            raise ContextProviderError(
                "onboarding_artifact_conflict",
                "Published onboarding snapshot is invalid.",
            ) from exc
        if (
            published.snapshot_id != self.snapshot.snapshot_id
            or published.project_key != self.project_key
            or published.canonical_root != str(self.root)
            or compute_repository_snapshot_id(published) != self.snapshot.snapshot_id
            or published.root_identity != self.snapshot.root_identity
        ):
            raise ContextProviderError(
                "onboarding_snapshot_mismatch",
                "Repository snapshot does not match the onboarding binding.",
            )

    def _build_envelope(
        self,
        *,
        tool_call_id: str,
        tool: str,
        model_turn: int,
        request: dict[str, object],
        status: str,
        observations: list[dict[str, object]],
        truncated: bool,
        response_metadata: dict[str, object],
    ) -> dict[str, object]:
        return {
            "schema": "repository_observation.v1",
            "tool_call_id": tool_call_id,
            "tool": tool,
            "snapshot_id": self.snapshot.snapshot_id,
            "model_turn": model_turn,
            "request": request,
            "status": status,
            "truncated": truncated,
            "limits": self.budget.limits.as_dict(),
            "observations": observations,
            "response_metadata": response_metadata,
            "budget": self.budget.as_dict(),
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }

    def _persist_error_envelope(
        self,
        *,
        tool_call_id: str,
        tool: str,
        model_turn: int,
        request: dict[str, object],
        code: str,
    ) -> None:
        envelope = self._build_envelope(
            tool_call_id=tool_call_id,
            tool=tool,
            model_turn=model_turn,
            request=request,
            status="error",
            observations=[],
            truncated=False,
            response_metadata={"error": {"code": code}},
        )
        existing = self.observation_store.read_all()
        if existing and existing[-1].get("tool_call_id") == tool_call_id:
            return
        self.observation_store.append(envelope)

    def _handle_list_tree(self, request: dict[str, object]) -> dict[str, object]:
        path = ""
        if "path" in request and isinstance(request["path"], str):
            path = request["path"]
        max_entries = 10
        if "max_entries" in request and isinstance(request["max_entries"], int) and not isinstance(request["max_entries"], bool):
            max_entries = request["max_entries"]
        with AnchoredRepository(self.root, expected_identity=self.snapshot.root_identity) as repository:
            entries = repository.list_directory(path, max_entries=max_entries)
        observations = [
            {
                "kind": "tree_entry",
                "path": entry.path,
                "entry_kind": entry.kind,
            }
            for entry in entries
        ]
        return {"observations": observations, "truncated": False}

    def _handle_search_text(self, request: dict[str, object]) -> dict[str, object]:
        return {"observations": [], "truncated": False}

    def _handle_read_file_excerpt(self, request: dict[str, object]) -> dict[str, object]:
        return {"observations": [], "truncated": False}

    def _handle_empty(self, request: dict[str, object]) -> dict[str, object]:
        del request
        return {"observations": [], "truncated": False}

    def _validate_model_turn(self, model_turn: int) -> int:
        if isinstance(model_turn, bool) or not isinstance(model_turn, int) or model_turn <= 0:
            raise ContextProviderError(
                "onboarding_budget_invalid",
                "Onboarding budget configuration is invalid.",
                details={"limit": "model_turn"},
            )
        return model_turn


def _validate_requested_limit(name: str, value: object) -> object:
    integer = name in _INTEGER_LIMITS
    validated = _validate_limit_value(name, value, integer=integer)
    ceiling = _SERVER_CEILINGS[name]
    if validated > ceiling:
        raise ContextProviderError(
            "onboarding_budget_invalid",
            "Onboarding budget configuration is invalid.",
            details={"limit": name},
        )
    return validated


def _validate_limit_value(name: str, value: object, *, integer: bool) -> int | float:
    if integer:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ContextProviderError(
                "onboarding_budget_invalid",
                "Onboarding budget configuration is invalid.",
                details={"limit": name},
            )
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContextProviderError(
            "onboarding_budget_invalid",
            "Onboarding budget configuration is invalid.",
            details={"limit": name},
        )
    numeric = float(value)
    if numeric <= 0.0 or not math.isfinite(numeric):
        raise ContextProviderError(
            "onboarding_budget_invalid",
            "Onboarding budget configuration is invalid.",
            details={"limit": name},
        )
    return numeric


def _sanitize_request(request: object) -> dict[str, object]:
    del request
    return {"redacted": True}


def _normalize_observations(
    observations: object,
    *,
    tool_call_id: str,
) -> list[dict[str, object]]:
    if not isinstance(observations, list):
        raise ContextProviderError(
            "context_handler_failed",
            "Repository context handler returned an invalid payload.",
        )
    normalized: list[dict[str, object]] = []
    for index, item in enumerate(observations):
        if not isinstance(item, dict):
            raise ContextProviderError(
                "context_handler_failed",
                "Repository context handler returned an invalid payload.",
            )
        normalized.append({"observation_id": f"{tool_call_id}:{index}", **item})
    return normalized


def _count_string_leaves(value: object) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(_count_string_leaves(item) for item in value)
    if isinstance(value, dict):
        return sum(_count_string_leaves(item) for item in value.values())
    return 0


def _observation_budget_events(
    observations: list[dict[str, object]],
) -> list[tuple[str | None, int, bool]]:
    events: list[tuple[str | None, int, bool]] = []
    for item in observations:
        chars = _count_string_leaves(item)
        if chars:
            events.append((str(item.get("path")) if isinstance(item.get("path"), str) else None, chars, False))
        text = item.get("text")
        if isinstance(text, str) and isinstance(item.get("path"), str):
            events.append((str(item["path"]), len(text), True))
        matches = item.get("matches")
        if isinstance(matches, list) and isinstance(item.get("path"), str):
            for match in matches:
                if isinstance(match, dict) and isinstance(match.get("text"), str):
                    events.append((str(item["path"]), len(match["text"]), True))
    return events


def _safe_path_label(value: str) -> str:
    return value.replace("\\", "/")
