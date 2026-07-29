from __future__ import annotations

import math
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

from goodteam_os.onboarding_store import (
    OnboardingArtifactStore,
    OnboardingObservationStore,
)
from goodteam_os.project_import import ProjectImportError
from goodteam_os._repository_content_policy import (
    redact_observation_value,
    redacted_path_label,
    reject_sensitive_path,
)
from goodteam_os.repository_access import RepositoryAccessError, canonical_relative_path
from goodteam_os.repository_snapshot import (
    RepositorySnapshot,
    RepositorySnapshotError,
    verify_repository_snapshot,
)


_SERVER_CEILINGS = MappingProxyType({
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
})
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
_ALLOWED_TOOLS = (
    "list_tree",
    "search_text",
    "read_file_excerpt",
    "inspect_manifest",
    "inspect_git_history",
    "inspect_tests",
    "inspect_recent_changes",
)
_LIST_TREE_MAX_ENTRIES = 500


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
        self.operational_signals: tuple[str, ...] = ()
        self._terminal_record_attempted = False

    def add_operational_signal(self, signal: str) -> None:
        if signal not in self.operational_signals:
            self.operational_signals = (*self.operational_signals, signal)


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


@dataclass(frozen=True)
class OnboardingBudgetReservation:
    allowance: int
    _token: str


@dataclass(frozen=True)
class RepositoryContextHandlerCallContext:
    result_char_allowance: int


@dataclass(frozen=True)
class _ObservationStoreBinding:
    workspace: Path
    project_key: str
    root: Path
    job_id: str
    snapshot_id: str


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
        self._peak_single_result_chars = 0
        self._observed_files: set[str] = set()
        self._observed_file_chars: dict[str, int] = {}
        self._usage_by_turn: dict[int, dict[str, float | bool]] = {}
        self._result_reservations: dict[str, int] = {}
        self._reserved_result_chars = 0
        self._limits = self._resolve_limits(requested_limits)
        self.close_requested = False

    @property
    def limits(self) -> OnboardingBudgetLimits:
        return self._limits

    def begin_model_turn(self, turn_number: int) -> None:
        with self._lock:
            turn = self._validate_turn_number(turn_number)
            self._refresh_elapsed()
            self._check_elapsed_limit()
            self._check_consumed_hard_limits()
            self._validate_turn_transition(turn)
            self._commit_model_turn(turn)
            self._update_close_requested()

    def begin_tool_call(self) -> None:
        with self._lock:
            self._refresh_elapsed()
            self._check_elapsed_limit()
            self._check_consumed_hard_limits()
            if self._tool_calls_used >= self.limits.max_tool_calls:
                raise self._budget_exceeded("max_tool_calls")
            self._tool_calls_used += 1
            self._update_close_requested()

    def result_char_allowance(self) -> int:
        with self._lock:
            self._refresh_elapsed()
            self._check_elapsed_limit()
            remaining_total = (
                self.limits.max_total_observation_chars
                - self._total_observation_chars
                - self._reserved_result_chars
            )
            if remaining_total <= 0:
                return 0
            return max(0, min(self.limits.max_single_result_chars, remaining_total))

    def reserve_result(self) -> OnboardingBudgetReservation:
        with self._lock:
            self._refresh_elapsed()
            self._check_elapsed_limit()
            self._check_consumed_hard_limits()
            remaining_total = (
                self.limits.max_total_observation_chars
                - self._total_observation_chars
                - self._reserved_result_chars
            )
            if remaining_total <= 0:
                raise self._budget_exceeded("max_total_observation_chars")
            allowance = min(self.limits.max_single_result_chars, remaining_total)
            token = uuid.uuid4().hex
            self._result_reservations[token] = allowance
            self._reserved_result_chars += allowance
            return OnboardingBudgetReservation(allowance=allowance, _token=token)

    def release_result(self, reservation: OnboardingBudgetReservation) -> None:
        with self._lock:
            if not isinstance(reservation, OnboardingBudgetReservation):
                return
            allowance = self._result_reservations.pop(reservation._token, None)
            if allowance is not None:
                self._reserved_result_chars -= allowance

    def commit_result(
        self,
        reservation: OnboardingBudgetReservation,
        *,
        result_chars: int,
        total_chars: int,
        observed_paths: tuple[str, ...],
        file_chars: dict[str, int],
    ) -> None:
        with self._lock:
            if not isinstance(reservation, OnboardingBudgetReservation):
                raise self._budget_invalid("result_reservation")
            allowance = self._result_reservations.pop(reservation._token, None)
            if allowance is None:
                raise self._budget_invalid("result_reservation")
            self._reserved_result_chars -= allowance
            self._refresh_elapsed()
            self._check_elapsed_limit()
            self._check_consumed_hard_limits()
            validated_result = _validate_nonnegative_integer("result_chars", result_chars)
            validated_total = _validate_nonnegative_integer("total_chars", total_chars)
            if validated_result > self.limits.max_single_result_chars:
                raise self._budget_exceeded("max_single_result_chars")
            if validated_result > allowance:
                raise self._budget_exceeded("max_total_observation_chars")
            if validated_total > allowance:
                raise self._budget_exceeded("max_total_observation_chars")
            if self._total_observation_chars + validated_total > self.limits.max_total_observation_chars:
                raise self._budget_exceeded("max_total_observation_chars")
            next_files, next_file_chars = self._project_observation_state(
                observed_paths=observed_paths,
                file_chars=file_chars,
            )
            self._observed_files = next_files
            self._observed_file_chars = next_file_chars
            self._total_observation_chars += validated_total
            self._peak_single_result_chars = max(
                self._peak_single_result_chars,
                validated_result,
            )
            self._update_close_requested()

    def record_observation(
        self,
        *,
        path: str | None = None,
        chars: int,
        count_toward_file: bool,
    ) -> None:
        with self._lock:
            validated_chars = _validate_nonnegative_integer("chars", chars)
            if path is not None and not isinstance(path, str):
                raise self._budget_invalid("path")
            if not isinstance(count_toward_file, bool):
                raise self._budget_invalid("count_toward_file")
            self._refresh_elapsed()
            self._check_elapsed_limit()
            self._check_consumed_hard_limits()
            if (
                self._total_observation_chars
                + self._reserved_result_chars
                + validated_chars
                > self.limits.max_total_observation_chars
            ):
                raise self._budget_exceeded("max_total_observation_chars")
            observed_paths = () if path is None else (path,)
            file_chars = {path: validated_chars} if path is not None and count_toward_file else {}
            next_files, next_file_chars = self._project_observation_state(
                observed_paths=observed_paths,
                file_chars=file_chars,
            )
            self._observed_files = next_files
            self._observed_file_chars = next_file_chars
            self._total_observation_chars += validated_chars
            self._update_close_requested()

    def record_result_chars(self, chars: int) -> None:
        with self._lock:
            self._refresh_elapsed()
            self._check_elapsed_limit()
            validated_chars = _validate_nonnegative_integer("result_chars", chars)
            if validated_chars > self.limits.max_single_result_chars:
                raise self._budget_exceeded("max_single_result_chars")
            remaining_total = (
                self.limits.max_total_observation_chars
                - self._total_observation_chars
                - self._reserved_result_chars
            )
            if validated_chars > remaining_total:
                raise self._budget_exceeded("max_total_observation_chars")
            self._peak_single_result_chars = max(self._peak_single_result_chars, validated_chars)
            self._update_close_requested()

    def record_model_usage(
        self,
        *,
        turn_number: int,
        estimated_input_tokens: int | None = None,
        estimated_output_tokens: int | None = None,
        actual_input_tokens: int | None = None,
        actual_output_tokens: int | None = None,
        cost_usd: float | None = None,
    ) -> None:
        with self._lock:
            turn = self._validate_turn_number(turn_number)
            estimated_input = _validate_optional_usage_integer(
                "estimated_input_tokens",
                estimated_input_tokens,
            )
            estimated_output = _validate_optional_usage_integer(
                "estimated_output_tokens",
                estimated_output_tokens,
            )
            actual_input = _validate_optional_usage_integer(
                "actual_input_tokens",
                actual_input_tokens,
            )
            actual_output = _validate_optional_usage_integer(
                "actual_output_tokens",
                actual_output_tokens,
            )
            actual_cost = _validate_optional_usage_float("cost_usd", cost_usd)
            self._refresh_elapsed()
            self._check_elapsed_limit()
            self._validate_turn_transition(turn)
            previous = self._usage_by_turn.get(
                turn,
                {
                    "input_tokens": 0.0,
                    "input_authoritative": False,
                    "output_tokens": 0.0,
                    "output_authoritative": False,
                    "cost_usd": 0.0,
                },
            )
            next_usage = dict(previous)
            if actual_input is not None:
                next_usage["input_tokens"] = float(actual_input)
                next_usage["input_authoritative"] = True
            elif estimated_input is not None and not bool(previous["input_authoritative"]):
                next_usage["input_tokens"] = float(estimated_input)
            if actual_output is not None:
                next_usage["output_tokens"] = float(actual_output)
                next_usage["output_authoritative"] = True
            elif estimated_output is not None and not bool(previous["output_authoritative"]):
                next_usage["output_tokens"] = float(estimated_output)
            if actual_cost is not None:
                next_usage["cost_usd"] = float(actual_cost)
            projected_input = self._effective_input_tokens() - float(previous["input_tokens"]) + float(next_usage["input_tokens"])
            projected_output = self._effective_output_tokens() - float(previous["output_tokens"]) + float(next_usage["output_tokens"])
            projected_cost = self._effective_cost_usd() - float(previous["cost_usd"]) + float(next_usage["cost_usd"])
            if projected_input > self.limits.max_estimated_input_tokens:
                raise self._budget_exceeded("max_estimated_input_tokens")
            if projected_output > self.limits.max_estimated_output_tokens:
                raise self._budget_exceeded("max_estimated_output_tokens")
            if projected_cost > self.limits.max_cost_usd:
                raise self._budget_exceeded("max_cost_usd")
            self._commit_model_turn(turn)
            self._usage_by_turn[turn] = next_usage
            self._update_close_requested()

    def as_dict(self) -> dict[str, object]:
        with self._lock:
            self._refresh_elapsed()
            self._update_close_requested()
            return {
                "close_requested": self.close_requested,
                "cost_usd_used": self._effective_cost_usd(),
                "elapsed_seconds": self._elapsed_seconds_cached,
                "estimated_input_tokens_used": int(self._effective_input_tokens()),
                "estimated_output_tokens_used": int(self._effective_output_tokens()),
                "files_observed": len(self._observed_files),
                "model_turns_used": len(self._unique_turns),
                "observed_file_chars": dict(self._observed_file_chars),
                "peak_single_result_chars": self._peak_single_result_chars,
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
        return sum(float(item["input_tokens"]) for item in self._usage_by_turn.values())

    def _effective_output_tokens(self) -> float:
        return sum(float(item["output_tokens"]) for item in self._usage_by_turn.values())

    def _effective_cost_usd(self) -> float:
        return sum(float(item["cost_usd"]) for item in self._usage_by_turn.values())

    def _elapsed_seconds(self) -> float:
        return self._elapsed_seconds_cached

    def _refresh_elapsed(self) -> None:
        self._elapsed_seconds_cached = max(0.0, float(self._clock()) - self._started_at)

    def _check_elapsed_limit(self) -> None:
        if self._elapsed_seconds() > self.limits.max_elapsed_seconds:
            raise self._budget_exceeded("max_elapsed_seconds")

    def _check_consumed_hard_limits(self) -> None:
        dimensions = (
            (self._total_observation_chars, self.limits.max_total_observation_chars, "max_total_observation_chars"),
            (self._effective_input_tokens(), self.limits.max_estimated_input_tokens, "max_estimated_input_tokens"),
            (self._effective_output_tokens(), self.limits.max_estimated_output_tokens, "max_estimated_output_tokens"),
            (self._effective_cost_usd(), self.limits.max_cost_usd, "max_cost_usd"),
        )
        for used, limit, name in dimensions:
            if used >= limit:
                raise self._budget_exceeded(name)

    def _validate_turn_transition(self, turn: int) -> None:
        if self._last_model_turn == 0:
            if turn != 1:
                raise self._budget_invalid("model_turn")
        elif turn != self._last_model_turn and turn != self._last_model_turn + 1:
            raise self._budget_invalid("model_turn")
        if turn not in self._unique_turns and len(self._unique_turns) >= self.limits.max_model_turns:
            raise self._budget_exceeded("max_model_turns")

    def _commit_model_turn(self, turn: int) -> None:
        self._unique_turns.add(turn)
        self._last_model_turn = max(self._last_model_turn, turn)

    def _project_observation_state(
        self,
        *,
        observed_paths: tuple[str, ...],
        file_chars: dict[str, int],
    ) -> tuple[set[str], dict[str, int]]:
        next_files = set(self._observed_files)
        next_file_chars = dict(self._observed_file_chars)
        for path in observed_paths:
            if not isinstance(path, str):
                raise self._budget_invalid("path")
            next_files.add(_safe_path_label(path))
        if len(next_files) > self.limits.max_files_observed:
            raise self._budget_exceeded("max_files_observed")
        if not isinstance(file_chars, dict):
            raise self._budget_invalid("file_chars")
        for path, chars in file_chars.items():
            if not isinstance(path, str):
                raise self._budget_invalid("path")
            validated_chars = _validate_nonnegative_integer("file_chars", chars)
            normalized = _safe_path_label(path)
            next_files.add(normalized)
            next_value = next_file_chars.get(normalized, 0) + validated_chars
            if next_value > self.limits.max_file_cumulative_chars:
                raise self._budget_exceeded("max_file_cumulative_chars")
            next_file_chars[normalized] = next_value
        if len(next_files) > self.limits.max_files_observed:
            raise self._budget_exceeded("max_files_observed")
        return next_files, next_file_chars

    def _update_close_requested(self) -> None:
        dimensions = {
            "model_turns": (len(self._unique_turns), self.limits.max_model_turns),
            "tool_calls": (self._tool_calls_used, self.limits.max_tool_calls),
            "files_observed": (len(self._observed_files), self.limits.max_files_observed),
            "total_observation_chars": (
                self._total_observation_chars,
                self.limits.max_total_observation_chars,
            ),
            "single_result_chars": (
                self._peak_single_result_chars,
                self.limits.max_single_result_chars,
            ),
            "file_cumulative_chars": (
                max(self._observed_file_chars.values(), default=0),
                self.limits.max_file_cumulative_chars,
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
        self.budget = budget
        self._lock = threading.RLock()
        self._handler_context_local = threading.local()
        self.observation_store = observation_store
        constructor_error: ContextProviderError | None = None
        try:
            self._expected_observation_store = observation_store
            self._expected_store_binding = _capture_store_binding(observation_store)
            self._artifact_store = OnboardingArtifactStore(
                self.workspace,
                self.project_key,
                self.root,
            )
            self._validate_constructor_binding()
        except ContextProviderError as exc:
            constructor_error = _detach_context_provider_error(exc)
        except RepositorySnapshotError as exc:
            constructor_error = _detach_context_provider_error(
                ContextProviderError(
                    exc.code,
                    "Repository snapshot verification failed.",
                )
            )
        if constructor_error is not None:
            raise constructor_error

    def tool_names(self) -> tuple[str, ...]:
        return _ALLOWED_TOOLS

    def call(
        self,
        tool: str,
        request: dict[str, object],
        *,
        model_turn: int,
    ) -> dict[str, object]:
        tool_call_id = f"tool_{uuid.uuid4().hex}"
        request_payload = _sanitize_request(request)
        tool_label = _safe_tool_label(tool)
        pending_error: ContextProviderError | None = None
        turn = (
            model_turn
            if isinstance(model_turn, int)
            and not isinstance(model_turn, bool)
            and model_turn > 0
            else None
        )
        try:
            with self._lock:
                validated_request = _validate_request_object(request)
                turn = self._validate_model_turn(model_turn)
                self.budget.begin_model_turn(turn)
                self.budget.begin_tool_call()
                self._verify_bindings()
                if not isinstance(tool, str) or tool not in _ALLOWED_TOOLS:
                    error = ContextProviderError(
                        "context_tool_not_allowed",
                        "Repository context tool is not allowed.",
                    )
                    persistence_error = self._try_persist_denied_envelope(
                        tool_call_id=tool_call_id,
                        tool=tool_label,
                        model_turn=turn,
                        request=request_payload,
                        error=error,
                    )
                    pending_error = (
                        persistence_error
                        if persistence_error is not None
                        else _detach_context_provider_error(error)
                    )
                else:
                    reservation = self.budget.reserve_result()
                    handler_context = RepositoryContextHandlerCallContext(
                        result_char_allowance=reservation.allowance
                    )
                    try:
                        self._set_current_handler_context(handler_context)
                        buffered = self._invoke_handler(tool, validated_request)
                        observations_payload, truncated = _validate_handler_payload(
                            buffered
                        )
                        observations = _normalize_observations(
                            observations_payload,
                            tool_call_id=tool_call_id,
                        )
                        returned_chars, observed_paths, file_chars = (
                            _observation_budget_delta(observations)
                        )
                        self._verify_bindings()
                        self.budget.commit_result(
                            reservation,
                            result_chars=returned_chars,
                            total_chars=returned_chars,
                            observed_paths=observed_paths,
                            file_chars=file_chars,
                        )
                        reservation = None
                    finally:
                        self._clear_current_handler_context()
                        if reservation is not None:
                            self.budget.release_result(reservation)

                    envelope = self._build_envelope(
                        tool_call_id=tool_call_id,
                        tool=tool_label,
                        model_turn=turn,
                        request=request_payload,
                        status="ok",
                        observations=observations,
                        truncated=truncated,
                        response_metadata={
                            "observation_count": len(observations),
                            "returned_chars": returned_chars,
                        },
                    )
                    self._append_envelope(envelope)
                    return envelope
        except ContextProviderError as exc:
            if _is_authority_error_code(exc.code):
                pending_error = _detach_context_provider_error(exc)
            elif not exc._terminal_record_attempted and turn is not None:
                persistence_error = self._try_persist_error_envelope(
                    tool_call_id=tool_call_id,
                    tool=tool_label,
                    model_turn=turn,
                    request=request_payload,
                    error=exc,
                )
                pending_error = (
                    persistence_error
                    if persistence_error is not None
                    else _detach_context_provider_error(exc)
                )
            elif pending_error is None:
                pending_error = _detach_context_provider_error(exc)
        except RepositoryAccessError as exc:
            error = ContextProviderError(
                exc.code,
                "Repository context operation failed.",
                details=_safe_repository_error_details(exc.details),
            )
            if _is_authority_error_code(error.code):
                pending_error = _detach_context_provider_error(error)
            elif turn is not None:
                persistence_error = self._try_persist_error_envelope(
                    tool_call_id=tool_call_id,
                    tool=tool_label,
                    model_turn=turn,
                    request=request_payload,
                    error=error,
                )
                pending_error = (
                    persistence_error
                    if persistence_error is not None
                    else _detach_context_provider_error(error)
                )
            else:
                pending_error = _detach_context_provider_error(error)
        except RepositorySnapshotError as exc:
            error = ContextProviderError(
                exc.code,
                "Repository snapshot verification failed.",
            )
            if _is_authority_error_code(error.code):
                pending_error = _detach_context_provider_error(error)
            elif turn is not None:
                persistence_error = self._try_persist_error_envelope(
                    tool_call_id=tool_call_id,
                    tool=tool_label,
                    model_turn=turn,
                    request=request_payload,
                    error=error,
                )
                pending_error = (
                    persistence_error
                    if persistence_error is not None
                    else _detach_context_provider_error(error)
                )
            else:
                pending_error = _detach_context_provider_error(error)
        except ProjectImportError as exc:
            error = _normalize_artifact_error(exc)
            if _is_authority_error_code(error.code):
                pending_error = _detach_context_provider_error(error)
            elif turn is not None:
                persistence_error = self._try_persist_error_envelope(
                    tool_call_id=tool_call_id,
                    tool=tool_label,
                    model_turn=turn,
                    request=request_payload,
                    error=error,
                )
                pending_error = (
                    persistence_error
                    if persistence_error is not None
                    else _detach_context_provider_error(error)
                )
            else:
                pending_error = _detach_context_provider_error(error)
        except Exception:
            error = ContextProviderError(
                "context_provider_failed",
                "Repository context operation failed.",
            )
            if turn is not None:
                persistence_error = self._try_persist_error_envelope(
                    tool_call_id=tool_call_id,
                    tool=tool_label,
                    model_turn=turn,
                    request=request_payload,
                    error=error,
                )
                pending_error = (
                    persistence_error
                    if persistence_error is not None
                    else _detach_context_provider_error(error)
                )
            else:
                pending_error = _detach_context_provider_error(error)
        if pending_error is not None:
            raise pending_error

    def _validate_constructor_binding(self) -> None:
        if not isinstance(self.snapshot, RepositorySnapshot):
            raise ContextProviderError(
                "onboarding_snapshot_mismatch",
                "Repository snapshot does not match the onboarding binding.",
            )
        if self._expected_store_binding.snapshot_id != self.snapshot.snapshot_id:
            raise ContextProviderError(
                "onboarding_snapshot_mismatch",
                "Repository snapshot does not match the onboarding binding.",
            )
        if self._expected_store_binding.project_key != self.project_key:
            raise ContextProviderError(
                "onboarding_snapshot_mismatch",
                "Repository snapshot does not match the onboarding binding.",
            )
        if self._expected_store_binding.root != self.root:
            raise ContextProviderError(
                "onboarding_snapshot_mismatch",
                "Repository snapshot does not match the onboarding binding.",
            )
        if self._expected_store_binding.workspace != self.workspace:
            raise ContextProviderError(
                "onboarding_snapshot_mismatch",
                "Repository snapshot does not match the onboarding binding.",
            )
        self._assert_store_binding()
        self._verify_bindings()

    def _verify_bindings(self) -> None:
        self._assert_store_binding()
        verify_repository_snapshot(self.root, self.snapshot)
        try:
            published = self._artifact_store.verify_snapshot(
                self.snapshot.snapshot_id
            )
        except ProjectImportError as exc:
            raise _normalize_artifact_error(exc)
        if published.as_dict() != self.snapshot.as_dict():
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

    def _try_persist_error_envelope(
        self,
        *,
        tool_call_id: str,
        tool: str,
        model_turn: int,
        request: dict[str, object],
        error: ContextProviderError,
    ) -> ContextProviderError | None:
        error._terminal_record_attempted = True
        try:
            envelope = self._build_envelope(
                tool_call_id=tool_call_id,
                tool=tool,
                model_turn=model_turn,
                request=request,
                status="error",
                observations=[],
                truncated=False,
                response_metadata={"error": {"code": error.code}},
            )
            self._append_envelope(envelope)
        except ContextProviderError as exc:
            if _is_authority_error_code(exc.code):
                return _detach_context_provider_error(exc)
            return _build_persistence_failure_error()
        except Exception:
            return _build_persistence_failure_error()
        return None

    def _try_persist_denied_envelope(
        self,
        *,
        tool_call_id: str,
        tool: str,
        model_turn: int,
        request: dict[str, object],
        error: ContextProviderError,
    ) -> ContextProviderError | None:
        error._terminal_record_attempted = True
        try:
            envelope = self._build_envelope(
                tool_call_id=tool_call_id,
                tool=tool,
                model_turn=model_turn,
                request=request,
                status="denied",
                observations=[],
                truncated=False,
                response_metadata={"error": {"code": error.code}},
            )
            self._append_envelope(envelope)
        except ContextProviderError as exc:
            if _is_authority_error_code(exc.code):
                return _detach_context_provider_error(exc)
            return _build_persistence_failure_error()
        except Exception:
            return _build_persistence_failure_error()
        return None

    def _set_current_handler_context(
        self,
        context: RepositoryContextHandlerCallContext,
    ) -> None:
        self._handler_context_local.value = context

    def _clear_current_handler_context(self) -> None:
        if hasattr(self._handler_context_local, "value"):
            del self._handler_context_local.value

    def _current_tool_call_context(self) -> RepositoryContextHandlerCallContext:
        try:
            context = self._handler_context_local.value
        except AttributeError:
            context = None
        if not isinstance(context, RepositoryContextHandlerCallContext):
            raise ContextProviderError(
                "context_provider_failed",
                "Repository context operation failed.",
            )
        return context

    def _assert_store_binding(self) -> None:
        if self.observation_store is not self._expected_observation_store:
            raise ContextProviderError(
                "onboarding_snapshot_mismatch",
                "Repository snapshot does not match the onboarding binding.",
            )
        current = _capture_store_binding(self.observation_store)
        if current != self._expected_store_binding:
            raise ContextProviderError(
                "onboarding_snapshot_mismatch",
                "Repository snapshot does not match the onboarding binding.",
            )

    def _recording_authority_error(self) -> ContextProviderError | None:
        try:
            self._verify_bindings()
        except ContextProviderError as exc:
            return _detach_context_provider_error(exc)
        except RepositorySnapshotError as exc:
            return _detach_context_provider_error(
                ContextProviderError(
                    exc.code,
                    "Repository snapshot verification failed.",
                )
            )
        except ProjectImportError as exc:
            return _detach_context_provider_error(_normalize_artifact_error(exc))
        return None

    def _append_envelope(self, envelope: dict[str, object]) -> None:
        authority_error = self._recording_authority_error()
        if authority_error is not None:
            raise authority_error
        self._assert_store_binding()
        try:
            append_store = self._authoritative_append_store()
        except ContextProviderError as exc:
            raise self._prefer_append_window_authority_error(exc) from None
        try:
            append_store.append(envelope)
        except ProjectImportError as exc:
            if exc.code in {
                "onboarding_artifact_conflict",
                "onboarding_snapshot_missing",
                "onboarding_snapshot_mismatch",
                "project_workspace_not_isolated",
            }:
                normalized = _normalize_artifact_error(exc)
                if _is_authority_error_code(normalized.code):
                    raise self._prefer_append_window_authority_error(normalized) from None
            raise _build_persistence_failure_error()
        except ContextProviderError:
            raise
        except Exception:
            raise _build_persistence_failure_error()

    def _authoritative_append_store(self) -> OnboardingObservationStore:
        binding = self._expected_store_binding
        try:
            return OnboardingObservationStore(
                binding.workspace,
                binding.project_key,
                binding.root,
                binding.job_id,
                binding.snapshot_id,
            )
        except ProjectImportError as exc:
            raise _normalize_artifact_error(exc)

    def _prefer_append_window_authority_error(
        self,
        fallback: ContextProviderError,
    ) -> ContextProviderError:
        authority_error = self._recording_authority_error()
        if authority_error is not None:
            return authority_error
        return _detach_context_provider_error(fallback)

    def _invoke_handler(
        self,
        tool: str,
        request: dict[str, object],
    ) -> dict[str, object]:
        try:
            return self._dispatch_tool(tool, request)
        except ContextProviderError as exc:
            if exc.code == "context_request_invalid":
                raise
            if exc.code == "context_tool_limit_exceeded":
                raise
            if exc.code == "context_tool_timeout":
                raise ContextProviderError(
                    "context_tool_timeout",
                    "Repository context tool timed out.",
                    details=_safe_handler_error_details(exc.details),
                ) from exc
            if exc.code == "context_tool_invalid_output":
                raise ContextProviderError(
                    "context_tool_invalid_output",
                    "Repository context tool produced invalid output.",
                    details=_safe_handler_error_details(exc.details),
                ) from exc
            if exc.code == "context_tool_failed":
                raise ContextProviderError(
                    "context_tool_failed",
                    "Repository context tool failed.",
                    details=_safe_handler_error_details(exc.details),
                ) from exc
            if exc.code == "context_tool_not_implemented":
                raise ContextProviderError(
                    "context_tool_not_implemented",
                    "Repository context tool is not implemented.",
                    details=_safe_handler_error_details(exc.details),
                ) from exc
            if exc.code == "context_tool_unsupported":
                raise ContextProviderError(
                    "context_tool_unsupported",
                    "Repository context tool is unsupported.",
                    details=_safe_handler_error_details(exc.details),
                ) from exc
            raise ContextProviderError(
                "context_handler_failed",
                "Repository context handler failed.",
                details=_safe_handler_error_details(exc.details),
            ) from exc
        except (RepositoryAccessError, RepositorySnapshotError):
            raise
        except Exception as exc:
            raise ContextProviderError(
                "context_handler_failed",
                "Repository context handler failed.",
            ) from exc

    def _dispatch_tool(
        self,
        tool: str,
        request: dict[str, object],
    ) -> dict[str, object]:
        if tool == "list_tree":
            return self._handle_list_tree(request)
        if tool == "search_text":
            return self._handle_search_text(request)
        if tool == "read_file_excerpt":
            return self._handle_read_file_excerpt(request)
        if tool == "inspect_manifest":
            return self._handle_inspect_manifest(request)
        if tool == "inspect_git_history":
            return self._handle_inspect_git_history(request)
        if tool == "inspect_tests":
            return self._handle_inspect_tests(request)
        if tool == "inspect_recent_changes":
            return self._handle_inspect_recent_changes(request)
        raise ContextProviderError(
            "context_tool_not_allowed",
            "Repository context tool is not allowed.",
        )

    def _handle_list_tree(self, request: dict[str, object]) -> dict[str, object]:
        from goodteam_os._repository_content_tools import list_tree

        return list_tree(
            root=self.root,
            snapshot=self.snapshot,
            request=request,
            result_char_allowance=self._current_tool_call_context().result_char_allowance,
        )

    def _handle_search_text(self, request: dict[str, object]) -> dict[str, object]:
        from goodteam_os._repository_content_tools import search_text

        return search_text(
            root=self.root,
            snapshot=self.snapshot,
            request=request,
            result_char_allowance=self._current_tool_call_context().result_char_allowance,
        )

    def _handle_read_file_excerpt(self, request: dict[str, object]) -> dict[str, object]:
        from goodteam_os._repository_content_tools import read_file_excerpt

        return read_file_excerpt(
            root=self.root,
            snapshot=self.snapshot,
            request=request,
            result_char_allowance=self._current_tool_call_context().result_char_allowance,
        )

    def _handle_inspect_manifest(self, request: dict[str, object]) -> dict[str, object]:
        return self._not_implemented(request)

    def _handle_inspect_git_history(self, request: dict[str, object]) -> dict[str, object]:
        return self._not_implemented(request)

    def _handle_inspect_tests(self, request: dict[str, object]) -> dict[str, object]:
        return self._not_implemented(request)

    def _handle_inspect_recent_changes(self, request: dict[str, object]) -> dict[str, object]:
        return self._not_implemented(request)

    def _not_implemented(self, request: dict[str, object]) -> dict[str, object]:
        del request
        raise ContextProviderError(
            "context_tool_not_implemented",
            "Repository context tool is not implemented.",
        )

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


def _validate_nonnegative_integer(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContextProviderError(
            "onboarding_budget_invalid",
            "Onboarding budget configuration is invalid.",
            details={"limit": name},
        )
    return value


def _validate_optional_usage_integer(name: str, value: object) -> int | None:
    if value is None:
        return None
    return _validate_nonnegative_integer(name, value)


def _validate_optional_usage_float(name: str, value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContextProviderError(
            "onboarding_budget_invalid",
            "Onboarding budget configuration is invalid.",
            details={"limit": name},
        )
    numeric = float(value)
    if numeric < 0.0 or not math.isfinite(numeric):
        raise ContextProviderError(
            "onboarding_budget_invalid",
            "Onboarding budget configuration is invalid.",
            details={"limit": name},
        )
    return numeric


def _sanitize_request(request: object) -> dict[str, object]:
    if type(request) is not dict:
        return {"redacted": True}
    redacted = _sanitize_request_value(request)
    if type(redacted) is dict:
        return redacted
    return {"redacted": True}


def _sanitize_request_value(value: object, *, key: str | None = None) -> object:
    if isinstance(value, str):
        if key == "path":
            try:
                canonical_relative_path(value, allow_root=True)
                reject_sensitive_path(value)
            except RepositoryAccessError as exc:
                path = exc.details.get("path")
                if exc.details.get("reason") == "sensitive_path" and isinstance(path, str):
                    return path
                return redacted_path_label(value)
        return redact_observation_value(value, key_name=key)
    if isinstance(value, list):
        return [_sanitize_request_value(item, key=key) for item in value]
    if type(value) is dict:
        return {
            str(item_key): _sanitize_request_value(item, key=str(item_key))
            for item_key, item in value.items()
        }
    return value


def _safe_tool_label(tool: object) -> str:
    if (
        isinstance(tool, str)
        and 0 < len(tool) <= 64
        and all(character.isascii() and (character.isalnum() or character == "_") for character in tool)
    ):
        return tool
    return "<invalid>"


def _validate_request_object(request: object) -> dict[str, object]:
    if type(request) is not dict:
        raise ContextProviderError(
            "context_request_invalid",
            "Repository context request must be a JSON object.",
        )
    return dict(request)


def _validate_list_tree_request(
    request: dict[str, object],
) -> tuple[str, int]:
    if set(request) - {"path", "max_entries"}:
        raise ContextProviderError(
            "context_request_invalid",
            "Repository context request must be a JSON object.",
        )
    path = request.get("path", "")
    max_entries = request.get("max_entries", 10)
    if not isinstance(path, str):
        raise ContextProviderError(
            "context_request_invalid",
            "Repository context request must be a JSON object.",
        )
    if (
        isinstance(max_entries, bool)
        or not isinstance(max_entries, int)
        or max_entries <= 0
        or max_entries > _LIST_TREE_MAX_ENTRIES
    ):
        raise ContextProviderError(
            "context_request_invalid",
            "Repository context request must be a JSON object.",
        )
    return path, max_entries


def _validate_handler_payload(
    payload: object,
) -> tuple[list[dict[str, object]], bool]:
    if type(payload) is not dict or not _is_json_safe(payload):
        raise ContextProviderError(
            "context_handler_failed",
            "Repository context handler returned an invalid payload.",
        )
    observations = payload.get("observations", [])
    truncated = payload.get("truncated", False)
    if not isinstance(observations, list) or not isinstance(truncated, bool):
        raise ContextProviderError(
            "context_handler_failed",
            "Repository context handler returned an invalid payload.",
        )
    return observations, truncated


def _is_json_safe(value: object, active_containers: set[int] | None = None) -> bool:
    if value is None or isinstance(value, (bool, str, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list) or type(value) is dict:
        if active_containers is None:
            active_containers = set()
        identity = id(value)
        if identity in active_containers:
            return False
        active_containers.add(identity)
        try:
            if isinstance(value, list):
                return all(
                    _is_json_safe(item, active_containers) for item in value
                )
            return all(
                isinstance(key, str)
                and _is_json_safe(item, active_containers)
                for key, item in value.items()
            )
        finally:
            active_containers.remove(identity)
    return False


def _normalize_observations(
    observations: list[dict[str, object]],
    *,
    tool_call_id: str,
) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for index, item in enumerate(observations):
        if type(item) is not dict:
            raise ContextProviderError(
                "context_handler_failed",
                "Repository context handler returned an invalid payload.",
            )
        normalized_item = {
            key: redact_observation_value(value)
            for key, value in item.items()
            if key != "observation_id"
        }
        normalized_item["observation_id"] = f"{tool_call_id}:{index}"
        normalized.append(normalized_item)
    return normalized


def _count_string_leaves(value: object) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(_count_string_leaves(item) for item in value)
    if isinstance(value, dict):
        return sum(_count_string_leaves(item) for item in value.values())
    return 0


def _observation_budget_delta(
    observations: list[dict[str, object]],
) -> tuple[int, tuple[str, ...], dict[str, int]]:
    total_chars = 0
    observed_paths: list[str] = []
    file_chars: dict[str, int] = {}
    for item in observations:
        total_chars += _count_string_leaves(
            {
                key: value
                for key, value in item.items()
                if key != "observation_id"
            }
        )
        path = item.get("path")
        if isinstance(path, str):
            observed_paths.append(path)
        text = item.get("text")
        if isinstance(text, str) and isinstance(path, str):
            file_chars[path] = file_chars.get(path, 0) + len(text)
        matches = item.get("matches")
        if isinstance(matches, list) and isinstance(path, str):
            for match in matches:
                if isinstance(match, dict) and isinstance(match.get("text"), str):
                    file_chars[path] = file_chars.get(path, 0) + len(match["text"])
    return total_chars, tuple(observed_paths), file_chars


def _safe_handler_error_details(details: dict[str, object]) -> dict[str, object]:
    if not isinstance(details, dict):
        return {}
    limit = details.get("limit")
    if isinstance(limit, str) and limit in {
        *_SERVER_CEILINGS,
        "chars",
        "file_chars",
        "model_turn",
        "result_chars",
        "total_chars",
    }:
        return {"limit": limit}
    return {}


def _safe_repository_error_details(
    details: dict[str, object],
) -> dict[str, object]:
    if not isinstance(details, dict):
        return {}
    safe = _safe_handler_error_details(details)
    if "path" in details:
        path = details.get("path")
        safe["path"] = path if isinstance(path, str) and path.startswith("<redacted>") else "<redacted>"
    reason = details.get("reason")
    if isinstance(reason, str) and reason in {
        "binary",
        "sensitive_path",
        "symlink",
    }:
        safe["reason"] = reason
    return safe


def _normalize_artifact_error(exc: ProjectImportError) -> ContextProviderError:
    if exc.code == "onboarding_artifact_conflict":
        return ContextProviderError(
            "onboarding_artifact_conflict",
            "Published onboarding snapshot is invalid.",
        )
    if exc.code in {"onboarding_snapshot_missing", "onboarding_snapshot_mismatch"}:
        return ContextProviderError(
            "onboarding_snapshot_mismatch",
            "Repository snapshot does not match the onboarding binding.",
        )
    if exc.code == "project_workspace_not_isolated":
        return ContextProviderError(
            "project_workspace_not_isolated",
            "Project workspace binding is invalid.",
        )
    return ContextProviderError(
        "onboarding_artifact_conflict",
        "Published onboarding snapshot is invalid.",
    )


def _capture_store_binding(
    store: OnboardingObservationStore,
) -> _ObservationStoreBinding:
    try:
        workspace = Path(store.workspace).expanduser().resolve(strict=False)
        project_key = str(store.project_key)
        root = Path(store.root).expanduser().resolve()
        job_id = str(store.job_id)
        snapshot_id = str(store.snapshot_id)
    except Exception:
        raise ContextProviderError(
            "onboarding_snapshot_mismatch",
            "Repository snapshot does not match the onboarding binding.",
        )
    return _ObservationStoreBinding(
        workspace=workspace,
        project_key=project_key,
        root=root,
        job_id=job_id,
        snapshot_id=snapshot_id,
    )


def _is_authority_error_code(code: str) -> bool:
    return code in {
        "onboarding_snapshot_mismatch",
        "onboarding_artifact_conflict",
        "project_workspace_not_isolated",
        "repository_root_changed",
        "repository_snapshot_changed",
    }


def _detach_context_provider_error(
    error: ContextProviderError,
) -> ContextProviderError:
    detached = ContextProviderError(
        error.code,
        str(error),
        details=dict(error.details),
    )
    detached.operational_signals = tuple(error.operational_signals)
    detached._terminal_record_attempted = error._terminal_record_attempted
    return detached


def _build_persistence_failure_error() -> ContextProviderError:
    error = ContextProviderError(
        "context_observation_persistence_failed",
        "Repository context observation could not be persisted.",
    )
    error._terminal_record_attempted = True
    error.add_operational_signal("context_observation_persistence_failed")
    return error


def _safe_path_label(value: str) -> str:
    return value.replace("\\", "/")
