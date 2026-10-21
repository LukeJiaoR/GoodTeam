"""Durable release corrections from the current implementation job only."""
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from goodteam_os.reviewer_dispatch import artifact_version_for_path


INPUTS = ('rfc', 'implementation_handoff', 'qa_report', 'code_review')


def _inputs(state):
    return {key: {'path': state.artifacts.get(key, ''),
                  'version': artifact_version_for_path(state.artifacts.get(key, ''))}
            for key in INPUTS}


def _request(state):
    return hashlib.sha256(state.change_requests.get('release', '').encode()).hexdigest()


class ReleaseCandidateError(ValueError):
    """Expected delivery artifact validation failure, not an interrupted decision."""


def block_release_candidate(state, error):
    from goodteam_os.state import ApprovalDecision

    state.current_node = 'implementation'
    state.next_node = 'blocked'
    state.approvals['release'] = ApprovalDecision.PENDING
    if str(error) not in state.blockers:
        state.blockers.append(str(error))
    return state


def _blocked():
    return ReleaseCandidateError('Release correction is missing or stale. Retry delivery and review it again; '
                        'the current implementation job must return job-local release-notes.md. '
                        'The rejected template was not substituted.')


def capture_release_candidate(state, workspace, project_config, results):
    from goodteam_os.artifact_revisions import current_job_artifact
    from goodteam_os.projects import load_project_target
    from goodteam_os.workers import WorkerJobStore
    from goodteam_os.graph import resolve_project_runtime_root

    state.release_candidate = {}
    if project_config is None:
        raise _blocked()
    runtime = resolve_project_runtime_root(dict(load_project_target(project_config).get('repos', {})))
    if runtime is None:
        raise _blocked()
    store = WorkerJobStore(runtime)
    implementations = [result for result in results if result.get('role') == 'implementation']
    if len(implementations) != 1:
        raise _blocked()
    result = implementations[0]
    job_id = str(result.get('job_id', ''))
    if not job_id or job_id in {'.', '..'} or Path(job_id).name != job_id or result.get('status') != 'completed':
        raise _blocked()
    job_root = store.root / job_id
    if not job_root.resolve().is_relative_to(store.root.resolve()):
        raise _blocked()
    try:
        job = json.loads((job_root / 'job.json').read_text())
    except (OSError, ValueError):
        raise _blocked() from None
    if (job.get('id') != job_id or job.get('run_id') != state.run_id or job.get('role') != 'implementation'
            or job.get('status') != 'completed' or 'release-notes.md' not in job.get('expected_artifacts', [])):
        raise _blocked()
    source = job_root / 'release-notes.md'
    content = current_job_artifact(job_root, {'result_path': str(source)},
                                   artifact_key='release_notes', filename='release-notes.md')
    if content is None:
        raise _blocked()
    path = Path(workspace) / 'artifacts' / 'runs' / state.run_id / 'release_candidates' / f'{uuid4().hex}.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(source.read_bytes())
    state.release_candidate = dict(run_id=state.run_id, project_key=state.project_key, job_id=job_id,
                                   request=_request(state), inputs={}, path=str(path),
                                   version=artifact_version_for_path(str(path)))


def bind_release_candidate(state):
    if not state.release_candidate:
        raise _blocked()
    # QA now completed successfully. Keep the implementation snapshot immutable;
    # these bindings prevent reuse after changes to the delivery it describes.
    state.release_candidate['inputs'] = _inputs(state)


def validated_release_candidate(state, workspace):
    candidate = state.release_candidate
    path = Path(str(candidate.get('path', ''))).resolve()
    root = (Path(workspace) / 'artifacts' / 'runs' / state.run_id / 'release_candidates').resolve()
    if (candidate.get('run_id') != state.run_id or candidate.get('project_key') != state.project_key
            or not candidate.get('job_id') or candidate.get('request') != _request(state)
            or candidate.get('inputs') != _inputs(state)
            or not path.is_relative_to(root) or not path.is_file()
            or not candidate.get('version') or candidate['version'] != artifact_version_for_path(str(path))):
        raise _blocked()
    return path.read_bytes().decode('utf-8')
