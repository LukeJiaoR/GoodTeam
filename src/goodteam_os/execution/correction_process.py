"""Bounded observation for a caller-owned correction process group.

Admission, command/environment ownership and group cleanup stay with the caller.
This transport does not grant isolation or permission to invoke an executor.
"""
from __future__ import annotations

import os
import selectors
import subprocess
import time

from .task_snapshot import TaskInputError

MAX_OUTPUT_BYTES = 1024 * 1024


def communicate_correction(process, prompt, *, seconds, check_current):
    payload = (prompt or "").encode("utf-8")
    position = 0
    output = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + seconds
    with selectors.DefaultSelector() as selector:
        for stream, events, name in ((process.stdin, selectors.EVENT_WRITE, "stdin"),
                (process.stdout, selectors.EVENT_READ, "stdout"), (process.stderr, selectors.EVENT_READ, "stderr")):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, events, name)
        while selector.get_map() or process.poll() is None:
            check_current()
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(process.args, seconds, output=bytes(output["stdout"]), stderr=bytes(output["stderr"]))
            for key, _events in selector.select(timeout=0.05):
                if key.data == "stdin":
                    if position < len(payload):
                        try:
                            position += os.write(key.fd, payload[position:position + 65536])
                        except BrokenPipeError as exc:
                            raise TaskInputError("correction_input_incomplete") from exc
                    if position == len(payload):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                else:
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output[key.data].extend(chunk)
                        if sum(len(value) for value in output.values()) > MAX_OUTPUT_BYTES:
                            raise TaskInputError("correction_output_limit")
        check_current()
    return tuple(output[name].decode("utf-8", errors="replace") for name in ("stdout", "stderr"))
