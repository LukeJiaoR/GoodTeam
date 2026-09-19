"""One-use, pinned #426 reconciliation in the isolated Actions checkout.

This helper never merges a PR, deploys, calls a model, or changes the CI gate.
It removes itself and its workflow from the resulting tree. The ordinary CI
must run on a subsequent connector-authored commit before merge readiness.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

BASE = 'fd5cc337e681a8e7e9f63094ee8e22dd766a47c8'
SOURCE = '7f25bdc00f8377549724312dcebe86b106f41cee'
SOURCE_BASE = '3a20a7b003abe8971d0a5f267bf7a0a7ed60d516'
BRANCH = 'refs/heads/codex/delivery-outcomes'
REPO = 'LukeJiaoR/GoodTeam'
ADDED = [
    'docs/plans/2026-09-10-delivery-outcomes.md',
    'frontend/src/features/runs/workspace/DeliverySummary.jsx',
    'frontend/src/features/runs/workspace/DeliverySummary.test.jsx',
    'frontend/src/features/runs/workspace/PreparationFacts.jsx',
    'frontend/src/features/runs/workspace/PreparationFacts.test.jsx',
    'frontend/src/features/runs/workspace/deliverySummary.css',
    'src/goodteam_os/delivery_outcomes.py',
    'tests/test_delivery_api.py',
    'tests/test_delivery_outcomes.py',
    'tests/test_delivery_review_truth.py',
    'tests/test_intake_team_dispatch.py',
]
PATCHED = [
    'frontend/src/features/runs/workspace/TeamWorkspace.jsx',
    'frontend/src/features/runs/workspace/assistantControls.css',
    'frontend/src/i18n/locales/en.js',
    'frontend/src/i18n/locales/zh-CN.js',
    'src/goodteam_os/graph.py',
    'src/goodteam_os/web.py',
]


def git(*args, data=None, env=None):
    result = subprocess.run(['git', *args], input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, check=False)
    if result.returncode:
        raise RuntimeError(f'git {args[0]} failed: {result.stderr.decode(errors="replace")}')
    return result.stdout


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError(f'Expected exactly one reconciliation anchor: {old[:100]!r}')
    return text.replace(old, new, 1)


def patch_function(text, name, old, new):
    start = text.index(f'def {name}(')
    next_function = re.search(r'^def |^class ', text[start + 1:], re.MULTILINE)
    end = start + 1 + next_function.start() if next_function else len(text)
    return text[:start] + replace_once(text[start:end], old, new) + text[end:]


def save(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding='utf-8')


def main():
    expected = os.environ['EXPECTED_HEAD']
    if os.environ.get('GITHUB_REPOSITORY') != REPO or os.environ.get('GITHUB_REF') != BRANCH:
        raise RuntimeError('This one-use helper is restricted to the authorized #426 branch.')
    if git('rev-parse', 'HEAD').decode().strip() != expected:
        raise RuntimeError('Checkout no longer matches the triggering SHA.')
    if git('rev-parse', 'origin/main').decode().strip() != BASE:
        raise RuntimeError('Main changed; reconcile against the new main explicitly.')
    git('merge-base', '--is-ancestor', SOURCE, expected)
    # Only the isolated Actions checkout is rewritten. SOURCE remains a parent.
    git('read-tree', '--reset', '-u', BASE)
    for path in ADDED:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(git('show', f'{SOURCE}:{path}'))

    path = 'src/goodteam_os/graph.py'
    text = Path(path).read_text(encoding='utf-8')
    text = replace_once(text, 'def resolve_role_adapter(',
        'def _team_execution_role(target: dict[str, object], role: str) -> str:\n'
        '    from goodteam_os.team_control_plane import attached_team_state\n\n'
        '    # Intake is a workflow stage, provided by the existing Research capability.\n'
        '    return "research" if role == "project_intake" and attached_team_state(target) is not None else role\n\n\n'
        'def resolve_role_adapter(')
    text = patch_function(text, 'resolve_role_adapter',
        '    state = attached_team_state(target)\n',
        '    role = _team_execution_role(target, role)\n    state = attached_team_state(target)\n')
    text = patch_function(text, '_worker_job_fields',
        '    from goodteam_os.team_control_plane import resolve_worker_for_role\n',
        '    from goodteam_os.team_control_plane import resolve_worker_for_role\n\n'
        '    role = _team_execution_role(target, role)\n')
    save(path, text)

    path = 'src/goodteam_os/web.py'
    text = Path(path).read_text(encoding='utf-8')
    guard = '''        provenance = review_dict.get("reviewer_provenance", {})
        badge = provenance.get("badge", {}) if isinstance(provenance, dict) else {}
        evidence_policy = review_dict.get("review_evidence_policy", {})
        unproven_policy = isinstance(evidence_policy, dict) and evidence_policy.get("state") in {
            "missing_evidence", "fallback_evidence", "non_provider_evidence", "failed_invocation",
        }
        if unproven_policy or (isinstance(badge, dict) and badge.get("kind") in {"missing", "builtin", "failed", "fallback"}):
            reasons.append({"kind": "missing_evidence", "text": "Reviewer evidence is missing, simulated or unsuccessful. Human review is required."})

'''
    text = patch_function(text, 'build_approval_recommendation',
                          '        messages = {}\n', guard + '        messages = {}\n')
    text = patch_function(text, 'build_project_work_state',
        '    project_config = _project_config_for_key(workspace, project_key)\n',
        '    from goodteam_os.delivery_outcomes import build_delivery_summary\n\n'
        '    project_config = _project_config_for_key(workspace, project_key)\n')
    text = patch_function(text, 'build_project_work_state',
        '        "worker_jobs": worker_jobs,\n',
        '        "worker_jobs": worker_jobs,\n'
        '        "delivery_summary": build_delivery_summary(workspace, state=state, jobs=worker_job_records),\n')
    text = patch_function(text, 'build_project_run_detail',
        '    key = normalize_project_key(project_key)\n',
        '    from goodteam_os.delivery_outcomes import build_delivery_summary\n\n'
        '    key = normalize_project_key(project_key)\n')
    text = patch_function(text, 'build_project_run_detail',
        '        "team_workspace": team_workspace,\n',
        '        "team_workspace": team_workspace,\n'
        '        "delivery_summary": build_delivery_summary(\n'
        '            workspace, state=state, jobs=worker_jobs, historical_reason=str(summary.get("reason", "")),\n'
        '        ),\n')
    route = '''            if parsed.path == "/api/delivery-artifact":
                from goodteam_os.delivery_outcomes import DeliveryArtifactError, read_delivery_artifact
                try:
                    self._send_json(200, read_delivery_artifact(
                        workspace, project_key=project_key,
                        run_id=_decode_project_run_id(query.get("run", [""])[0]),
                        artifact_key=query.get("key", [""])[0], version=query.get("version", [""])[0],
                    ))
                except DeliveryArtifactError as exc:
                    self._send_json(exc.status, {"error": "delivery_artifact_unavailable", "message": str(exc)})
                except Exception as exc:
                    self._send_api_error(exc)
                return
'''
    anchor = '            if parsed.path in {"/artifact", "/api/artifact"}:\n'
    text = replace_once(text, anchor, route + anchor)
    save(path, text)

    path = 'frontend/src/features/runs/workspace/TeamWorkspace.jsx'
    text = Path(path).read_text(encoding='utf-8')
    anchor = "import TaskDraftControls, { ProjectModeControl } from './TaskDraftControls';\n"
    text = replace_once(text, anchor, anchor + "import PreparationFacts from './PreparationFacts';\nimport DeliverySummary from './DeliverySummary';\n")
    anchor = '      <h3>{t(`work.taskAssistant.preparation.${kind}`)}</h3>\n'
    text = replace_once(text, anchor, anchor + '      <PreparationFacts requirements={requirements} t={t} />\n')
    text = replace_once(text, 'function OutcomesPane({\n', 'function OutcomesPane({\n  deliverySummary,\n')
    text = replace_once(text, '      {outcomes.length ? (\n', '      <DeliverySummary summary={deliverySummary} t={t} />\n      {outcomes.length ? (\n')
    text = replace_once(text, '        <OutcomesPane\n', '        <OutcomesPane\n          deliverySummary={data.delivery_summary}\n')
    save(path, text)
    # The remaining original CSS/locale additions have no duplicate review logic.
    small_patch = git('diff', SOURCE_BASE, SOURCE, '--', *PATCHED[1:4])
    git('apply', '--3way', data=small_patch)
    git('add', '-A')
    git('diff', '--cached', '--check')
    changed = set(git('diff', '--cached', '--name-only', BASE).decode().splitlines())
    if changed != set(ADDED + PATCHED):
        raise RuntimeError(f'Unexpected reconciliation paths: {sorted(changed ^ set(ADDED + PATCHED))}')
    tree = git('write-tree').decode().strip()
    author_env = dict(os.environ, GIT_AUTHOR_NAME='github-actions[bot]',
                      GIT_AUTHOR_EMAIL='41898282+github-actions[bot]@users.noreply.github.com',
                      GIT_COMMITTER_NAME='github-actions[bot]',
                      GIT_COMMITTER_EMAIL='41898282+github-actions[bot]@users.noreply.github.com')
    commit = git('commit-tree', tree, '-p', expected, '-p', BASE,
        data=b'refactor(delivery): reconcile remaining #426 increments with main\n\nRetain main ReviewPage and artifact reader; replay only the four remaining\nfunctional increments. Preserve #431-433 unchanged. Remove one-use helper.\nStill Draft: follow-up hardening and combined current CI are required.\n',
        env=author_env).decode().strip()
    out = Path(os.environ['RUNNER_TEMP']) / 'pr426-source-reconciliation'
    out.mkdir(parents=True, exist_ok=True)
    for name, ref in [('main', BASE), ('original', SOURCE), ('reconciled', commit)]:
        raw = git('archive', '--format=tar', ref)
        (out / f'{name}.tar').write_bytes(raw)
    manifest = {'base': BASE, 'source': SOURCE, 'parent': expected, 'commit': commit, 'tree': tree,
                'files': sorted(changed), 'archives': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.glob('*.tar')}}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    token = os.environ['RECONCILE_TOKEN']
    auth = base64.b64encode(('x-access-token:' + token).encode()).decode()
    push_env = dict(os.environ, GIT_CONFIG_COUNT='1',
                    GIT_CONFIG_KEY_0='http.https://github.com/.extraheader',
                    GIT_CONFIG_VALUE_0='AUTHORIZATION: basic ' + auth)
    remote = git('ls-remote', 'origin', BRANCH, env=push_env).decode().split()[0]
    if remote != expected:
        raise RuntimeError('PR branch changed; refusing to overwrite concurrent work.')
    # Plain push is fast-forward-only; the new commit has expected as its parent.
    git('push', 'origin', f'{commit}:{BRANCH}', env=push_env)
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
