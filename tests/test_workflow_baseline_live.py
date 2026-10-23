"""Ordinary graph execution against real local Git and an offline CLI fixture."""
import json
import os
from pathlib import Path
import sys

import pytest

from goodteam_os.graph import run_implementation_workers
from goodteam_os.release_artifacts import ReleaseCandidateError, retain_repository_candidate
from goodteam_os.state import StateStore
from goodteam_os.web import start_workflow
from test_dispatch_isolation import git, repository


@pytest.fixture
def workflow_cli(tmp_path, monkeypatch):
    binary_dir = tmp_path / 'bin'
    binary_dir.mkdir()
    binary = binary_dir / 'codex'
    binary.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
from pathlib import Path
if '--version' in sys.argv:
    print('offline workflow fixture 1')
    raise SystemExit(0)
prompt = sys.stdin.read()
role = next(line.removeprefix('Role: ') for line in prompt.splitlines() if line.startswith('Role: '))
cwd = Path.cwd()
assert Path(sys.argv[sys.argv.index('--cd') + 1]) == cwd
assert (cwd / 'original.txt').read_text() == 'human dirty input\\n'
assert (cwd / 'untracked.bin').read_bytes() == b'\\0human\\xff'
sequence = cwd / 'sequence.txt'
before = sequence.read_text() if sequence.exists() else ''
expected = {'ui': '', 'implementation': 'ui\\n', 'qa': 'ui\\nimplementation\\n'}
assert before == expected[role], (role, before)
sequence.write_text(before + role + '\\n')
with Path(os.environ['WORKFLOW_FIXTURE_CALLS']).open('a') as calls:
    calls.write(json.dumps({'role': role, 'cwd': str(cwd)}) + '\\n')
Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text('# Offline workflow result\\n')
''')
    binary.chmod(0o755)
    empty_home = tmp_path / 'empty-home'
    empty_home.mkdir()
    calls = tmp_path / 'calls.jsonl'
    monkeypatch.setenv('PATH', str(binary_dir) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('CODEX_HOME', str(empty_home))
    monkeypatch.setenv('CODEX_API_KEY', 'offline-workflow-fixture')
    monkeypatch.setenv('WORKFLOW_FIXTURE_CALLS', str(calls))
    return calls


def config_for(tmp_path, source):
    config = tmp_path / 'workspace/projects/demo.yaml'
    config.parent.mkdir(parents=True)
    config.write_text(f'''name: Baseline fixture
key: DEMO
model_profile: balanced
repos:
  root: {source}
worker_backend:
  mode: fake
workers:
  ui: codex
  implementation: codex
  qa: codex
''')
    return config


def start(config, *, run_id='live-baseline'):
    return start_workflow(workspace=config.parent.parent, idea='Keep sequential changes isolated.', project_key='DEMO',
                          model_config=Path(__file__).resolve().parents[1] / 'models.yaml', model_profile='balanced',
                          project_config=config, source_type='new_project', run_id=run_id)


def test_start_freezes_full_input_and_graph_workers_share_retained_cwd(tmp_path, workflow_cli):
    source = repository(tmp_path / 'source')
    (source / 'docs').mkdir()
    (source / 'docs/existing.md').write_text('Human documentation\n')
    git(source, 'add', 'docs/existing.md')
    git(source, 'commit', '-qm', 'Human documentation')
    (source / 'original.txt').write_text('human staged input\n')
    git(source, 'add', 'original.txt')
    (source / 'original.txt').write_text('human dirty input\n')
    (source / 'untracked.bin').write_bytes(b'\0human\xff')
    head = git(source, 'rev-parse', 'HEAD')
    index = (source / '.git/index').read_bytes()
    config = config_for(tmp_path, source)

    payload = start(config)
    assert payload['repository_baseline']
    assert sorted(path.name for path in (source / 'docs').iterdir()) == ['existing.md']
    assert (source / 'docs/existing.md').read_text() == 'Human documentation\n'
    state = StateStore(config.parent.parent).read_project_current('DEMO')
    # Later live source edits must not become this task's new starting input.
    (source / 'original.txt').write_text('later human edit\n')
    results = run_implementation_workers(state, config, workspace=config.parent.parent)

    assert len(results) == 3 and all(result['status'] == 'completed' for result in results), results
    calls = [json.loads(line) for line in workflow_cli.read_text().splitlines()]
    assert [call['role'] for call in calls] == ['ui', 'implementation', 'qa']
    assert len({call['cwd'] for call in calls}) == 1
    retained = Path(calls[0]['cwd'])
    assert retained != source and (retained / 'sequence.txt').read_text() == 'ui\nimplementation\nqa\n'
    assert (source / 'original.txt').read_text() == 'later human edit\n'
    assert not (source / 'sequence.txt').exists()
    assert git(source, 'rev-parse', 'HEAD') == head
    assert (source / '.git/index').read_bytes() == index
    assert all(result['invocation_evidence']['runtime_cwd'] == str(retained) for result in results)
    retain_repository_candidate(state)
    candidate = state.release_candidate['repository_result']
    assert candidate['attribution_scope'] == 'workflow_task'
    assert candidate['integration']['status'] == 'succeeded'
    assert (Path(candidate['integration']['candidate_path']) / 'sequence.txt').read_text() == 'ui\nimplementation\nqa\n'
    # A post-QA edit cannot inherit that completed verification.
    (retained / 'original.txt').write_text('unchecked task edit\n')
    with pytest.raises(ReleaseCandidateError, match='changed after QA'):
        retain_repository_candidate(state)


@pytest.mark.parametrize('kind', ['nongit', 'unborn', 'broken'])
def test_workflow_start_rejects_incomplete_git_before_runtime(tmp_path, workflow_cli, kind):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'original.txt').write_text('human dirty input\n')
    if kind in {'unborn', 'broken'}:
        git(source, 'init', '-q')
    if kind == 'broken':
        (source / '.git/HEAD').write_text('broken HEAD\n')
    config = config_for(tmp_path, source)

    with pytest.raises(ValueError):
        start(config)

    assert not workflow_cli.exists()
    assert not (source / 'sequence.txt').exists()
