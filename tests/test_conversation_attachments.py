import base64
import io

import pytest
from docx import Document
from openpyxl import Workbook
from PIL import Image
from pypdf import PdfWriter

from goodteam_os import web
from goodteam_os.conversation_attachments import read_attachment, resolve_attachments
from goodteam_os.manager_execution import ManagerExecutionError
from goodteam_os.projects import write_project_config
from goodteam_os.task_assistant import TaskAssistantService


@pytest.mark.parametrize('runtime', ['legacy', 'langgraph'])
def test_reference_files_reach_manager_with_exact_message_identity_and_retry(tmp_path, monkeypatch, runtime):
    if runtime == 'langgraph':
        from types import SimpleNamespace
        from langgraph.checkpoint.memory import InMemorySaver
        from goodteam_os import assistant_requests, manager_dialogue

        # Exercise the new dialogue path without using a real project's database.
        checkpointer = InMemorySaver()
        monkeypatch.setattr(assistant_requests, 'database_configured', lambda: True)
        monkeypatch.setattr(manager_dialogue, 'database', lambda: SimpleNamespace(manager_checkpointer=lambda: checkpointer))
    root = tmp_path / 'repository'
    root.mkdir()
    config = write_project_config(tmp_path / 'projects', 'DEMO', 'Demo', repo_path=str(root), worker_mode='fake')
    prefix = '/api/projects/DEMO/task-assistant'
    document = Document()
    document.add_paragraph('Word acceptance: preserve the current navigation.')
    sheet = Workbook()
    sheet.active.append(['Requirement', 'Expected'])
    sheet.active.append(['Width', 760])
    pdf = PdfWriter()
    pdf.add_blank_page(width=100, height=100)
    originals = {'reference.md': b'Use the narrow chat column.'}
    for name, writer in [('brief.docx', document.save), ('checks.xlsx', sheet.save), ('scan.pdf', pdf.write),
                         ('layout.png', lambda stream: Image.new('RGB', (12, 12), 'white').save(stream, format='PNG'))]:
        output = io.BytesIO()
        writer(output)
        originals[name] = output.getvalue()
    files = []
    for name, content in originals.items():
        request = {'name': name, 'data': base64.b64encode(content).decode()}
        status, uploaded = web.handle_project_api_post(tmp_path, prefix + '/attachments', request)
        assert status == 201
        repeated = web.handle_project_api_post(tmp_path, prefix + '/attachments', request)[1]
        assert repeated == uploaded
        metadata, path, _text = read_attachment(tmp_path, 'DEMO', root, uploaded['attachment']['id'])
        assert path.read_bytes() == content
        files.append(metadata)
    assert files[3]['status'] == 'empty'  # Scans cannot silently masquerade as extracted text.
    with pytest.raises(ValueError, match='unavailable'):
        resolve_attachments(tmp_path, 'OTHER', root, [files[0]['id']])
    with pytest.raises(ValueError):
        resolve_attachments(tmp_path, 'DEMO', root, ['../../outside'])

    calls = []
    def turn(**request):
        calls.append(request)
        if len(calls) == 1:
            raise ManagerExecutionError('Temporary provider failure')
        return {'intent': 'ambiguous', 'response': 'The attachment references are available.'}
    service = TaskAssistantService(tmp_path, project_config=config, manager_turn=turn)
    monkeypatch.setattr(web, 'TaskAssistantService', lambda *_a, **_kw: service)
    request = {'message_id': 'attachment-message', 'text': 'Review these references.', 'attachments': [item['id'] for item in files]}
    with pytest.raises(web.ProjectApiError):
        web.handle_project_api_post(tmp_path, prefix + '/messages', request)
    session = service.session('DEMO', root)
    original = next(item for item in session['timeline'] if item.get('message_id') == request['message_id'])
    assert original['attachments'] == files
    assert session['assistant_requests'][request['message_id']]['attachments'] == request['attachments']
    assert 'Word acceptance: preserve the current navigation.' in calls[0]['context']
    assert 'Width\\t760' in calls[0]['context']
    assert all(item['name'] in calls[0]['context'] for item in files)
    with pytest.raises(web.ProjectApiError, match='different attachments'):
        web.handle_project_api_post(tmp_path, prefix + '/messages', {**request, 'attachments': []})
    retry = {'action': 'retry_message', 'message_id': request['message_id'], 'expected_attempt': 1}
    result = web.handle_project_api_post(tmp_path, prefix + '/actions', retry)[1]
    web.handle_project_api_post(tmp_path, prefix + '/messages', request)
    assert len(calls) == 2
    assert not result['workflow_started']
    assert not result['session'].get('proposed_task')
    assert calls[1]['text'] == request['text']
