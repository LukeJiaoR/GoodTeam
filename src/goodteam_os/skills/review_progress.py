"""Non-secret parent observations of an owned CLI; never report authority."""
import json
import math
import re
import time

from .models import SkillError

MAX_REVIEW_SECONDS = 3600
PHASES = ("process_started", "input_delivered", "native_thread_started", "native_turn_started", "native_output", "native_turn_completed")
THREAD_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
CLASSES = {"native_error", "authentication", "rate_limit", "network", "quota", "native_panic"}
USAGE = {"input_tokens", "cached_input_tokens", "output_tokens"}


def validate_progress(value):
    keys = {"phase", "elapsed_seconds", "deadline_seconds", "stdout_bytes", "stderr_bytes", "thread_id",
            "usage", "diagnostic_classes", "returncode", "termination_confirmed", "error_code"}
    if (not isinstance(value, dict) or set(value) != keys or not isinstance(value["phase"], str) or value["phase"] not in PHASES
            or type(value["elapsed_seconds"]) not in {int, float} or not math.isfinite(value["elapsed_seconds"])
            or not 0 <= value["elapsed_seconds"] <= MAX_REVIEW_SECONDS + 10
            or type(value["deadline_seconds"]) not in {int, float} or not math.isfinite(value["deadline_seconds"])
            or not 0 < value["deadline_seconds"] <= MAX_REVIEW_SECONDS
            or any(type(value[k]) is not int or not 0 <= value[k] <= 3 * 1024 * 1024 for k in ("stdout_bytes", "stderr_bytes"))
            or (value["thread_id"] is not None and (not isinstance(value["thread_id"], str)
                or re.fullmatch(THREAD_ID, value["thread_id"]) is None))
            or not isinstance(value["diagnostic_classes"], list) or len(value["diagnostic_classes"]) > len(CLASSES)
            or any(not isinstance(k, str) or k not in CLASSES for k in value["diagnostic_classes"])
            or len(set(value["diagnostic_classes"])) != len(value["diagnostic_classes"])
            or type(value["termination_confirmed"]) is not bool
            or (value["returncode"] is not None and (type(value["returncode"]) is not int or abs(value["returncode"]) > 65535))
            or (value["error_code"] is not None and (not isinstance(value["error_code"], str)
                or re.fullmatch(r"[a-z_]{1,128}", value["error_code"]) is None))):
        raise SkillError("review_progress_invalid", "The owned process observation is invalid.")
    usage = value["usage"]
    if usage is not None and (not isinstance(usage, dict) or not usage or set(usage) - USAGE
            or any(type(v) is not int or not 0 <= v < 10**12 for v in usage.values())):
        raise SkillError("review_progress_invalid", "The native usage observation is invalid.")
    return value


class ProcessProgress:
    def __init__(self, seconds, callback):
        self.callback, self.started, self.last = callback, time.monotonic(), 0.0
        self.pending = bytearray()
        self.value = {"phase": "process_started", "elapsed_seconds": 0.0, "deadline_seconds": seconds,
                      "stdout_bytes": 0, "stderr_bytes": 0, "thread_id": None, "usage": None,
                      "diagnostic_classes": [], "returncode": None, "termination_confirmed": False, "error_code": None}

    def emit(self, phase=None, *, force=False):
        if self.callback is None:
            return
        now = time.monotonic()
        changed = phase is not None and PHASES.index(phase) > PHASES.index(self.value["phase"])
        if changed:
            self.value["phase"] = phase
        if force or changed or now - self.last >= 1:
            self.value["elapsed_seconds"] = round(now - self.started, 3)
            self.callback(validate_progress({**self.value, "diagnostic_classes": list(self.value["diagnostic_classes"])}))
            self.last = now

    def output(self, chunk, *, stderr=False):
        if self.callback is None:
            return
        key = "stderr_bytes" if stderr else "stdout_bytes"
        self.value[key] += len(chunk)
        if stderr:
            text = chunk.decode("utf-8", errors="replace")
            for name, terms in {"authentication": ("401 Unauthorized", "invalid_api_key"),
                                "rate_limit": ("429 Too Many Requests",), "network": ("error sending request", "Connection refused"),
                                "quota": ("insufficient_quota",), "native_panic": ("panicked at",)}.items():
                if any(term in text for term in terms) and name not in self.value["diagnostic_classes"]:
                    self.value["diagnostic_classes"].append(name)
        else:
            self.pending.extend(chunk)
            while b"\n" in self.pending:
                line, _, tail = self.pending.partition(b"\n")
                self.pending = bytearray(tail)
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(event, dict):
                    continue
                kind = event.get("type")
                if kind == "thread.started":
                    identity = event.get("thread_id")
                    if isinstance(identity, str) and re.fullmatch(THREAD_ID, identity):
                        self.value["thread_id"] = identity
                    self.emit("native_thread_started")
                elif kind == "turn.started":
                    self.emit("native_turn_started")
                elif kind == "item.completed":
                    self.emit("native_output")
                elif kind == "turn.completed":
                    usage = event.get("usage")
                    if isinstance(usage, dict):
                        safe = {k: v for k, v in usage.items() if k in USAGE and type(v) is int and 0 <= v < 10**12}
                        self.value["usage"] = safe or None
                    self.emit("native_turn_completed")
                elif kind in {"error", "turn.failed"} and "native_error" not in self.value["diagnostic_classes"]:
                    self.value["diagnostic_classes"].append("native_error")
        self.emit()
