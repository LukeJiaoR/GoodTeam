"""Task-owned Responses transport for the pinned Linux Reviewer boundary.

This is an internal forwarding component, not an admission implementation or
HTTP listener. No production host route is composed by default. A trusted host
authority must bind the effective managed route and resolve existing auth in
host memory; neither Worker JSON nor a saved PASS file can supply it. Container
relay/network wiring is separately admitted. Cancelling a socket cannot retire
work already accepted by an external provider.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
import fcntl
import http.client
import os
from pathlib import Path
import re
import socket
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

from .content import _bytes, _decode, _publish_immutable
from .filesystem import directory, open_lock_file, regular_file, subdirectory, write_new
from .models import SkillError, content_digest
from .task_locks import TaskSkillIdentity

BRIDGE_VERSION = "goodteam.codex-task-bridge.v1"
_DIGEST = r"sha256:[0-9a-f]{64}"
_NAME = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"


@dataclass(frozen=True)
class BridgeScope:
    """Parent-owned exact task/input/context and finite exposure authorization."""
    task: TaskSkillIdentity
    worker_root: str
    task_lock_ref: str
    snapshot_digest: str
    context_id: str
    model: str
    authorization_id: str
    expires_at: int
    max_requests: int = 1
    deadline_seconds: int = 20
    max_request_bytes: int = 2 * 1024 * 1024
    max_response_bytes: int = 2 * 1024 * 1024
    max_output_tokens: int = 2048

    def __post_init__(self):
        if (not isinstance(self.task, TaskSkillIdentity)
                or not isinstance(self.worker_root, str) or not Path(self.worker_root).is_absolute()
                or ".." in Path(self.worker_root).parts
                or not isinstance(self.task_lock_ref, str) or not 1 <= len(self.task_lock_ref) <= 2048
                or not isinstance(self.snapshot_digest, str) or re.fullmatch(_DIGEST, self.snapshot_digest) is None
                or not isinstance(self.context_id, str) or re.fullmatch(r"review-[0-9a-f]{32}", self.context_id) is None
                or not isinstance(self.model, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", self.model) is None
                or not isinstance(self.authorization_id, str) or re.fullmatch(_NAME, self.authorization_id) is None
                or type(self.expires_at) is not int or not time.time() < self.expires_at <= time.time() + 86400
                or type(self.max_requests) is not int or not 1 <= self.max_requests <= 4
                or type(self.deadline_seconds) is not int or not 1 <= self.deadline_seconds <= 120
                or any(type(n) is not int or not 1 <= n <= 8 * 1024 * 1024
                       for n in (self.max_request_bytes, self.max_response_bytes))
                or type(self.max_output_tokens) is not int or not 1 <= self.max_output_tokens <= 4096):
            raise SkillError("bridge_scope_invalid", "The bridge needs an exact task, fresh context and finite limits.")


@dataclass(frozen=True)
class HostRouteBinding:
    """Nonsecret witness from the separately implemented host authority.

    A route digest alone is not proof of effective configuration. This object is
    returned/rechecked by typed host composition, never deserialized from a job.
    Its endpoint is never returned to the Worker or written into the ledger.
    """
    route_ref: str
    effective_config_digest: str
    endpoint: str = field(repr=False)

    def __post_init__(self):
        if (not isinstance(self.route_ref, str) or re.fullmatch(_NAME, self.route_ref) is None
                or not isinstance(self.effective_config_digest, str)
                or re.fullmatch(_DIGEST, self.effective_config_digest) is None):
            raise SkillError("bridge_route_invalid", "A host-owned route and effective-config witness are required.")


class HostProviderAuthority(ABC):
    """Host-side dependency; no production implementation is installed here.

    bind/check must resolve the effective managed provider, authenticate its
    account/model authorization, and reject drift/revocation. Authentication is
    resolved only into host memory, never a container header/argument/file.
    A user config digest, features listing, alias or boolean is insufficient.
    This interface does not promise remote retirement or a USD ceiling.
    """
    verification_kind = "unverified"

    @abstractmethod
    def bind(self, scope: BridgeScope) -> HostRouteBinding: ...

    @abstractmethod
    def check(self, binding: HostRouteBinding, scope: BridgeScope) -> None: ...

    @abstractmethod
    def authorization(self, binding: HostRouteBinding, scope: BridgeScope) -> str:
        """Return one existing bearer in host memory; do not create credentials."""


@dataclass(frozen=True)
class BridgeResponse:
    content_type: str
    body: bytes = field(repr=False)


@dataclass(frozen=True)
class BridgeStop:
    context_retired: bool
    local_transport_stopped: bool
    remote_retirement_confirmed: bool = False


@dataclass
class _Operation:
    connection: http.client.HTTPConnection
    deadline: float
    done: threading.Event = field(default_factory=threading.Event)
    sock: socket.socket | None = None
    reason: str | None = None


class TaskProviderBridge:
    """One fresh context, one owned upstream transport, durable request budget.

    Requests are buffered/validated and responses are bounded/buffered before
    return. This component never opens a listener, changes networking, retries,
    follows redirects or launches a model process. Only an admitted relay can
    expose it to the Linux envelope. A relay must map client disconnect to
    cancel(). Contexts cannot be resumed/reused after restart; consumed slots
    survive crashes, failed calls and cancellation.
    """
    def __init__(self, scope: BridgeScope, *, state_root: Path,
                 authority: HostProviderAuthority | None = None):
        if not isinstance(authority, HostProviderAuthority):
            raise SkillError("bridge_authority_unavailable", "No effective host provider authority is composed.")
        if not isinstance(scope, BridgeScope):
            raise SkillError("bridge_scope_invalid", "A parent-owned typed bridge scope is required.")
        self.scope, self.authority = scope, authority
        self._mutex = threading.RLock()
        self._active: _Operation | None = None
        self._retired = False
        self._owner = uuid4().hex
        try:
            self.binding = authority.bind(scope)
            if not isinstance(self.binding, HostRouteBinding):
                raise ValueError("No typed binding")
            url = urlsplit(self.binding.endpoint)
            port = url.port
            fixture = authority.verification_kind == "contract_fixture"
            fixture_http = fixture and url.scheme == "http" and url.hostname == "127.0.0.1"
            if (authority.verification_kind not in {"verified_runtime", "contract_fixture"}
                    or (fixture and not fixture_http)
                    or not isinstance(self.binding.endpoint, str) or (url.scheme != "https" and not fixture_http)
                    or not url.hostname or url.username is not None or url.password is not None
                    or url.query or url.fragment or not url.path.endswith("/responses")
                    or "//" in url.path or "/../" in url.path or not url.path.startswith("/")
                    or (port is not None and not 1 <= port <= 65535)):
                raise ValueError("Unsafe endpoint")
            self._url = url
            self._check_authority()
        except Exception:
            raise SkillError("bridge_route_unverified", "The effective host provider route is unavailable or unsafe.") from None
        root = Path(state_root).absolute()
        if root.resolve().is_relative_to(Path(scope.worker_root).resolve()):
            raise SkillError("bridge_ledger_unsafe", "The provider ledger must be outside Worker storage.")
        self.root = root
        manifest = {"bridge": BRIDGE_VERSION, **asdict(scope)}
        manifest.pop("context_id")
        manifest.update({"route_ref": self.binding.route_ref,
                         "effective_config_digest": self.binding.effective_config_digest,
                         "verification_kind": authority.verification_kind})
        self._manifest = _bytes(manifest)
        self._context = scope.context_id + ".json"
        with self._ledger() as fd:
            _publish_immutable(fd, "authorization.json", self._manifest, code="bridge_authorization_conflict")
            if self._context in os.listdir(fd):
                raise SkillError("bridge_context_used", "A provider context cannot be restarted or borrowed.")
            write_new(fd, self._context, _bytes({"owner": self._owner, "snapshot_digest": scope.snapshot_digest}))
            os.fsync(fd)

    def _check_authority(self):
        if time.time() >= self.scope.expires_at:
            raise SkillError("bridge_authorization_revoked", "The task provider authorization expired.")
        try:
            self.authority.check(self.binding, self.scope)
        except Exception:
            raise SkillError("bridge_authorization_revoked", "The effective provider authorization changed or was revoked.") from None

    def _ledger(self):
        from contextlib import contextmanager

        @contextmanager
        def locked():
            with directory(self.root, create=True) as root:
                info = os.fstat(root)
                if info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise SkillError("bridge_ledger_unsafe", "The host ledger must be private and owned by this user.")
                with subdirectory(root, self.scope.authorization_id, create=True) as fd:
                    lock = open_lock_file(fd, "budget.lock")
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX)
                        yield fd
                    finally:
                        fcntl.flock(lock, fcntl.LOCK_UN)
                        os.close(lock)
        return locked()

    def _current(self, fd):
        if regular_file(fd, "authorization.json", maximum=32 * 1024)[0] != self._manifest:
            raise SkillError("bridge_authorization_conflict", "The durable provider authorization changed.")
        owner = _decode(regular_file(fd, self._context, maximum=4096)[0])
        if owner != {"owner": self._owner, "snapshot_digest": self.scope.snapshot_digest}:
            raise SkillError("bridge_context_used", "The provider context belongs to another controller.")
        if self._retired or self.scope.context_id + ".retired" in os.listdir(fd):
            raise SkillError("bridge_context_retired", "The task provider context is retired.")

    def _reserve(self, body):
        with self._ledger() as fd:
            self._current(fd)
            names = [n for n in os.listdir(fd) if re.fullmatch(r"request-[0-9]{4}\.json", n)]
            if len(names) >= self.scope.max_requests:
                raise SkillError("bridge_budget_exhausted", "The authorized upstream request budget is exhausted.")
            number = len(names) + 1
            write_new(fd, f"request-{number:04}.json", _bytes({"context_id": self.scope.context_id,
                      "snapshot_digest": self.scope.snapshot_digest, "request_digest": content_digest(body)}))
            os.fsync(fd)  # Consume before connect/write; failures never refund.

    def _request(self, method, path, body):
        if method != "POST" or path != "/v1/responses":
            raise SkillError("bridge_request_invalid", "Only the task Responses endpoint is exposed.")
        if not isinstance(body, bytes) or not 0 < len(body) <= self.scope.max_request_bytes:
            raise SkillError("bridge_request_invalid", "The model request exceeds its byte limit.")
        try:
            request = _decode(body)
            allowed = {"model", "input", "instructions", "tools", "tool_choice", "parallel_tool_calls",
                       "reasoning", "stream", "store", "background", "previous_response_id", "include",
                       "text", "prompt_cache_key", "metadata", "service_tier", "max_output_tokens"}
            if (set(request) - allowed or request.get("model") != self.scope.model
                    or request.get("tools", []) != [] or request.get("tool_choice", "none") not in {"none", "auto"}
                    or request.get("previous_response_id") is not None or request.get("background", False) is not False
                    or request.get("service_tier", "default") != "default"
                    or type(request.get("stream", False)) is not bool):
                raise ValueError("Model or fresh tool-free request mismatch")
            items = request["input"]
            if isinstance(items, str):
                if not items:
                    raise ValueError("Empty input")
            elif isinstance(items, list) and items:
                for item in items:
                    if (not isinstance(item, dict) or set(item) - {"type", "role", "content"}
                            or item.get("type", "message") != "message"
                            or item.get("role") not in {"system", "developer", "user", "assistant"}):
                        raise ValueError("Nonmessage input")
                    parts = item["content"]
                    if isinstance(parts, str):
                        continue
                    if not isinstance(parts, list) or any(not isinstance(p, dict) or set(p) != {"type", "text"}
                            or p["type"] not in {"input_text", "output_text"} or not isinstance(p["text"], str)
                            for p in parts):
                        raise ValueError("Nontext input")
            else:
                raise ValueError("Invalid input")
            # These are request restrictions, not assertions of upstream tariffs
            # or enforcement of an account dollar budget.
            request.update({"tools": [], "tool_choice": "none", "parallel_tool_calls": False,
                            "store": False, "background": False,
                            "reasoning": {"effort": "low"}, "service_tier": "default",
                            "max_output_tokens": self.scope.max_output_tokens})
            result = _bytes(request)
            if len(result) > self.scope.max_request_bytes:
                raise ValueError("Normalized request too large")
            return result
        except Exception:
            raise SkillError("bridge_request_invalid", "The bridge requires its exact model and a fresh tool-free text request.") from None

    def _check_operation(self, operation):
        if operation.reason is not None or self._retired:
            raise SkillError(self._stop_code(operation), "The task provider transport stopped.")
        self._check_authority()

    @staticmethod
    def _stop_code(operation):
        return {"deadline": "bridge_deadline", "revoked": "bridge_authorization_revoked"}.get(
            operation.reason, "bridge_cancelled")

    def _watch_transport(self, operation):
        while not operation.done.wait(0.05):
            reason = "deadline" if time.monotonic() >= operation.deadline else None
            if reason is None:
                try:
                    self._check_authority()
                except SkillError:
                    reason = "revoked"
            if reason is not None:
                # An old watcher cannot stop a later operation that finished
                # between the route check and this lock acquisition.
                with self._mutex:
                    if self._active is operation and not operation.done.is_set():
                        self.cancel(reason=reason, wait_seconds=0)
                return

    def forward(self, method: str, path: str, body: bytes) -> BridgeResponse:
        body = self._request(method, path, body)
        with self._mutex:
            if self._active is not None:
                raise SkillError("bridge_busy", "A task context has one owned provider transport.")
            self._check_authority()
            try:
                bearer = self.authority.authorization(self.binding, self.scope)
                if (not isinstance(bearer, str) or not 1 <= len(bearer) <= 4096
                        or any(ord(c) < 33 or ord(c) > 126 for c in bearer)):
                    raise ValueError("Invalid authorization")
            except Exception:
                raise SkillError("bridge_auth_unavailable", "Existing host authentication is unavailable.") from None
            self._reserve(body)
            connection_type = http.client.HTTPSConnection if self._url.scheme == "https" else http.client.HTTPConnection
            connection = connection_type(self._url.hostname, self._url.port, timeout=self.scope.deadline_seconds)
            operation = _Operation(connection, time.monotonic() + self.scope.deadline_seconds)
            self._active = operation
        watcher = threading.Thread(target=self._watch_transport, args=(operation,), daemon=True)
        watcher.start()
        try:
            self._check_operation(operation)
            connection.connect()
            with self._mutex:
                operation.sock = connection.sock
                self._check_operation(operation)
            # cancel() shuts down the registered socket without clearing
            # connection.sock. HTTPConnection cannot reconnect after a cancel.
            connection.request("POST", self._url.path, body, headers={"Authorization": "Bearer " + bearer,
                               "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
            response = connection.getresponse()
            if response.status != 200:
                raise SkillError("bridge_upstream_failed", "The provider rejected the request; it was not retried.")
            content_type = response.getheader("Content-Type", "").split(";", 1)[0].lower()
            if content_type not in {"application/json", "text/event-stream"}:
                raise SkillError("bridge_response_invalid", "The provider returned an unsupported response type.")
            chunks, total = [], 0
            while True:
                self._check_operation(operation)
                chunk = response.read1(min(65536, self.scope.max_response_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > self.scope.max_response_bytes:
                    raise SkillError("bridge_response_limit", "The provider response exceeded its byte limit.")
            result = b"".join(chunks)
            # Do not return an upstream header or direct bearer reflection. A
            # provider is still trusted with the host credential it receives.
            if bearer.encode("ascii") in result:
                raise SkillError("bridge_response_invalid", "The provider response cannot be delivered safely.")
            self._check_operation(operation)
            return BridgeResponse(content_type, result)
        except SkillError:
            raise
        except Exception:
            # A socket timeout can beat the watcher by milliseconds. It is
            # still deadline consumption, and must durably retire this context.
            if operation.reason is None and time.monotonic() >= operation.deadline:
                self.cancel(reason="deadline", wait_seconds=0)
            code = self._stop_code(operation) if operation.reason else "bridge_upstream_failed"
            raise SkillError(code, "The task provider transport failed; the consumed request was not retried.") from None
        finally:
            connection.close()
            bearer = ""
            with self._mutex:
                self._active = None
                operation.done.set()
            watcher.join(0.2)

    def cancel(self, *, reason="cancelled", wait_seconds=1) -> BridgeStop:
        if reason not in {"cancelled", "deadline", "revoked"} or not isinstance(wait_seconds, (int, float)) or not 0 <= wait_seconds <= 2:
            raise ValueError("A bounded local stop is required")
        with self._mutex:
            self._retired = True
            with self._ledger() as fd:
                _publish_immutable(fd, self.scope.context_id + ".retired", b"retired\n", code="bridge_context_retired")
                os.fsync(fd)
            operation = self._active
            if operation is not None:
                operation.reason = operation.reason or reason
                if operation.sock is not None:
                    try:
                        operation.sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
        stopped = operation is None or operation.done.wait(wait_seconds)
        return BridgeStop(True, stopped)

    def boundaries(self):
        with self._ledger() as fd:
            allocated = len([n for n in os.listdir(fd) if re.fullmatch(r"request-[0-9]{4}\.json", n)])
        return {"adapter": BRIDGE_VERSION, "verification_kind": self.authority.verification_kind,
                "upstream_request_limit": self.scope.max_requests, "upstream_requests_allocated": allocated,
                "deadline_seconds": self.scope.deadline_seconds, "max_output_tokens_requested": self.scope.max_output_tokens,
                "usd_limit": None, "provider_price_verified": False, "remote_retirement_supported": False,
                "production_admission_composed": False}


class BridgeRelayPump:
    """Host half of an owned relay pipe; never a public socket listener.

    The caller owns/inspects the process/container and its dedicated descriptors.
    A pipe disconnect retires the bridge. Remote cancellation remains unknown.
    The only relayed data is an untrusted Responses body or sanitized response;
    the effective endpoint and bearer never enter either pipe.
    """
    def __init__(self, bridge: TaskProviderBridge, *, input_fd: int, output_fd: int):
        from .codex_relay import FrameReader
        if not isinstance(bridge, TaskProviderBridge):
            raise ValueError("An owned task bridge is required")
        self.bridge, self.reader, self.output_fd = bridge, FrameReader(input_fd), output_fd
        self._writer = threading.Lock()
        self._active = None
        self._active_id = None
        self._response = None
        self._closed = threading.Event()

    def _send(self, frame):
        from .codex_relay import write_frame
        with self._writer:
            write_frame(self.output_fd, frame)

    def _forward(self, frame):
        from .codex_relay import decode_body, encode_body
        try:
            result = self.bridge.forward(frame["method"], frame["path"], decode_body(frame["body"]))
            status, content_type, body = 200, result.content_type, result.body
        except SkillError as error:
            status, content_type, body = 403, "application/json", _bytes({"error": error.code})
        except Exception:
            status, content_type, body = 400, "application/json", b'{"error":"bridge_request_invalid"}'
        try:
            # Publish only from run(), after this forwarding thread exits.
            # Otherwise a client can read the response and submit again while
            # is_alive() still reports this completed operation as overlapping.
            self._response = {"type": "response", "request_id": frame["request_id"], "status": status,
                              "content_type": content_type, "body": encode_body(body)}
        except Exception:
            self._closed.set()
            self.bridge.cancel(wait_seconds=0)

    def run(self):
        try:
            while not self._closed.is_set():
                if self._active is not None and not self._active.is_alive():
                    self._active.join()
                    response = self._response
                    self._active, self._response = None, None
                    if response is None:
                        raise ValueError("Missing completed relay response")
                    self._send(response)
                frame = self.reader.read()
                if frame is None:
                    continue
                request_id = frame.get("request_id")
                if not isinstance(request_id, str) or re.fullmatch(r"[0-9a-f]{32}", request_id) is None:
                    raise ValueError("Invalid relay request identity")
                if frame.get("type") == "cancel" and set(frame) == {"type", "request_id"}:
                    if self._active_id != request_id:
                        raise ValueError("Foreign relay cancellation")
                    self.bridge.cancel(wait_seconds=0)
                elif frame.get("type") == "request" and set(frame) == {"type", "request_id", "method", "path", "body"}:
                    if self._active is not None:
                        raise ValueError("Overlapping relay requests")
                    self._active_id = request_id
                    self._active = threading.Thread(target=self._forward, args=(frame,), daemon=True)
                    self._active.start()
                else:
                    raise ValueError("Invalid relay frame")
        except EOFError:
            pass
        except Exception:
            raise SkillError("bridge_relay_invalid", "The owned provider relay failed or sent an invalid frame.") from None
        finally:
            self._closed.set()
            stopped = self.bridge.cancel()
            if self._active is not None:
                self._active.join(2)
            if not stopped.local_transport_stopped or (self._active is not None and self._active.is_alive()):
                raise SkillError("bridge_termination_unknown", "The owned local provider transport has not confirmed stopping.")
