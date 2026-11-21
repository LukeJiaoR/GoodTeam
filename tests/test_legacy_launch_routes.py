import threading
from http.client import HTTPConnection

from goodteam_os import web


def test_direct_launch_post_routes_are_not_served(tmp_path, monkeypatch):
    server_type = web.ThreadingHTTPServer

    class TestServer(server_type):
        def serve_forever(self):
            thread = threading.Thread(target=super().serve_forever, kwargs={"poll_interval": 0.01})
            thread.start()
            host, port = self.server_address
            origin = f"http://{host}:{port}"
            try:
                for prefix in ("", "/api"):
                    for route, body in (
                        ("/dispatch-track", "{}"),
                        ("/smoke-run", '{"suite":"not-a-suite"}'),
                    ):
                        connection = HTTPConnection(host, port, timeout=5)
                        try:
                            path = prefix + route
                            connection.request("POST", path, body, {"Origin": origin, "Content-Type": "application/json"})
                            assert connection.getresponse().status == 404, path
                        finally:
                            connection.close()
            finally:
                self.shutdown()
                thread.join()

    monkeypatch.setattr(web, "ThreadingHTTPServer", TestServer)
    web.serve(tmp_path, port=0)
