"""Opt-in hosted search for an existing Pi job, never a new worker runtime.

The host owns credentials and a bounded, durable request allowance. Pi receives
only a short-lived loopback capability. Unknown outcomes consume allowance and
are not retried, including after process restart.
"""
from __future__ import annotations

import asyncio
import fcntl
import os
import tempfile
import hashlib
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from openai import AsyncOpenAI, omit

from goodteam_os.providers import ProviderStore


def search_policy(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"provider", "model", "max_requests", "timeout_seconds"}:
        raise ValueError("web_search requires an explicit provider, model, request limit and timeout")
    ref = value["provider"]
    if (not isinstance(ref, dict) or set(ref) != {"id", "revision"}
            or not isinstance(ref["id"], str) or type(ref["revision"]) is not int or ref["revision"] < 1):
        raise ValueError("web_search provider reference is invalid")
    if (not isinstance(value["model"], str) or not value["model"].strip() or len(value["model"]) > 200
            or type(value["max_requests"]) is not int or not 1 <= value["max_requests"] <= 20
            or type(value["timeout_seconds"]) is not int or not 1 <= value["timeout_seconds"] <= 120):
        raise ValueError("web_search requires a bounded model/request/time authorization")
    return json.loads(json.dumps(value))


def _http_client(timeout):
    return httpx.AsyncClient(follow_redirects=False, timeout=timeout)


async def request_search(connection, policy, query):
    timeout = policy["timeout_seconds"]
    # End-to-end deadline, no redirects, no SDK retries, no private task context.
    async with asyncio.timeout(timeout):
        async with AsyncOpenAI(base_url=connection["base_url"], api_key=connection["api_key"] or "unused",
                max_retries=0, timeout=timeout,
                http_client=_http_client(timeout)) as client:
            async with client.responses.with_streaming_response.create(
                model=policy["model"], input=query, tools=[{"type": "web_search"}],
                include=["web_search_call.action.sources"], max_output_tokens=2048, store=False,
                extra_headers={} if connection["api_key"] else {"Authorization": omit},
            ) as response:
                raw = bytearray()
                async for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 2 * 1024 * 1024:
                        raise ValueError("search response too large")
                return json.loads(raw)


def _source(source):
    url = source.get("url", "")
    if not isinstance(url, str) or len(url) > 4096:
        return None
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    return {"url": url, "title": str(source.get("title") or url)[:500]}


def search_result(payload):
    calls, sources, citations, parts = [], [], [], []
    for item in payload.get("output", []):
        if item.get("type") == "web_search_call":
            calls.append({"id": item.get("id"), "status": item.get("status"), "action": item.get("action", {}).get("type")})
            for source in item.get("action", {}).get("sources", []):
                clean = _source(source)
                if clean and clean not in sources:
                    sources.append(clean)
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") != "output_text":
                    continue
                text = content.get("text", "")
                if not isinstance(text, str):
                    continue
                part = len(parts)
                parts.append(text)
                for annotation in content.get("annotations", []):
                    if annotation.get("type") != "url_citation":
                        continue
                    clean = _source(annotation)
                    if clean:
                        if clean not in sources:
                            sources.append(clean)
                        citations.append({**clean, "part": part, "start_index": annotation.get("start_index"),
                                          "end_index": annotation.get("end_index")})
    searched = any(c["status"] == "completed" for c in calls)
    status = "searched" if searched and sources else "searched_no_sources" if searched else "not_searched"
    return {"status": status, "response_id": payload.get("id"), "response_status": payload.get("status"),
            "answer": "\n".join(parts), "sources": sources, "citations": citations, "search_calls": calls,
            "usage": payload.get("usage"), "untrusted_external_content": True}


class JobWebSearch:
    def __init__(self, job_root, job, *, active, provider_store=None, requester=request_search):
        self.job_root, self.job = Path(job_root), job
        self.policy = search_policy(job.get("web_search"))
        self.active, self.providers, self.requester = active, provider_store or ProviderStore(), requester
        self.path = self.job_root / "web_search.json"
        self.lock = threading.Lock()
        self.closed = False
        self.server = None
        self.token = secrets.token_urlsafe(32)

    def call(self, query):
        if not isinstance(query, str) or not query.strip() or len(query) > 4000:
            return {"status": "invalid_query"}
        query = query.strip()
        digest = hashlib.sha256(query.encode()).hexdigest()
        with self.lock, (self.job_root / "web_search.lock").open("a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                saved = json.loads(self.path.read_text())
            except FileNotFoundError:
                saved = {"policy": self.policy, "job_id": self.job["id"], "requests": {}}
            if saved.get("policy") != self.policy or saved.get("job_id") != self.job["id"]:
                return {"status": "authorization_changed"}
            # Reading a previous result is safe even after Stop. Never resubmit.
            if digest in saved["requests"]:
                return saved["requests"][digest]
            if self.policy is None or self.closed or not self.active():
                return {"status": "not_authorized_or_stopped"}
            if len(saved["requests"]) >= self.policy["max_requests"]:
                return {"status": "request_budget_exhausted"}
            try:
                connection = self.providers.resolve(self.policy["provider"])
                if not connection or connection.get("protocol") != "responses":
                    return {"status": "provider_unavailable"}
            except ValueError:
                return {"status": "provider_configuration_changed"}
            identity = {key: self.job.get(key, "") for key in ("id", "project_key", "run_id", "project_task_id", "track_id")}
            record = {"status": "outcome_unknown", "query": query, "job": identity,
                      "provider": self.policy["provider"], "model": self.policy["model"]}
            saved["requests"][digest] = record
            self._save(saved)
            if self.closed or not self.active():
                record["status"] = "stopped_before_request"
            else:
                try:
                    record.update(search_result(asyncio.run(self.requester(connection, self.policy, query))))
                except Exception:
                    # No provider exception text: may contain prompts or credentials.
                    # Even a timeout may have billed upstream. Do not refund/retry.
                    record["status"] = "failed_or_unknown"
            self._save(saved)
            return record

    def _save(self, payload):
        fd, temporary = tempfile.mkstemp(dir=self.job_root, prefix=".web-search-")
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(payload, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def start(self):
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path != "/search" or not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + owner.token):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 24000:
                        raise ValueError
                    payload = json.loads(self.rfile.read(length))
                    result = owner.call(payload.get("query"))
                    data = json.dumps(result).encode()
                except (ValueError, TypeError, AttributeError):
                    self.send_error(400)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *_args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return {"GOODTEAM_SEARCH_URL": f"http://127.0.0.1:{self.server.server_port}/search", "GOODTEAM_SEARCH_TOKEN": self.token}

    def stop(self):
        # Do not wait for an in-flight upstream call to stop accepting new calls.
        self.closed = True

    def close(self):
        self.stop()
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    def evidence_text(self):
        try:
            saved = json.loads(self.path.read_text())
        except FileNotFoundError:
            return ""
        records = list(saved.get("requests", {}).values())
        if not records:
            return ""
        # Raw URLs remain clickable and cannot inject a Markdown link label.
        lines = ["\n\n## Web Search evidence (external, untrusted)"]
        for record in records:
            lines.append(f"Search status: {record['status']}; response: {record.get('response_id') or 'none'}")
            lines.extend(source["url"] for source in record.get("sources", []))
        return "\n".join(lines)
