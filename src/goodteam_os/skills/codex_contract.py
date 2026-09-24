"""Exact Codex 0.155.1 command contract for the single Linux ARM64 Reviewer.

Configuration is supplied by server composition, NOT a Skill/job JSON flag.
Building this contract is not conformance or permission to contact a provider.
Source: openai/codex be2951ea34f0d295ed0becf97079f92fa5f6950e.
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
from urllib.parse import urlsplit

from .models import SkillError, canonical_json, content_digest

CODEX_VERSION = "0.155.1"
CODEX_BINARY_DIGEST = "sha256:298d3d73d0bbc1367e58a370df5b6216fe30ce0a92e8b6b0afb0377a958dc335"
ADAPTER_VERSION = "goodteam.codex-review-linux.v1"
PLATFORM = "linux/arm64"
BINARY = "/runtime/bin/codex"
WORKDIR = "/review"

# These are keys in the pinned upstream schema. skip_host_skill_discovery alone
# is NOT sufficient: a registered host provider can require discovery anyway.
# Empty, isolated native roots and a runtime inventory proof are still required.
OPTIONS = {
    "approval_policy": "never",
    "sandbox_mode": "read-only",
    "web_search": "disabled",
    "project_doc_max_bytes": 0,
    "allow_login_shell": False,
    "skills.bundled.enabled": False,
    "skills.include_instructions": False,
    "orchestrator.skills.enabled": False,
    "orchestrator.mcp.enabled": False,
    "features.skip_host_skill_discovery": True,
    "features.plugins": False,
    "features.hooks": False,
    "features.apps": False,
    "features.connectors": False,
    "features.browser_use": False,
    "features.in_app_browser": False,
    "features.computer_use": False,
    "features.image_generation": False,
    "features.multi_agent": False,
    "features.multi_agent_v2": False,
    "features.unified_exec": False,
    "features.shell_snapshot": False,
    "features.shell_snapshot_v2": False,
    "features.code_mode": False,
    "features.code_mode_host": False,
    "features.js_repl": False,
    "features.memories": False,
    "features.memory_tool": False,
    "features.remote_control": False,
    "features.remote_models": False,
    "features.api_key_model_discovery": False,
    "features.responses_websockets": False,
    "features.responses_websockets_v2": False,
    "features.skill_mcp_dependency_install": False,
    "features.skill_env_var_dependency_prompt": False,
    "features.respect_system_proxy": False,
    "features.enable_request_compression": False,
    "shell_environment_policy.inherit": "none",
    "shell_environment_policy.set": {"PATH": "/usr/bin:/bin", "HOME": "/home/worker", "LANG": "C.UTF-8"},
    "model_reasoning_summary": "none",
}


def _toml(value):
    if isinstance(value, dict):
        return "{" + ",".join(canonical_json(k) + "=" + _toml(v) for k, v in sorted(value.items())) + "}"
    return canonical_json(value)


@dataclass(frozen=True)
class CodexContract:
    """No credentials, arbitrary CLI options, provider headers or host paths.

    The provider must be an *existing*, independently admitted route on a Docker
    internal network. Its external authority must enforce model/request/spend
    limits before forwarding. A route name/digest does not prove those limits.
    The adapter never creates a proxy, network, provider account or credential.
    """
    image_id: str
    seccomp_digest: str
    model: str
    network_id: str
    provider_url: str
    provider_container_id: str
    provider_ipv4: str
    provider_route_ref: str
    provider_policy_digest: str

    def __post_init__(self):
        for value in (self.image_id, self.seccomp_digest, self.provider_policy_digest):
            if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
                raise SkillError("codex_contract_invalid", "Runtime images and policies must be content-addressed.")
        if not isinstance(self.network_id, str) or re.fullmatch(r"[0-9a-f]{64}", self.network_id) is None:
            raise SkillError("codex_contract_invalid", "An admitted internal provider network ID is required.")
        if not isinstance(self.provider_container_id, str) or re.fullmatch(r"[0-9a-f]{64}", self.provider_container_id) is None:
            raise SkillError("codex_contract_invalid", "The admitted route must have an exact provider-container identity.")
        try:
            address = ipaddress.IPv4Address(self.provider_ipv4)
            if (not address.is_private or address.is_loopback or address.is_link_local
                    or address.is_multicast or address.is_unspecified or address.is_reserved):
                raise ValueError("invalid provider address")
        except (ValueError, TypeError) as exc:
            raise SkillError("codex_contract_invalid", "An explicit private provider IPv4 address is required.") from exc
        if not isinstance(self.model, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", self.model) is None:
            raise SkillError("codex_contract_invalid", "An exact bounded model identifier is required.")
        if (not isinstance(self.provider_route_ref, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", self.provider_route_ref) is None):
            raise SkillError("codex_contract_invalid", "An opaque server-owned provider route reference is required.")
        try:
            url = urlsplit(self.provider_url)
            port = url.port
        except (ValueError, TypeError) as exc:
            raise SkillError("codex_contract_invalid", "Invalid internal provider endpoint.") from exc
        # A single explicit Docker DNS alias; no public address/URL, credential,
        # query token, arbitrary alternate endpoint, IPv6 literal or URL fragment.
        if (url.scheme not in {"http", "https"} or not url.hostname
                or re.fullmatch(r"[a-z][a-z0-9-]{0,62}", url.hostname) is None
                or url.username is not None or url.password is not None or url.query or url.fragment
                or url.path != "/v1" or (port is not None and not 1 <= port <= 65535)
                or self.provider_url != f"{url.scheme}://{url.hostname}" + (f":{port}" if port is not None else "") + "/v1"):
            raise SkillError("codex_contract_invalid", "Provider endpoints must use the admitted internal alias, without secrets.")

    def command(self) -> tuple[str, ...]:
        # Deliberately NOT `codex exec review`: native review injects the vendor's
        # review methodology instead of the user's replaceable locked method.
        command = [BINARY, "exec", "--strict-config", "--ignore-user-config", "--ignore-rules",
                   "--ephemeral", "--json", "--skip-git-repo-check", "--color", "never", "--model", self.model]
        provider = {"name": "GoodTeam admitted route", "base_url": self.provider_url,
                    "wire_api": "responses", "requires_openai_auth": False, "supports_websockets": False,
                    "request_max_retries": 0, "stream_max_retries": 0, "stream_idle_timeout_ms": 30000}
        options = {**OPTIONS, "model_provider": "goodteam_review", "model_providers.goodteam_review": provider}
        for key, value in sorted(options.items()):
            command.extend(("-c", key + "=" + _toml(value)))
        return (*command, "-")

    def execution_command(self):
        return ("/usr/bin/python3", "-I", "/review/.runtime/guard.py", *self.command())

    @property
    def digest(self):
        from .codex_guard import source_bytes
        return content_digest(canonical_json({"adapter": ADAPTER_VERSION, "platform": PLATFORM,
            "image": self.image_id, "binary": CODEX_BINARY_DIGEST, "seccomp": self.seccomp_digest,
            "guard_digest": content_digest(source_bytes()), "command": self.execution_command(), "network": self.network_id, "route": self.provider_route_ref,
            "provider_container": self.provider_container_id, "provider_ipv4": self.provider_ipv4,
            "provider_policy": self.provider_policy_digest, "container_policy": "goodteam.codex-container.v1"}).encode())
