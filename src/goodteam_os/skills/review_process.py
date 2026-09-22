"""Bounded parent-owned process transport for the selected Reviewer adapter.

This does not confer isolation. Its caller must own a verified execution envelope
and an exact environment; no environment, command or credential is read from a
Skill or a public request here. The entire new process group is owned and reaped.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import math
from pathlib import Path
import selectors
import signal
import subprocess
import time
from typing import Callable

from .models import SkillError
from .review_capture import MAX_CAPTURE_BYTES, CapturedReviewTurn, capture_final_turn


@dataclass(frozen=True)
class ReviewProcess:
    command: tuple[str, ...]
    cwd: Path
    environment: dict[str, str]
    deadline_seconds: float = 120
    max_input_bytes: int = 1024 * 1024


def _group_exists(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False


def _terminate(process, *, grace=1.0):
    """Never interpret a kill request or parent exit as descendant termination."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        end = time.monotonic() + grace
        while time.monotonic() < end:
            process.poll()  # Reap our direct child even when another descendant remains.
            if not _group_exists(process.pid):
                return
            time.sleep(0.02)
    process.poll()
    if _group_exists(process.pid):
        raise SkillError("review_termination_unknown", "Reviewer descendants have not been confirmed stopped.")


def run_review_process(spec: ReviewProcess, prompt: bytes, *, check_current: Callable[[], None],
                       on_input_delivered: Callable[[], None] | None = None) -> CapturedReviewTurn:
    """Capture one bounded CLI turn; no output file in Worker/control storage."""
    if (os.name != "posix" or not isinstance(spec, ReviewProcess)
            or not spec.command or not all(isinstance(part, str) and part for part in spec.command)
            or not Path(spec.command[0]).is_absolute()
            or type(spec.max_input_bytes) is not int or not 0 < spec.max_input_bytes <= 8 * 1024 * 1024
            or not isinstance(prompt, bytes) or not prompt or len(prompt) > spec.max_input_bytes
            or type(spec.deadline_seconds) not in {int, float}
            or not math.isfinite(spec.deadline_seconds) or not 0 < spec.deadline_seconds <= 120
            or not isinstance(spec.environment, dict)
            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in spec.environment.items())):
        raise SkillError("review_process_invalid", "Reviewer transport needs a bounded, server-owned process specification.")
    check_current()
    process = subprocess.Popen(spec.command, cwd=spec.cwd, env=dict(spec.environment),
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True, close_fds=True, bufsize=0)
    stdout = bytearray()
    stderr_size = 0
    position = 0
    deadline = time.monotonic() + spec.deadline_seconds
    try:
        with selectors.DefaultSelector() as selector:
            for stream, events in ((process.stdin, selectors.EVENT_WRITE), (process.stdout, selectors.EVENT_READ),
                                   (process.stderr, selectors.EVENT_READ)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, events)
            while selector.get_map():
                check_current()  # Revocation and stop signals are parent decisions, not stdout events.
                if time.monotonic() >= deadline:
                    raise SkillError("review_timeout", "Reviewer exceeded the authorized wall-time boundary.")
                for key, _mask in selector.select(timeout=0.05):
                    stream = key.fileobj
                    if stream is process.stdin:
                        if position < len(prompt):
                            try:
                                position += os.write(stream.fileno(), prompt[position:position + 65536])
                            except BrokenPipeError as exc:
                                raise SkillError("review_input_incomplete", "Reviewer closed its input before complete delivery.") from exc
                        if position == len(prompt):
                            selector.unregister(stream)
                            stream.close()
                            if on_input_delivered is not None:
                                on_input_delivered()
                    else:
                        chunk = os.read(stream.fileno(), 65536)
                        if not chunk:
                            selector.unregister(stream)
                            stream.close()
                        elif stream is process.stdout:
                            stdout.extend(chunk)
                            if len(stdout) > MAX_CAPTURE_BYTES:
                                raise SkillError("review_capture_size", "Reviewer result channel exceeded its limit.")
                        else:
                            # Bound stderr too, but never expose raw diagnostics containing secrets/paths.
                            stderr_size += len(chunk)
                            if stderr_size > MAX_CAPTURE_BYTES:
                                raise SkillError("review_capture_size", "Reviewer diagnostic channel exceeded its limit.")
            # Closed pipes do not mean the process has exited. Keep checking
            # revocation/cancellation during this phase, not only at the end of
            # a potentially long wait (a child can close stdio and keep running).
            while process.poll() is None:
                check_current()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SkillError("review_timeout", "Reviewer did not exit within its wall-time boundary.")
                try:
                    process.wait(timeout=min(0.05, remaining))
                except subprocess.TimeoutExpired:
                    continue
            returncode = process.returncode
        if _group_exists(process.pid):
            # Descendants can close inherited pipes then remain alive. Never accept such a turn.
            raise SkillError("review_descendants_running", "Reviewer descendants remained after its final output.")
        check_current()
        return capture_final_turn(bytes(stdout), returncode=returncode)
    finally:
        _terminate(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
