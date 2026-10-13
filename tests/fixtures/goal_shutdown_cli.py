"""Owned CLI host with an in-flight synthetic tick; no models or project commands."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from goodteam_os import cli, goal_continuation, web

root, control = (Path(value).resolve() for value in sys.argv[1:])
if not (root / '.recovery-fixture').is_file() or control.parent != root:
    raise ValueError('Owned fixture required')


def blocked_tick(*_args):
    worker = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    (control / 'worker-pid').write_text(str(worker.pid))
    (control / 'entered').touch()
    try:
        deadline = time.monotonic() + 20
        while not (control / 'release').exists():
            if time.monotonic() >= deadline:
                raise TimeoutError('Fixture release missing')
            time.sleep(.02)
        (control / 'finished').write_text(json.dumps({'worker_alive': worker.poll() is None}))
    finally:
        worker.terminate()
        worker.wait(timeout=5)


goal_continuation.tick_goal = blocked_tick
if os.getenv('GOODTEAM_TEST_SHORT_DRAIN') == '1':
    original_join = goal_continuation.GoalServiceStop.join
    goal_continuation.GoalServiceStop.join = lambda self: original_join(self, timeout=.15)
server_class = web.ThreadingHTTPServer


class Server(server_class):
    def server_activate(self):
        super().server_activate()
        (control / 'address.json').write_text(json.dumps({'port': self.server_port}))


web.ThreadingHTTPServer = Server
sys.argv = ['goodteam', 'serve', '--workspace', str(root / 'workspace'), '--host', '127.0.0.1', '--port', '0']
raise SystemExit(cli.main())
