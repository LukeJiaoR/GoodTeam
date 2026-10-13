"""Real approval HTTP/background host with synthetic read-only assessment only."""
import json

from goodteam_os.workers import WorkerJobStore
from workflow_recovery_server import main

complete = WorkerJobStore._complete_fake


def synthetic_assessment(store, job_root, job):
    if job.get('phase') != 'goal_assessment' or job.get('execution_policy') != 'read_only':
        raise AssertionError('Seeded release approval may only start a read-only Goal assessment')
    result = complete(store, job_root, job)
    packet = json.loads((job_root / 'context.md').read_text())
    ref, quote = next(iter(packet['evidence'].items()))
    (job_root / 'result.md').write_text(json.dumps({'requires_new_scope': False, 'checks': [
        {'criterion': index, 'status': 'met', 'evidence_ref': ref, 'quote': quote}
        for index in range(len(packet['acceptance_criteria']))]}))
    return result


WorkerJobStore._complete_fake = synthetic_assessment

if __name__ == '__main__':
    main()
