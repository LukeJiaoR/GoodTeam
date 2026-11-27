"""[INPUT]: 接收一次 Manager 请求的工具定义和回调。
[OUTPUT]: 通过本地 MCP 返回工具定义与结果。
[POS]: CodeBuddy 的 stdio 进程通过私有 socket 调用 GoodTeam。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""

import json
import socket
from socketserver import StreamRequestHandler, ThreadingUnixStreamServer
import sys
from threading import Thread


_MAX_FRAME = 8 * 1024 * 1024


def _write(stream, value):
    stream.write(json.dumps(value, ensure_ascii=False).encode() + b"\n")
    stream.flush()


class _Handler(StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(10)
        line = self.rfile.readline(_MAX_FRAME + 1)
        if line.endswith(b"\n") and len(line) <= _MAX_FRAME:
            try:
                request = json.loads(line)
            except ValueError:
                return
            if isinstance(request, dict):
                reply = self.server.handle_request_frame(request)
                try:
                    _write(self.wfile, reply)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # Stop 可以先关闭 CLI，再结束已经进入回调的工具调用。


class ManagerMcp(ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, path, specs, call):
        super().__init__(str(path), _Handler)
        self.specs = [{key: spec[key] for key in ("name", "description", "inputSchema")} for spec in specs]
        self.call = call
        self.thread = Thread(target=self.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def handle_request_frame(self, request):
        if "id" not in request:
            return None
        response = {"jsonrpc": "2.0", "id": request["id"]}
        method = request.get("method")
        params = request.get("params") or {}
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "goodteam", "version": "1"},
                      "capabilities": {"tools": {}}}
        elif method == "tools/list":
            result = {"tools": self.specs}
        elif method == "tools/call" and isinstance(params, dict):
            try:
                reply = self.call(params.get("name"), params.get("arguments", {}), str(request["id"]))
                content = [{"type": "text", "text": item["text"]} for item in reply["contentItems"]]
                result = {"content": content, "isError": not reply["success"]}
            except Exception:
                result = {"content": [{"type": "text", "text": "This Manager tool call is no longer available."}],
                          "isError": True}
        elif method == "ping":
            result = {}
        else:
            return {**response, "error": {"code": -32601, "message": "Unsupported MCP method"}}
        return {**response, "result": result}

    def close(self):
        self.shutdown()
        self.server_close()
        self.thread.join()


def _relay(path):
    # 一条连接只转发一条消息。回调和授权留在 GoodTeam 服务进程。
    source = sys.stdin.buffer
    output = sys.stdout.buffer
    while line := source.readline(_MAX_FRAME + 1):
        if len(line) > _MAX_FRAME or not line.endswith(b"\n"):
            break
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(path)
            client.sendall(line)
            with client.makefile("rb") as stream:
                reply = stream.readline(_MAX_FRAME + 1)
        if not reply or len(reply) > _MAX_FRAME:
            break
        if reply != b"null\n":
            output.write(reply)
            output.flush()


if __name__ == "__main__":
    _relay(sys.argv[1])
