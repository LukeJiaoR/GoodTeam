"""Ordinary Role input contract only; no native runtime or paid invocation."""
import json
from pathlib import Path

import pytest

from goodteam_os.skills.availability import SkillAvailabilityStore
from goodteam_os.skills.bindings import default_binding
from goodteam_os.skills.content import ContentLimits, ContentSession, SkillContextProvider
from goodteam_os.skills.library import SkillLibraryService
from goodteam_os.skills.models import SkillError, content_digest
from goodteam_os.skills.task_context import task_skill_context
from goodteam_os.skills.task_locks import TaskSkillIdentity, TaskSkillLockStore, TaskSkillPlan
from goodteam_os.workers import WorkerJobStore


class FixtureAuthority:
    def authorize(self, snapshot, *, context_id, operation):
        request = snapshot['job_input']['request']
        return ContentSession(request['adapter'], 'fixture', 'fixture', content_digest(b'fixture'),
                              request['model'], content_digest(b'config'), context_id, 'model-free-test',
                              'contract_fixture')


@pytest.fixture
def invocation(tmp_path):
    from goodteam_os.service import service_state_dir
    operator = Path(service_state_dir())
    workspace = tmp_path / 'worker'
    workspace.mkdir()
    library = SkillLibraryService(operator, worker_roots=(workspace,))
    items = []
    for name, activation in [('shared', 'required'), ('optional', 'on_demand')]:
        source = tmp_path / name
        source.mkdir()
        (source / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: bounded method\n---\n{name} complete body\n')
        (source / 'detail.md').write_text(f'{name} detail bytes')
        preview = library.preview_local(str(source), command_key=name)
        installed = library.install(preview['proposal_id'])['installed']
        items.append({**{key: installed[key] for key in ('skill_id', 'version_id')},
                      'package_digest': preview['package_digest'], 'activation': activation, 'enabled': True})
    locks = TaskSkillLockStore(operator / 'skills/task-locks', library.store, worker_roots=(workspace,))
    SkillAvailabilityStore(operator / 'skills/availability', library.store, worker_roots=(workspace,)).initialize()
    task = TaskSkillIdentity('direct_task', 'project', 'bounded-task')
    bindings = {f'role:{role}': {**default_binding(f'role:{role}', project_key='project'),
                                'mode': 'custom', 'items': items} for role in ('developer', 'researcher')}
    contracts = {subject: {'ref': binding['role_contract_ref'], 'subject': subject} for subject, binding in bindings.items()}
    ref = locks.begin(task, command_key='start', task_input={},
                      resolve_plan=lambda: TaskSkillPlan(1, bindings, contracts))
    workers = WorkerJobStore(workspace)

    def create(role, adapter='codex'):
        job = workers.create_job(run_id='run', role=role, adapter=adapter, model='fixture',
                                 task=f'{role} purpose; scope; acceptance criteria', context=f'{role} private context',
                                 constraints=['no external side effects'], expected_artifacts=['result'],
                                 project_key='project', idempotency_key=role,
                                 task_skill_context=task_skill_context(task, ref), skill_subject=f'role:{role}')
        provider = SkillContextProvider(workers, job['id'], authority=FixtureAuthority())
        context = provider.prepare('context-' + role)
        return provider, context, job
    return create, items, workspace


def test_role_input_preserves_locked_contract_and_isolates_shared_methods(invocation):
    create, items, workspace = invocation
    developer, ref, job = create('developer')
    researcher, other, _ = create('researcher')
    data = developer.worker_input(ref)
    payload = json.loads(data)
    assert payload['role'] == 'developer'
    assert payload['task'] == 'developer purpose; scope; acceptance criteria'
    assert payload['context'] == 'developer private context'
    assert 'role:developer' in payload['role_contract']
    assert 'shared complete body' in payload['required_methods'][0]['text']
    assert 'optional complete body' not in data.decode()
    assert len(payload['method_catalog']) == 2
    assert str(workspace) not in data.decode()
    assert 'researcher private context' not in data.decode()
    assert json.loads(researcher.worker_input(other))['context'] == 'researcher private context'
    assert developer.worker_input(ref) == data
    assert developer.read_resource(ref, skill_id=items[1]['skill_id'], relative='detail.md', call_id='read-1') == b'optional detail bytes'
    with pytest.raises(SkillError):
        developer.worker_input(other)
    with pytest.raises(SkillError):
        developer.read_resource(ref, skill_id=items[1]['skill_id'], relative='../SKILL.md', call_id='escape')
    # Preparation must neither activate legacy Codex nor mark a Skill loaded.
    result = developer.workers.run_job(job['id'])
    assert result['skill_runtime_status'] == 'not_connected'
    assert result['invocation_evidence']['external_invocation'] is False


def test_role_input_keeps_admission_and_complete_budget_fail_closed(invocation):
    create, _, _ = invocation
    provider, ref, job = create('developer')
    with pytest.raises(SkillError) as error:
        SkillContextProvider(provider.workers, job['id']).worker_input(ref)
    assert error.value.code == 'runtime_conformance_unverified'
    from goodteam_os.skills.worker_input import render_worker_input
    private = provider.private_input(ref)
    rendered = provider.worker_input(ref)
    assert render_worker_input(private, maximum=len(rendered)) == rendered
    with pytest.raises(SkillError) as error:
        render_worker_input(private, maximum=len(rendered) - 1)
    assert error.value.code == 'content_budget_exceeded'
    private['original_job_input']['request']['role'] = 'code_review'
    with pytest.raises(SkillError) as error:
        render_worker_input(private, maximum=100000)
    assert error.value.code == 'review_protocol_required'
    provider.limits = ContentLimits(max_input_bytes=10)
    with pytest.raises(SkillError) as error:
        provider.worker_input(ref)
    assert error.value.code == 'content_budget_exceeded'
