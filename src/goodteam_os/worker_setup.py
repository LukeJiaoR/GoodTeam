"""Fail closed until project setup has a verified host-read isolation boundary."""
from pathlib import Path
import sys


_SETUP_BLOCKED = (
    "worker_setup_read_isolation_unavailable: automatic project setup is disabled "
    "because the native workspace sandbox permits host credential reads. "
    "Do not run installation hooks through this route until a read-isolated "
    "setup runtime is implemented and verified."
)


def _network_access(value):
    """Accept only explicit booleans, including this project's YAML scalars."""
    if type(value) is bool:
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise ValueError("worker_setup.network_access must be true or false.")


def load_worker_setup(config_path):
    from goodteam_os.models import _parse_simple_yaml

    value = _parse_simple_yaml(Path(config_path)).get("worker_setup")
    if not value:
        return None
    if not isinstance(value, dict):
        raise ValueError("worker_setup must be a mapping.")
    command = value.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
        raise ValueError("worker_setup.command must be a nonempty argument list.")
    _network_access(value.get("network_access", False))
    raise ValueError(_SETUP_BLOCKED)


def with_setup(command, *, setup, job_root, cwd, binary):
    """Fail through the normal process lifecycle so recovered jobs become terminal."""
    return [sys.executable, str(Path(__file__).resolve())]


def main():
    # Old saved wrapper invocations must not retain the vulnerable execution path.
    # Reject before reading their input, creating directories or spawning commands.
    print(_SETUP_BLOCKED, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
