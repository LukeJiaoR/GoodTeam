"""CLI entry point for GoodTeam v2 (`goodteam serve` and `goodteam doctor`)."""
from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import webbrowser
from typing import Sequence

from goodteam.api.server import create_server
from goodteam.config import doctor


def _cmd_doctor(args: argparse.Namespace) -> int:
    info = doctor(args.state_dir)
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0
    print(f"GoodTeam state_dir: {info['state_dir']}")
    for name in ("codex", "claude", "git", "gh"):
        entry = info[name]
        status = "OK" if entry.get("available") else "MISSING"
        extra = ""
        if name == "gh" and entry.get("available"):
            auth = "authenticated" if entry.get("authenticated") else "not authenticated"
            acct = f" ({entry['account']})" if entry.get("account") else ""
            extra = f" | {auth}{acct}"
        ver = entry.get("version") or entry.get("error") or ""
        print(f"  {name:6s}: {status:7s} {ver}{extra}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    server, app = create_server(args.host, args.port, state_dir=args.state_dir)
    actual_host, actual_port = server.server_address[:2]
    url = f"http://{actual_host}:{actual_port}"
    print(f"GoodTeam v2 running at {url} (state: {app.store.path.parent})", flush=True)

    stop_once = threading.Event()

    def _shutdown(_signum: int, _frame: object) -> None:
        if not stop_once.is_set():
            stop_once.set()
            threading.Thread(target=server.shutdown, daemon=True).start()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

    if args.open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="goodteam", description="GoodTeam v2 local AI team orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Start the local GoodTeam v2 HTTP/SSE server")
    serve.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8788, help="Bind port (default: 8788)")
    serve.add_argument("--state-dir", default=None, help="Override GoodTeam state directory")
    serve.add_argument("--open", action="store_true", help="Open browser after starting server")
    serve.set_defaults(func=_cmd_serve)

    doc = sub.add_parser("doctor", help="Inspect local CLI readiness (codex, claude, git, gh)")
    doc.add_argument("--state-dir", default=None, help="Override GoodTeam state directory")
    doc.add_argument("--json", action="store_true", help="Emit JSON output")
    doc.set_defaults(func=_cmd_doctor)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
