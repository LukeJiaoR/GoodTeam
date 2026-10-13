"""Owned recovery host: completed malformed response, then synthetic evidence assessment."""
import json
import sys
from pathlib import Path

from goodteam_os.workers import WorkerJobStore
from workflow_recovery_server import main

complete = WorkerJobStore._complete_fake


def synthetic_result(store, job_root, job):
    result = complete(store, job_root, job)
    if job.get('phase') != 'goal_assessment':
        raise AssertionError('Assessment recovery must not rerun any workflow Worker')
    if job['adapter'] != 'fake' or job['execution_policy'] != 'read_only':
        raise AssertionError('Only synthetic read-only assessment is allowed')
    marker = Path(sys.argv[1]) / 'assessment-protocol-failed'
    if not marker.exists():
        marker.touch()
        (job_root / 'result.md').write_text('{unfinished')
    else:
        packet = json.loads((job_root / 'context.md').read_text())
        ref, quote = next(iter(packet['evidence'].items()))
        (job_root / 'result.md').write_text(json.dumps({'requires_new_scope': False, 'checks': [
            {'criterion': i, 'status': 'met', 'evidence_ref': ref, 'quote': quote}
            for i in range(len(packet['acceptance_criteria']))]}))
    return result


WorkerJobStore._complete_fake = synthetic_result

if __name__ == '__main__':
    main()
