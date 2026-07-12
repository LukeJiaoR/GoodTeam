from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from goodteam_os.native_picker import APPLESCRIPT, choose_native_path


def _completed(*, stdout: str = "", stderr: str = "", returncode: int = 0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


@pytest.mark.parametrize(
    ("mode", "selected_name"),
    [("directory", "目录 with spaces"), ("file", "说明 with spaces.md")],
)
def test_native_picker_returns_normalized_selected_path(tmp_path, mode, selected_name) -> None:
    selected = tmp_path / selected_name
    selected.mkdir() if mode == "directory" else selected.write_text("context", encoding="utf-8")
    seen = {}

    def runner(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return _completed(stdout=f"{selected}\n")

    result = choose_native_path(
        mode,
        str(tmp_path),
        host="127.0.0.1",
        platform="darwin",
        environ={},
        runner=runner,
    )

    assert result == {"status": "selected", "path": str(selected.resolve())}
    assert seen["command"][:3] == ["/usr/bin/osascript", "-e", APPLESCRIPT]
    assert seen["command"][-2:] == [mode, str(tmp_path.resolve())]
    assert seen["kwargs"] == {
        "capture_output": True,
        "text": True,
        "check": False,
        "timeout": 60.0,
    }


def test_native_picker_cancellation_is_not_an_error(tmp_path) -> None:
    result = choose_native_path(
        "directory",
        str(tmp_path),
        host="localhost",
        platform="darwin",
        environ={},
        runner=lambda *args, **kwargs: _completed(stdout="__GOODTEAM_CANCELLED__\n"),
    )

    assert result == {"status": "cancelled", "path": ""}


@pytest.mark.parametrize(
    ("platform", "host", "environ", "reason"),
    [
        ("linux", "127.0.0.1", {}, "native_picker_unavailable"),
        ("darwin", "0.0.0.0", {}, "non_loopback_host"),
        ("darwin", "192.168.1.20", {}, "non_loopback_host"),
        ("darwin", "127.0.0.1", {"CI": "true"}, "headless_environment"),
        ("darwin", "127.0.0.1", {"GOODTEAM_HEADLESS": "1"}, "headless_environment"),
    ],
)
def test_native_picker_is_safely_unsupported(platform, host, environ, reason) -> None:
    def forbidden_runner(*args, **kwargs):
        raise AssertionError("unsupported picker must not spawn a process")

    assert choose_native_path(
        "directory",
        "",
        host=host,
        platform=platform,
        environ=environ,
        runner=forbidden_runner,
    ) == {"status": "unsupported", "reason": reason}


def test_native_picker_rejects_unknown_mode_before_spawning() -> None:
    with pytest.raises(ValueError, match="file or directory"):
        choose_native_path(
            "script",
            "",
            host="127.0.0.1",
            platform="darwin",
            environ={},
            runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
        )


def test_native_picker_normalizes_existing_file_initial_path_to_parent(tmp_path) -> None:
    initial_file = tmp_path / "context.md"
    initial_file.write_text("context", encoding="utf-8")
    selected = tmp_path / "selected.md"
    selected.write_text("selected", encoding="utf-8")
    seen = {}

    def runner(command, **kwargs):
        seen["command"] = command
        return _completed(stdout=str(selected))

    choose_native_path(
        "file",
        str(initial_file),
        host="::1",
        platform="darwin",
        environ={},
        runner=runner,
    )

    assert seen["command"][-1] == str(tmp_path.resolve())


def test_native_picker_uses_fixed_script_and_argv_for_untrusted_initial_path(tmp_path) -> None:
    hostile = tmp_path / 'quote" & do shell script "touch-pwned"'
    hostile.mkdir()
    selected = hostile / "picked"
    selected.mkdir()
    seen = {}

    def runner(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return _completed(stdout=str(selected))

    choose_native_path(
        "directory",
        str(hostile),
        host="127.0.0.1",
        platform="darwin",
        environ={},
        runner=runner,
    )

    assert seen["command"][2] == APPLESCRIPT
    assert str(hostile.resolve()) not in APPLESCRIPT
    assert seen["command"][-1] == str(hostile.resolve())
    assert "shell" not in seen["kwargs"]


def test_native_picker_timeout_and_process_failure_have_stable_errors(tmp_path) -> None:
    timeout = choose_native_path(
        "directory",
        str(tmp_path),
        host="127.0.0.1",
        platform="darwin",
        environ={},
        runner=lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("osascript", 60)),
    )
    failed = choose_native_path(
        "directory",
        str(tmp_path),
        host="127.0.0.1",
        platform="darwin",
        environ={},
        runner=lambda *args, **kwargs: _completed(returncode=1, stderr="Finder failed"),
    )

    assert timeout == {"status": "error", "error": "native_picker_timeout"}
    assert failed == {"status": "error", "error": "Finder failed"}


def test_native_picker_rejects_selected_path_of_wrong_type(tmp_path) -> None:
    selected_directory = tmp_path / "directory"
    selected_directory.mkdir()

    result = choose_native_path(
        "file",
        str(tmp_path),
        host="127.0.0.1",
        platform="darwin",
        environ={},
        runner=lambda *args, **kwargs: _completed(stdout=str(selected_directory)),
    )

    assert result == {"status": "error", "error": "selected_path_is_not_a_file"}
