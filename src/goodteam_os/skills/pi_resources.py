"""Context-bound Pi resource transport; not production Runtime admission.

No production selector registers this channel. An admitted Pi envelope must own
its lifecycle and pass its original content context and current execution check.
The existing broker revalidates package, task and Runtime authority on every read.
"""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading

from .models import SkillError


class PiResourceChannel:
    def __init__(self, provider, context_ref, *, active):
        self.provider, self.context_ref = provider, deepcopy(context_ref)
        self.active = active
        self.closed = threading.Event()
        self.lifecycle = threading.Lock()
        self.server = None
        self.token = secrets.token_urlsafe(32)

    def call(self, payload):
        if (not isinstance(payload, dict)
                or set(payload) != {"skill_id", "relative", "call_id"}
                or any(not isinstance(value, str) for value in payload.values())):
            return {"status": "invalid_request"}
        if self.closed.is_set() or not self.active():
            return {"status": "stopped"}
        try:
            self._check_context()
            if self.closed.is_set() or not self.active():
                return {"status": "stopped"}
            data = self.provider.read_resource(self.context_ref, **payload)
            # Stop during authorization/read must not deliver late private bytes.
            if self.closed.is_set() or not self.active():
                return {"status": "stopped"}
            text = data.decode("utf-8")
        except UnicodeError:
            return {"status": "unsupported_binary_resource"}
        except (SkillError, OSError):
            # Paths, credentials and private package text must not leak in errors.
            return {"status": "resource_unavailable"}
        return {"status": "broker_read_completed", "text": text,
                "runtime_read_observed": False, "method_following_verified": False}

    def _check_context(self):
        private = self.provider.private_input(self.context_ref)
        if private["original_job_input"]["request"]["adapter"] not in {"pi", "pi-rpc"}:
            raise SkillError("resource_runtime_mismatch", "Resource channel requires its original Pi invocation.")

    def start(self):
        if self.server is not None or self.closed.is_set() or not self.active():
            raise SkillError("resource_channel_inactive", "Resource channel is not available.")
        # Full existing authority check, not a metadata flag or new admission seam.
        self._check_context()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if (self.path != "/resource" or not secrets.compare_digest(
                        self.headers.get("Authorization", ""), "Bearer " + owner.token)):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 4096:
                        raise ValueError
                    payload = json.loads(self.rfile.read(length))
                except (ValueError, UnicodeError):
                    self.send_error(400)
                    return
                data = json.dumps(owner.call(payload)).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args):
                pass

        with self.lifecycle:
            if self.server is not None or self.closed.is_set() or not self.active():
                raise SkillError("resource_channel_inactive", "Resource channel is not available.")
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            self.server.daemon_threads = True
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
            return {"GOODTEAM_SKILL_RESOURCE_URL": f"http://127.0.0.1:{self.server.server_port}/resource",
                    "GOODTEAM_SKILL_RESOURCE_TOKEN": self.token}

    def close(self):
        self.closed.set()
        with self.lifecycle:
            if self.server is not None:
                self.server.shutdown()
                self.server.server_close()
