"""Read a native Codex identity without selecting a release on its behalf."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?(?:\+[0-9A-Za-z][0-9A-Za-z.-]*)?\Z")


def valid_codex_version(value: object) -> bool:
    return isinstance(value, str) and len(value) <= 128 and _VERSION.fullmatch(value) is not None


def parse_codex_version(output: bytes | str) -> str:
    if isinstance(output, bytes):
        output = output.decode("ascii")
    if not isinstance(output, str) or len(output) > 256:
        raise ValueError("Invalid Codex version response")
    prefix = "codex-cli "
    output = output.strip()
    if not output.startswith(prefix) or not valid_codex_version(output[len(prefix):]):
        raise ValueError("Invalid Codex version response")
    return output[len(prefix):]


def probe_codex_version(binary: Path) -> str:
    """Run only the CLI's bounded version query; never start a model session."""
    try:
        result = subprocess.run([str(binary), "--version"], env={"PATH": "/usr/bin:/bin"},
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=False)
        if result.returncode:
            raise ValueError("Codex version query failed")
        return parse_codex_version(result.stdout)
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise ValueError("Codex version query failed") from exc
