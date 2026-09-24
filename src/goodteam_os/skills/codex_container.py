"""One owned, pinned Docker container; never a daemon/configuration manager.

No pull, build, privileged mode, host network, credential mount, socket mount or
arbitrary command API. Existing server authority supplies the admitted image and
provider route. Transport exit never substitutes for outer-container shutdown.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from .codex_contract import CodexContract, PLATFORM, WORKDIR
from .content import _decode
from .filesystem import directory, regular_file, write_new
from .models import SkillError, canonical_json, content_digest
from .review_process import ReviewProcess

OWNER_LABEL = "goodteam.review.context"
TMPFS = {"/tmp": "rw,nosuid,nodev,noexec,size=128m,mode=1777",
         "/codex-state": "rw,nosuid,nodev,noexec,size=64m,uid=65534,gid=65534,mode=700",
         "/home/worker": "rw,nosuid,nodev,noexec,size=1m,uid=65534,gid=65534,mode=700"}
ENVIRONMENT = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
INNER_ENV = ("-i", "PATH=/runtime/bin:/usr/bin:/bin", "HOME=/home/worker", "CODEX_HOME=/codex-state",
             "TMPDIR=/tmp", "LANG=C.UTF-8")


@dataclass(frozen=True)
class DockerEngine:
    executable: Path = Path("/usr/bin/docker")
    socket: str = "unix:///var/run/docker.sock"

    def __post_init__(self):
        # No environment-derived DOCKER_HOST/context or user-supplied remote daemon.
        if self.executable != Path("/usr/bin/docker") or self.socket != "unix:///var/run/docker.sock":
            raise SkillError("codex_engine_invalid", "Only the explicitly selected local Docker engine is supported.")

    def command(self, config: Path, *args: str):
        return (str(self.executable), "--host", self.socket, "--config", str(config), *args)

    def call(self, config: Path, *args: str) -> bytes:
        try:
            result = subprocess.run(self.command(config, *args), env=ENVIRONMENT, cwd=config,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=15, check=False, close_fds=True)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SkillError("codex_engine_unavailable", "The selected container engine did not complete its operation.") from exc
        if result.returncode != 0 or len(result.stdout) > 128 * 1024 or len(result.stderr) > 128 * 1024:
            raise SkillError("codex_engine_unavailable", "The selected container engine rejected the operation.")
        return result.stdout

    def inspect(self, config: Path, kind: str, identity: str):
        if kind not in {"container", "image", "network"}:
            raise SkillError("codex_engine_invalid", "Unsupported engine inspection.")
        return _decode(self.call(config, kind, "inspect", "--format", "{{json .}}", identity))


def _owned_container(info, *, container_id, name, context_id, contract):
    config = info.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if (not isinstance(labels, dict) or info.get("Id") != container_id or info.get("Name") != "/" + name
            or info.get("Image") != contract.image_id
            or labels.get(OWNER_LABEL) != context_id):
        raise SkillError("review_termination_unknown", "Container identity no longer matches this exact review context.")


def _check_container(info, *, spec, stage, seccomp_bytes):
    host = info.get("HostConfig", {})
    config = info.get("Config", {})
    mounts = info.get("Mounts")
    if not isinstance(host, dict) or not isinstance(config, dict):
        raise SkillError("codex_container_policy", "Container inspection has no complete host/runtime configuration.")
    security = host.get("SecurityOpt") or []
    if not isinstance(security, list) or len(security) != 2 or not all(isinstance(s, str) for s in security):
        raise SkillError("codex_container_policy", "The actual container has unexpected security options.")
    policies = [s[len("seccomp="):] for s in security if s.startswith("seccomp=")]
    try:
        # Docker CLI sends the compacted JSON profile to the daemon, not its
        # client-side path. Compare the complete decoded policy, not a filename
        # or a flag whose presence says nothing about the actual syscall rules.
        policy_matches = len(policies) == 1 and _decode(policies[0].encode()) == _decode(seccomp_bytes)
    except SkillError:
        policy_matches = False
    if (host.get("Privileged") is not False or host.get("ReadonlyRootfs") is not True
            or host.get("NetworkMode") != spec.network_id or host.get("CapAdd")
            or {str(c).upper() for c in host.get("CapDrop") or ()} != {"ALL"}
            or host.get("PidMode", "") or host.get("IpcMode") != "private"
            or host.get("Devices") or host.get("DeviceRequests") or host.get("PortBindings")
            or host.get("PublishAllPorts")
            or host.get("Dns") != ["127.0.0.1"] or host.get("DnsSearch") != ["."]
            or host.get("ExtraHosts") != [urlsplit(spec.provider_url).hostname + ":" + spec.provider_ipv4]
            or host.get("Sysctls") != {"net.ipv6.conf.all.disable_ipv6": "1"}
            or host.get("LogConfig") != {"Type": "none", "Config": {}}
            or host.get("RestartPolicy", {}).get("Name") != "no"
            or host.get("UsernsMode", "") or host.get("VolumesFrom") or host.get("Links")
            or host.get("PidsLimit") != 64 or host.get("Memory") != 768 * 1024 * 1024
            or host.get("NanoCpus") != 1000000000 or host.get("MemorySwap") != 768 * 1024 * 1024
            or host.get("Tmpfs") != TMPFS
            or "no-new-privileges=true" not in security or not policy_matches
            or config.get("User") != "65534:65534" or config.get("WorkingDir") != WORKDIR
            or config.get("Entrypoint") != ["/usr/bin/env"]
            or config.get("Cmd") != [*INNER_ENV, *spec.execution_command()]
            or config.get("Healthcheck", {}).get("Test") != ["NONE"]
            or config.get("OpenStdin") is not True or config.get("Tty") is not False
            or not isinstance(mounts, list)):
        raise SkillError("codex_container_policy", "The actual container differs from the fixed Reviewer policy.")
    # Docker reports tmpfs separately or in Mounts depending on engine version.
    if not all(isinstance(m, dict) for m in mounts):
        raise SkillError("codex_container_policy", "Invalid container mount inspection.")
    binds = [m for m in mounts if m.get("Type") != "tmpfs"]
    if (len(binds) != 1 or binds[0].get("Type") != "bind" or binds[0].get("Source") != str(stage)
            or binds[0].get("Destination") != WORKDIR or binds[0].get("RW") is not False
            or binds[0].get("Propagation") not in {"rprivate", ""}
            or any(m.get("Destination") not in TMPFS for m in mounts if m.get("Type") == "tmpfs")):
        raise SkillError("codex_container_policy", "The Reviewer must see only its immutable input tree, never Operator storage.")


class CodexContainer:
    def __init__(self, contract: CodexContract, *, engine: DockerEngine | None = None):
        self.contract, self.engine = contract, engine or DockerEngine()

    @contextmanager
    def execution(self, stage: Path, *, control: Path, context_id: str, seccomp_bytes: bytes):
        if (not isinstance(context_id, str) or re.fullmatch(r"review-[0-9a-f]{32}", context_id) is None
                or content_digest(seccomp_bytes) != self.contract.seccomp_digest):
            raise SkillError("codex_container_policy", "Container policy and context must match the admitted contract.")
        stage, control = Path(stage), Path(control)
        if stage == control or stage in control.parents or control in stage.parents:
            raise SkillError("codex_container_policy", "Worker input and private container control directories must be separate.")
        # Anchored checks reject symlink ancestors. Neither directory is discovered
        # from a job path; both were freshly created by the parent adapter.
        with directory(stage), directory(control) as fd:
            for name, data in (("seccomp.json", seccomp_bytes), ("config.json", b"{}\n")):
                write_new(fd, name, data)
            record = {"schema_version": "goodteam.codex-envelope.v1", "context_id": context_id,
                      "contract_digest": self.contract.digest, "name": "goodteam-" + context_id}
            write_new(fd, "envelope.json", canonical_json(record).encode())
            os.fsync(fd)  # Prospective owner identity is durable BEFORE create.
        seccomp = control / "seccomp.json"
        spec, engine = self.contract, self.engine
        image = engine.inspect(control, "image", spec.image_id)
        network = engine.inspect(control, "network", spec.network_id)
        if not isinstance(image.get("Config"), dict):
            raise SkillError("codex_image_mismatch", "The pinned image configuration is unavailable.")
        if (image.get("Id") != spec.image_id or image.get("Os") != "linux" or image.get("Architecture") != "arm64"
                or image.get("Config", {}).get("Volumes")
                or any(e.split("=", 1)[0] not in {"PATH", "LANG", "LC_ALL"}
                       for e in image.get("Config", {}).get("Env") or ())):
            raise SkillError("codex_image_mismatch", "The exact image must be Linux ARM64 with no implicit volume mounts.")
        if (network.get("Id") != spec.network_id or network.get("Internal") is not True
                or network.get("Driver") != "bridge" or network.get("EnableIPv6") is not False):
            raise SkillError("codex_network_unverified", "The admitted provider route needs a dedicated internal IPv4 bridge.")
        peers = network.get("Containers", {})
        if (not isinstance(peers, dict) or set(peers) != {spec.provider_container_id}
                or not isinstance(peers.get(spec.provider_container_id), dict)
                or peers[spec.provider_container_id].get("IPv4Address", "").split("/")[0] != spec.provider_ipv4):
            raise SkillError("codex_network_unverified", "The dedicated bridge must contain only the admitted provider endpoint.")
        name = record["name"]
        command = ["create", "--name", name, "--label", OWNER_LABEL + "=" + context_id,
            "--platform", PLATFORM, "--pull", "never", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges=true", "--security-opt", "seccomp=" + str(seccomp),
            "--user", "65534:65534", "--pids-limit", "64", "--memory", "768m", "--memory-swap", "768m", "--cpus", "1",
            "--dns", "127.0.0.1", "--dns-search", ".",
            "--add-host", urlsplit(spec.provider_url).hostname + ":" + spec.provider_ipv4,
            "--sysctl", "net.ipv6.conf.all.disable_ipv6=1", "--no-healthcheck", "--log-driver", "none", "--restart", "no",
            "--network", spec.network_id, "--ipc", "private", "--workdir", WORKDIR,
            "--mount", "type=bind,src=" + str(stage) + ",dst=" + WORKDIR + ",readonly,bind-propagation=rprivate",
            "--interactive", "--entrypoint", "/usr/bin/env"]
        # Docker --mount has comma syntax: do not accidentally grant another mount.
        if any(c in str(stage) for c in (",", "\n", "\r")):
            raise SkillError("codex_container_policy", "The private staging path cannot be encoded as a Docker mount.")
        for path, options in TMPFS.items():
            command += ["--tmpfs", path + ":" + options]
        container_id = None
        creation_attempted = False
        try:
            creation_attempted = True
            try:
                raw_id = engine.call(control, *command, spec.image_id, *INNER_ENV, *spec.execution_command()).decode("ascii").strip()
            except UnicodeError as exc:
                raise SkillError("review_termination_unknown", "Container creation returned invalid identity bytes.") from exc
            if re.fullmatch(r"[0-9a-f]{64}", raw_id) is None:
                raise SkillError("review_termination_unknown", "Container creation returned no exact owned identity.")
            container_id = raw_id
            actual = engine.inspect(control, "container", container_id)
            _owned_container(actual, container_id=container_id, name=name, context_id=context_id, contract=spec)
            _check_container(actual, spec=spec, stage=stage, seccomp_bytes=seccomp_bytes)
            if (actual.get("State", {}).get("Running") is not False
                    or actual.get("State", {}).get("Pid") != 0):
                raise SkillError("review_termination_unknown", "The created Reviewer was already started outside its parent.")
            with directory(control) as fd:
                # Reject policy-file replacement during daemon operations.
                if regular_file(fd, "seccomp.json", maximum=1024 * 1024)[0] != seccomp_bytes:
                    raise SkillError("codex_container_policy", "The admitted policy changed before launch.")
                write_new(fd, "container.json", canonical_json({**record, "container_id": container_id}).encode())
                os.fsync(fd)
            yield ReviewProcess(engine.command(control, "start", "--attach", "--interactive", container_id),
                                control, dict(ENVIRONMENT))
        finally:
            if creation_attempted:
                try:
                    # A create timeout may have created a container. Resolve only
                    # this pre-recorded name and prove its owner, never reuse it.
                    actual = engine.inspect(control, "container", container_id or name)
                    owned_id = actual.get("Id")
                    if not isinstance(owned_id, str) or re.fullmatch(r"[0-9a-f]{64}", owned_id) is None:
                        raise SkillError("review_termination_unknown", "Missing owned container identity during cleanup.")
                    _owned_container(actual, container_id=container_id or owned_id, name=name,
                                     context_id=context_id, contract=spec)
                    if actual.get("State", {}).get("Running") is True:
                        engine.call(control, "stop", "--time", "1", owned_id)
                    stopped = engine.inspect(control, "container", owned_id)
                    _owned_container(stopped, container_id=owned_id, name=name, context_id=context_id, contract=spec)
                    state = stopped.get("State", {})
                    if state.get("Running") is not False or state.get("Pid") != 0 or state.get("Restarting") is not False:
                        raise SkillError("review_termination_unknown", "The outer container has not confirmed termination.")
                    engine.call(control, "rm", owned_id)
                    remaining = engine.call(control, "container", "ls", "--all", "--no-trunc",
                                            "--filter", "id=" + owned_id, "--format", "{{.ID}}")
                    if remaining.strip():
                        raise SkillError("review_termination_unknown", "The owned container still exists after cleanup.")
                    with directory(control) as fd:
                        write_new(fd, "terminated.json", canonical_json({**record, "container_id": owned_id,
                                  "container_absent": True}).encode())
                        os.fsync(fd)
                except (OSError, ValueError, SkillError) as exc:
                    raise SkillError("review_termination_unknown", "The exact Reviewer container cleanup is unconfirmed.") from exc
