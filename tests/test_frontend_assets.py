from __future__ import annotations

from pathlib import Path

import pytest

from goodteam_os.frontend_assets import FrontendAssetsNotFound, resolve_frontend_dist


def _write_dist(path: Path, marker: str = "react-build") -> Path:
    path.mkdir(parents=True)
    (path / "index.html").write_text(marker, encoding="utf-8")
    return path.resolve()


def test_frontend_dist_explicit_override_is_expanded_and_resolved(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    expected = _write_dist(home / "GoodTeam UI")
    monkeypatch.setenv("HOME", str(home))

    actual = resolve_frontend_dist(
        environ={"GOODTEAM_FRONTEND_DIST": "~/GoodTeam UI"},
        module_path=tmp_path / "elsewhere" / "src" / "goodteam_os" / "frontend_assets.py",
        prefix=tmp_path / "prefix",
    )

    assert actual == expected


@pytest.mark.parametrize("with_directory", [False, True])
def test_frontend_dist_invalid_override_fails_without_falling_back(tmp_path, with_directory) -> None:
    override = tmp_path / "broken-dist"
    if with_directory:
        override.mkdir()
    checkout_dist = _write_dist(tmp_path / "checkout" / "frontend" / "dist")

    with pytest.raises(FrontendAssetsNotFound) as exc_info:
        resolve_frontend_dist(
            environ={"GOODTEAM_FRONTEND_DIST": str(override)},
            module_path=tmp_path / "checkout" / "src" / "goodteam_os" / "frontend_assets.py",
            prefix=tmp_path / "prefix",
        )

    message = str(exc_info.value)
    assert str(override.resolve()) in message
    assert str(checkout_dist) not in exc_info.value.attempted_paths
    assert "index.html" in message
    assert "npm --prefix frontend install" in message
    assert "npm --prefix frontend run build" in message
    assert "GOODTEAM_FRONTEND_DIST" in message


def test_frontend_dist_prefers_package_assets_then_install_prefix(tmp_path) -> None:
    module_path = tmp_path / "site-packages" / "goodteam_os" / "frontend_assets.py"
    package_dist = _write_dist(module_path.parent / "frontend_dist", "package")
    _write_dist(tmp_path / "prefix" / "share" / "goodteam" / "frontend" / "dist", "prefix")

    assert resolve_frontend_dist(environ={}, module_path=module_path, prefix=tmp_path / "prefix") == package_dist


def test_frontend_dist_supports_install_prefix_assets(tmp_path) -> None:
    expected = _write_dist(tmp_path / "prefix" / "share" / "goodteam" / "frontend" / "dist")

    actual = resolve_frontend_dist(
        environ={},
        module_path=tmp_path / "site-packages" / "goodteam_os" / "frontend_assets.py",
        prefix=tmp_path / "prefix",
    )

    assert actual == expected


def test_frontend_dist_supports_source_checkout_without_using_cwd_or_workspace(tmp_path, monkeypatch) -> None:
    checkout = tmp_path / "checkout"
    expected = _write_dist(checkout / "frontend" / "dist")
    unrelated_cwd = tmp_path / "empty-cwd"
    unrelated_cwd.mkdir()
    (unrelated_cwd / "frontend" / "dist").mkdir(parents=True)
    monkeypatch.chdir(unrelated_cwd)

    actual = resolve_frontend_dist(
        environ={},
        module_path=checkout / "src" / "goodteam_os" / "frontend_assets.py",
        prefix=tmp_path / "prefix",
    )

    assert actual == expected


def test_frontend_dist_error_lists_every_attempted_application_path(tmp_path) -> None:
    module_path = tmp_path / "checkout" / "src" / "goodteam_os" / "frontend_assets.py"

    with pytest.raises(FrontendAssetsNotFound) as exc_info:
        resolve_frontend_dist(environ={}, module_path=module_path, prefix=tmp_path / "prefix")

    attempted = exc_info.value.attempted_paths
    assert str((module_path.parent / "frontend_dist").resolve()) in attempted
    assert str((tmp_path / "prefix" / "share" / "goodteam" / "frontend" / "dist").resolve()) in attempted
    assert str((tmp_path / "checkout" / "frontend" / "dist").resolve()) in attempted
