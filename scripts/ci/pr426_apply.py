"""Apply an already-reviewed, hash-pinned local patch to the #426 branch only.

Remove all one-use workflows and this script. This does not run or alter the
ordinary CI gate, call providers, deploy, merge, or touch a real workspace.
"""
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess

REPO = 'LukeJiaoR/GoodTeam'
REF = 'refs/heads/codex/delivery-outcomes'
BASE = 'fd5cc337e681a8e7e9f63094ee8e22dd766a47c8'
PARENT = 'f0e2e859bedec230274b743115087a14034e03e2'
PATCH_HASH = '312e86c5547a1bf00e4dd43bd84a2a1c557e5e116c17309f8cbae632f30a0e3c'
PATCHED = {
    'frontend/e2e/business-loop-helpers.js', 'frontend/e2e/delivery-reports.spec.js',
    'frontend/src/features/runs/workspace/DeliverySummary.jsx',
    'frontend/src/features/runs/workspace/DeliverySummary.test.jsx',
    'frontend/src/features/runs/workspace/PreparationFacts.jsx',
    'frontend/src/features/runs/workspace/PreparationFacts.test.jsx',
    'frontend/src/i18n/locales/en.js', 'frontend/src/i18n/locales/zh-CN.js',
    'src/goodteam_os/delivery_outcomes.py', 'src/goodteam_os/web.py',
    'tests/test_delivery_outcomes.py', 'tests/test_delivery_review_truth.py',
    'tests/test_intake_team_dispatch.py',
}


def git(*args, data=None, env=None):
    result = subprocess.run(['git', *args], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, check=False)
    if result.returncode:
        raise RuntimeError(f'git {args[0]} failed: {result.stderr.decode(errors="replace")}')
    return result.stdout


def main():
    head = os.environ['EXPECTED_HEAD']
    if os.environ.get('GITHUB_REPOSITORY') != REPO or os.environ.get('GITHUB_REF') != REF:
        raise RuntimeError('Wrong repository or branch.')
    if git('rev-parse', 'HEAD').decode().strip() != head or git('rev-parse', 'HEAD^').decode().strip() != PARENT:
        raise RuntimeError('Unexpected patch parent/head.')
    if git('rev-parse', 'origin/main').decode().strip() != BASE:
        raise RuntimeError('Main changed. Re-review before applying.')
    encoded = ''.join(Path(f'scripts/ci/pr426-patch-{i}.txt').read_text().strip() for i in range(4))
    patch = gzip.decompress(base64.b64decode(encoded, validate=True))
    if hashlib.sha256(patch).hexdigest() != PATCH_HASH:
        raise RuntimeError('Patch bytes differ from the locally reviewed files.')
    git('apply', '--check', data=patch)
    git('apply', data=patch)
    for path in ['.github/workflows/pr426-hardening.yml', '.github/workflows/pr426-offline-deps.yml',
                 'scripts/ci/pr426_apply.py', *[f'scripts/ci/pr426-patch-{i}.txt' for i in range(4)]]:
        Path(path).unlink()
    git('add', '-A')
    git('diff', '--cached', '--check')
    changed = set(git('diff', '--cached', '--name-only', PARENT).decode().splitlines())
    if changed != PATCHED | {'.github/workflows/pr426-offline-deps.yml'}:
        raise RuntimeError(f'Unexpected changed files: {sorted(changed)}')
    # The final workflow directory is exactly main, not merely a green helper.
    if git('diff', '--cached', BASE, '--', '.github/workflows'):
        raise RuntimeError('Final tree must not retain temporary workflow changes.')
    tree = git('write-tree').decode().strip()
    env = dict(os.environ, GIT_AUTHOR_NAME='github-actions[bot]',
               GIT_AUTHOR_EMAIL='41898282+github-actions[bot]@users.noreply.github.com',
               GIT_COMMITTER_NAME='github-actions[bot]',
               GIT_COMMITTER_EMAIL='41898282+github-actions[bot]@users.noreply.github.com')
    commit = git('commit-tree', tree, '-p', head, env=env, data=(
        'fix(delivery): harden exact report reads and remaining #426 contracts\n\n'
        'Keep main Review and conversation behavior. Add isolated intake job\n'
        'identity/gating coverage, exact preparation facts, conservative review\n'
        'metadata handling, retry-aware outcome status and report read guards.\n'
        'Add current/history browser acceptance and retain old safety assertions.\n'
        'Remove all temporary patch and dependency workflows before final CI.\n'
        'Still requires current-head CI; no real-model run or merge.\n'
    ).encode()).decode().strip()
    output = Path(os.environ['RUNNER_TEMP']) / 'pr426-applied-source'
    output.mkdir(parents=True, exist_ok=True)
    raw = git('archive', '--format=tar', commit)
    (output / 'source.tar').write_bytes(raw)
    manifest = {'commit': commit, 'tree': tree, 'base': BASE, 'parent': head,
                'patch_sha256': PATCH_HASH, 'archive_sha256': hashlib.sha256(raw).hexdigest(),
                'changes': sorted(changed)}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    auth = base64.b64encode(('x-access-token:' + os.environ['PATCH_TOKEN']).encode()).decode()
    push_env = dict(os.environ, GIT_CONFIG_COUNT='1',
                    GIT_CONFIG_KEY_0='http.https://github.com/.extraheader',
                    GIT_CONFIG_VALUE_0='AUTHORIZATION: basic ' + auth)
    if git('ls-remote', 'origin', REF, env=push_env).decode().split()[0] != head:
        raise RuntimeError('The PR branch changed. Refusing to overwrite concurrent work.')
    git('push', 'origin', f'{commit}:{REF}', env=push_env)
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
