"""Shared offline native Pi Worker fixtures, independent of retired Manager tests."""
from pathlib import Path

from goodteam_os.project_registry import ProjectRegistry
from goodteam_os.team_control_plane import TeamControlPlaneStore
from goodteam_os.workers import WorkerJobStore


def setup(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    workspace = tmp_path / "workspace"
    config = workspace / "projects/demo.local.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("project_key: DEMO\n")
    ProjectRegistry().register_project(config, root=root, key="DEMO")
    team = TeamControlPlaneStore(workspace, "DEMO")
    team.load_or_migrate(legacy_target={})
    return root, config, team, WorkerJobStore(root)


def fake_pi_with_restore(tmp_path):
    source = (Path(__file__).parent / "fixtures/fake_pi_rpc.py").read_text()
    source = source.replace("session_file.touch()", """if option('--session'):
    session_file = Path(option('--session'))
session_file.touch()""")
    source = source.replace("entries: list[dict[str, object]] = []", """entries: list[dict[str, object]] = []
if option('--session'):
    saved = [json.loads(line) for line in session_file.read_text().splitlines() if line]
    session_id = saved[0]['id']
    entries = saved[1:]""")
    source = source.replace('"".join(json.dumps(entry, ensure_ascii=False)',
        '''json.dumps({'type': 'session', 'id': session_id, 'cwd': str(Path.cwd()), 'version': 3}) + "\\n" + "".join(json.dumps(entry, ensure_ascii=False)''')
    path = tmp_path / "fake-worker-pi"
    path.write_text(source)
    path.chmod(0o755)
    return path
