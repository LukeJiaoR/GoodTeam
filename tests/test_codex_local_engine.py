from pathlib import Path
from types import SimpleNamespace

import pytest

from goodteam_os.skills import codex_container
from goodteam_os.skills.models import SkillError


@pytest.mark.parametrize("platform,executable,socket", [
    ("linux", "/usr/bin/docker", "unix:///var/run/docker.sock"),
    ("darwin", "/Applications/Docker.app/Contents/Resources/bin/docker", "unix:///Users/operator/.docker/run/docker.sock"),
])
def test_local_engine_ignores_environment_and_uses_empty_private_config(monkeypatch, tmp_path, platform, executable, socket):
    monkeypatch.setattr(codex_container.sys, "platform", platform)
    monkeypatch.setattr(codex_container.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_dir="/Users/operator"))
    for key, value in {"HOME": "/foreign", "DOCKER_HOST": "tcp://foreign:2375", "DOCKER_CONTEXT": "foreign",
                       "DOCKER_CONFIG": "/foreign", "PATH": "/foreign"}.items():
        monkeypatch.setenv(key, value)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"{}", stderr=b"")

    monkeypatch.setattr(codex_container.subprocess, "run", run)
    engine = codex_container.DockerEngine()
    assert engine.call(tmp_path, "version") == b"{}"
    command, options = calls[0]
    assert command == (executable, "--host", socket, "--config", str(tmp_path), "version")
    assert options["env"] == {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    with pytest.raises(SkillError, match="local Docker"):
        codex_container.DockerEngine(socket="tcp://foreign:2375")
    with pytest.raises(SkillError, match="local Docker"):
        codex_container.DockerEngine(executable=Path("/foreign/docker"))
