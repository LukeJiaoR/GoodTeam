"""Knowledge browsing preserves audit evidence while excluding execution noise."""
import hashlib
import json

import pytest

from goodteam_os.memory import RoleMemoryStore
from goodteam_os.memory_core.projections import event_id
from goodteam_os.memory_writeback import write_back_events
from goodteam_os.web import build_memory_event_detail, build_memory_events
from goodteam_os.workers import parse_categorized_result_sections, parse_result_sections


def test_filter_precedes_limit_and_search_without_hiding_superseded_history(tmp_path):
    store = RoleMemoryStore(tmp_path)
    old = store.append_memory_event('qa', horizon='mid-term', text='Knowledge database old', source='one/result.md')
    new = store.append_memory_event('qa', horizon='mid-term', text='Knowledge database new', source='two/result.md',
                                    supersedes=event_id(old), category='caveats')
    for source in ['job-a/provenance', 'job-b/provenance', 'worker_failure:job-c']:
        store.append_memory_event('qa', horizon='short-term', text=f'Knowledge database {source}', source=source)
    wal = store.event_path('qa').read_bytes()
    assert [e['id'] for e in store.list_events('qa', limit=1)] == [event_id(new)]
    assert {e['id'] for e in store.list_events('qa', query='database', limit=2)} == {event_id(old), event_id(new)}
    rows = build_memory_events(tmp_path, 'DEMO', 'qa')['events']
    assert [(e['category'], e['is_superseded']) for e in rows] == [('caveats', False), ('unclassified', True)]
    all_rows = build_memory_events(tmp_path, 'DEMO', 'qa', include_execution=True)['events']
    assert len(all_rows) == 5
    assert sum(e['category'] == 'execution' for e in all_rows) == 3
    assert len(store.list_events('qa', include_execution=True, limit=1)) == 1
    assert [e['id'] for e in store.retrieve_relevant_events('qa', 'database')] == [event_id(new)]
    detail = build_memory_event_detail(tmp_path, event_id(old))
    assert detail['text'] == old['text'] and detail['source'] == old['source']
    assert store.event_path('qa').read_bytes() == wal


def test_categories_only_from_sections_and_original_identity_and_full_body_survive(tmp_path):
    store = RoleMemoryStore(tmp_path)
    result = '\n'.join(f'## {heading}\n- {heading} 中文原文 ' + 'long text ' * 40
                       for heading in ['Learned', 'Caveats', 'Decisions', 'Open Questions'])
    items = parse_categorized_result_sections(result)
    assert parse_result_sections(result) == [item[:2] for item in items]
    status, receipt = write_back_events(store, 'qa', items=items, source='job/result.md', result_bytes=result.encode())
    assert status['appended'] == 4
    assert receipt['new_event_ids'] == [event_id(e) for e in store._read_events('qa')]
    assert {e['category'] for e in store.list_events('qa')} == {'learned', 'caveats', 'decisions', 'open_questions'}
    for event in store._read_events('qa'):
        legacy = {k: v for k, v in event.items() if k != 'category'}
        assert event_id(legacy) == event_id(event)
        row = next(e for e in store.list_events('qa') if e['id'] == event_id(event))
        assert row['body'] == event['text'] and len(row['body']) > 240
    replay, replay_receipt = write_back_events(store, 'qa', items=items, source='job/result.md', result_bytes=result.encode())
    assert replay['appended'] == 0
    assert replay_receipt['replayed_event_ids'] == receipt['new_event_ids']
    assert 'category' not in json.dumps(receipt)


def test_legacy_duplicate_is_not_rewritten_or_reclassified(tmp_path):
    store = RoleMemoryStore(tmp_path)
    old = store.append_memory_event('qa', horizon='mid-term', text='Preserved original', source='old/result.md')
    wal = store.event_path('qa').read_bytes()
    _, receipt = write_back_events(store, 'qa', items=[('mid-term', old['text'], 'learned')],
                                   source='new/result.md', result_bytes=b'result')
    assert receipt['duplicate_event_ids'] == [event_id(old)]
    assert store.list_events('qa')[0]['category'] == 'unclassified'
    assert store.event_path('qa').read_bytes() == wal


@pytest.mark.parametrize('extra', [False, True])
def test_categorized_commit_reconciliation_stays_exact(tmp_path, monkeypatch, extra):
    store = RoleMemoryStore(tmp_path)
    original = store.append_memory_event
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        if extra:
            # Another append, even with the same category, cannot establish the exact single write.
            original('qa', horizon='mid-term', text='unexpected', source='other')
        raise OSError('projection failure')
    monkeypatch.setattr(store, 'append_memory_event', fail)
    _, receipt = write_back_events(store, 'qa', items=[('mid-term', 'new fact', 'learned')],
                                   source='job/result.md', result_bytes=b'result')
    assert receipt['failure_outcome'] == ('unknown' if extra else 'committed')
    assert receipt['appended'] == (0 if extra else 1)
    if not extra:
        assert receipt['new_event_ids'] == [event_id(store._read_events('qa')[0])]


def test_execution_audit_stays_on_disk_but_not_in_pickup_or_index(tmp_path):
    store = RoleMemoryStore(tmp_path)
    for source in ['first/provenance', 'second/provenance', 'worker_failure:third']:
        store.append_memory_event('qa', horizon='short-term', text=f'PRIVATE_EXECUTION_NOISE {source}', source=source)
    store.append_memory_event('qa', horizon='short-term', text='Unresolved choice?', source='job/result.md', category='open_questions')
    assert 'PRIVATE_EXECUTION_NOISE' in store.memory_path('qa', 'short-term').read_text()
    assert 'PRIVATE_EXECUTION_NOISE' not in json.dumps(store.build_pickup_packet('qa'))
    assert 'PRIVATE_EXECUTION_NOISE' not in json.dumps(store.refresh_memory_index('qa'))
    assert len(store._read_events('qa')) == 4
    assert store.list_events('qa')[0]['category'] == 'open_questions'


def test_old_index_rebuilds_summary_policy_without_rewriting_events(tmp_path):
    store = RoleMemoryStore(tmp_path)
    store.append_memory_event('qa', horizon='short-term', text='AUDIT_LOG', source='old/provenance')
    index = store.read_memory_index('qa')
    index.pop('retrieval_policy')
    index['horizons']['short-term']['summary'] = 'AUDIT_LOG'
    index['horizons']['short-term']['summary_sha256'] = hashlib.sha256(b'AUDIT_LOG').hexdigest()
    store.index_path('qa').write_text(json.dumps(index))
    wal = store.event_path('qa').read_bytes()
    rebuilt = store.read_memory_index('qa')
    assert rebuilt['retrieval_policy'] == 'knowledge_without_execution_v3'
    assert 'AUDIT_LOG' not in rebuilt['horizons']['short-term']['summary']
    assert store.event_path('qa').read_bytes() == wal


def test_old_frozen_packet_policy_cannot_keep_audit_when_retrieval_is_empty(tmp_path):
    from goodteam_os.memory_core.context import _pickup_content_digest

    store = RoleMemoryStore(tmp_path)
    store.append_memory_event('qa', horizon='short-term', text='AUDIT_SENTINEL', source='old/provenance')
    packet = store.build_pickup_packet('qa')
    packet['provenance'].pop('retrieval_policy', None)
    packet['short_term'] = 'AUDIT_SENTINEL'
    packet['relevant_events'] = []
    packet['provenance']['content_sha256'] = _pickup_content_digest(packet)
    captured = json.dumps(packet)
    consumed = store.validate_pickup_packet(packet)
    assert consumed['context_status'] != 'available'
    assert consumed['short_term'] == ''
    assert consumed['summaries'] == {'long-term': '', 'mid-term': ''}
    assert json.dumps(packet) == captured


@pytest.mark.parametrize('horizon', ['short-term', 'mid-term', 'long-term'])
@pytest.mark.parametrize('source', ['job/provenance', 'worker_failure:job', 'execution:exec_test:final_provenance'])
def test_multiline_execution_blocks_never_enter_new_pickup(tmp_path, horizon, source):
    store = RoleMemoryStore(tmp_path)
    store.append_memory_event('qa', horizon=horizon,
                              text='MULTILINE_AUDIT\n\n## Embedded heading\n- continuation', source=source)
    store.append_memory_event('qa', horizon=horizon, text='KEEP_KNOWLEDGE\nsecond line', source='job/result.md')
    original = store.memory_path('qa', horizon).read_bytes()
    packet = store.build_pickup_packet('qa', query='KEEP_KNOWLEDGE')
    serialized = json.dumps(packet)
    assert 'MULTILINE_AUDIT' not in serialized
    assert 'Embedded heading' not in serialized
    assert 'continuation' not in serialized
    assert 'KEEP_KNOWLEDGE' in serialized
    assert store.memory_path('qa', horizon).read_bytes() == original


@pytest.mark.parametrize('policy', [None, 'knowledge_without_execution_v1', 'unknown_policy'])
def test_frozen_policy_change_retains_only_verified_old_hits_without_recapture(tmp_path, monkeypatch, policy):
    import copy
    from goodteam_os.memory_core.context import _pickup_content_digest

    store = RoleMemoryStore(tmp_path)
    old = store.append_memory_event('qa', horizon='mid-term', text='database CAPTURED_FACT', source='old/result.md')
    packet = store.build_pickup_packet('qa', query='database')
    if policy is None:
        packet['provenance'].pop('retrieval_policy')
    else:
        packet['provenance']['retrieval_policy'] = policy
    packet['summaries']['mid-term'] = 'FROZEN_AUDIT'
    packet['provenance']['content_sha256'] = _pickup_content_digest(packet)
    captured = copy.deepcopy(packet)
    store.append_memory_event('qa', horizon='mid-term', text='database NEW_AFTER_CAPTURE', source='new/result.md')
    monkeypatch.setattr(store, 'build_pickup_packet', lambda *_a, **_k: pytest.fail('must not recapture a frozen packet'))
    consumed = store.validate_pickup_packet(packet)
    assert consumed['context_status'] == 'partial'
    assert 'pickup_policy_changed' in consumed['context_reasons']
    assert consumed['short_term'] == '' and not any(consumed['summaries'].values())
    assert [event['id'] for event in consumed['relevant_events']] == [event_id(old)]
    assert 'NEW_AFTER_CAPTURE' not in json.dumps(consumed)
    assert 'FROZEN_AUDIT' not in json.dumps(consumed)
    assert packet == captured


def test_machine_appendix_is_rebuilt_without_trusting_edited_multiline_or_headings(tmp_path):
    store = RoleMemoryStore(tmp_path)
    path = store.ensure_role_memory('qa')['short-term']
    path.write_text('# Legacy notes\n- KEEP_LEGACY_PREFIX\n')
    store.append_memory_event('qa', horizon='short-term', text='AUDIT_FIRST\n## Nested\nAUDIT_LAST', source='job/provenance')
    store.append_memory_event('qa', horizon='short-term', text='KEEP_WAL_FACT', source='job/result.md')
    path.write_text(path.read_text().replace('AUDIT_FIRST', 'EDITED_AUDIT_FIRST') + '\n# Unknown appendix notes\nUNKNOWN_APPENDIX\n')
    # CRLF conversion cannot turn a generated appendix into trusted legacy notes.
    path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
    raw = path.read_bytes()
    packet = store.build_pickup_packet('qa')
    assert packet['context_status'] == 'partial'
    assert 'legacy_execution_unverifiable' in packet['context_reasons']
    assert 'KEEP_LEGACY_PREFIX' not in packet['short_term']
    assert 'KEEP_WAL_FACT' in packet['short_term']
    assert 'AUDIT' not in packet['short_term'] and 'UNKNOWN_APPENDIX' not in packet['short_term']
    assert store.validate_pickup_packet(packet)['short_term'] == packet['short_term']
    assert path.read_bytes() == raw


@pytest.mark.parametrize('heading', ['### Memory Events', 'prefix ## Memory Events', '## Memory Events'])
def test_legacy_writer_heading_substrings_cannot_bypass_execution_filter(tmp_path, heading):
    store = RoleMemoryStore(tmp_path)
    path = store.ensure_role_memory('qa')['short-term']
    store.append_memory_event('qa', horizon='short-term', text='BASE_WAL_FACT', source='old/result.md')
    path.write_text(f'# Legacy\n{heading}\n- existing notes\n')
    store.append_memory_event('qa', horizon='short-term', text='AUDIT_SENTINEL\ncontinuation', source='job/provenance')
    raw = path.read_bytes()
    packet = store.build_pickup_packet('qa')
    assert 'AUDIT_SENTINEL' not in packet['short_term']
    assert 'continuation' not in packet['short_term']
    assert path.read_bytes() == raw


@pytest.mark.parametrize('source', ['worker_failure:old', 'old/provenance'])
@pytest.mark.parametrize('with_appendix', [False, True])
def test_ambiguous_legacy_execution_prefix_is_withheld_as_a_whole(tmp_path, source, with_appendix):
    store = RoleMemoryStore(tmp_path)
    path = store.ensure_role_memory('qa')['short-term']
    path.write_text(f'# Legacy\n- OLD_AUDIT_SENTINEL\n\n## Body heading\nlast line Source: `{source}`.\n')
    if with_appendix:
        store.append_memory_event('qa', horizon='short-term', text='KEEP_WAL_FACT', source='job/result.md')
    raw = path.read_bytes()
    packet = store.build_pickup_packet('qa')
    assert 'OLD_AUDIT_SENTINEL' not in packet['short_term']
    assert 'Body heading' not in packet['short_term']
    assert packet['context_status'] == 'partial'
    assert 'legacy_execution_unverifiable' in packet['context_reasons']
    assert packet['provenance']['legacy_sources']['short-term']['status'] == 'withheld_execution_unverifiable'
    if with_appendix:
        assert 'KEEP_WAL_FACT' in packet['short_term']
    assert store.validate_pickup_packet(packet)['short_term'] == packet['short_term']
    assert path.read_bytes() == raw


def test_missing_appendix_marker_with_execution_wal_does_not_trust_modified_source(tmp_path):
    store = RoleMemoryStore(tmp_path)
    store.append_memory_event('qa', horizon='short-term', text='AUDIT_SENTINEL\ncontinuation', source='job/provenance')
    path = store.memory_path('qa', 'short-term')
    path.write_text(path.read_text().replace('## Memory Events', '## Edited heading').replace(' Source: `job/provenance`.', ''))
    raw = path.read_bytes()
    packet = store.build_pickup_packet('qa')
    assert packet['short_term'] == ''
    assert 'legacy_execution_unverifiable' in packet['context_reasons']
    assert path.read_bytes() == raw


@pytest.mark.parametrize('edit_body', [False, True])
def test_execution_embedded_marker_cannot_replace_the_real_appendix_boundary(tmp_path, edit_body):
    store = RoleMemoryStore(tmp_path)
    store.append_memory_event('qa', horizon='short-term', text='AUDIT_SENTINEL\n## Memory Events\nAUDIT_TAIL', source='job/provenance')
    store.append_memory_event('qa', horizon='short-term', text='KEEP_WAL_FACT', source='job/result.md')
    path = store.memory_path('qa', 'short-term')
    edited = path.read_text().replace('## Memory Events', '## Edited heading', 1)
    if edit_body:
        edited = edited.replace('AUDIT_SENTINEL', 'EDITED_AUDIT_SENTINEL')
    path.write_text(edited)
    raw = path.read_bytes()
    packet = store.build_pickup_packet('qa')
    assert 'AUDIT' not in packet['short_term']
    assert 'KEEP_WAL_FACT' in packet['short_term']
    assert 'legacy_execution_unverifiable' in packet['context_reasons']
    assert path.read_bytes() == raw


@pytest.mark.parametrize('source', ['old/provenance', 'worker_failure:old'])
@pytest.mark.parametrize('horizon', ['short-term', 'mid-term', 'long-term'])
def test_legacy_execution_marker_inside_body_without_execution_wal_is_withheld(tmp_path, source, horizon):
    store = RoleMemoryStore(tmp_path)
    store.append_memory_event('qa', horizon=horizon, text='KEEP_WAL_FACT', source='job/result.md')
    path = store.memory_path('qa', horizon)
    path.write_text(f'# Legacy\n- LEGACY_AUDIT_SENTINEL\n## Memory Events\nAUDIT_TAIL Source: `{source}`.\n')
    raw = path.read_bytes()
    packet = store.build_pickup_packet('qa')
    context = packet['short_term'] if horizon == 'short-term' else packet['summaries'][horizon]
    assert 'AUDIT' not in context
    assert 'KEEP_WAL_FACT' in context
    assert packet['context_status'] == 'partial'
    assert 'legacy_execution_unverifiable' in packet['context_reasons']
    assert store.validate_pickup_packet(packet)['context_status'] == 'partial'
    assert path.read_bytes() == raw


@pytest.mark.parametrize('source', ['job/provenance', 'worker_failure:run:implementation:qa',
                                    'execution:exec_test:final_provenance'])
def test_all_written_audit_source_families_keep_ids_but_leave_normal_context(tmp_path, source):
    store = RoleMemoryStore(tmp_path)
    fact = store.append_memory_event('orchestrator', horizon='long-term', text='shared KEEP_FACT', source='old/result.md')
    audit = store.append_memory_event('orchestrator', horizon='long-term', text='shared AUDIT_SENTINEL', source=source)
    wal = store.event_path('orchestrator').read_bytes()
    assert [e['id'] for e in store.list_events('orchestrator', query='shared', limit=1)] == [event_id(fact)]
    rows = store.list_events('orchestrator', include_execution=True)
    assert next(e for e in rows if e['id'] == event_id(audit))['category'] == 'execution'
    assert [e['id'] for e in store.retrieve_relevant_events('orchestrator', 'shared')] == [event_id(fact)]
    assert 'AUDIT_SENTINEL' not in json.dumps(store.read_memory_index('orchestrator'))
    assert 'AUDIT_SENTINEL' not in json.dumps(store.build_pickup_packet('orchestrator', query='shared'))
    detail = build_memory_event_detail(tmp_path, event_id(audit))
    assert detail['source'] == source and detail['text'] == audit['text']
    assert store.event_path('orchestrator').read_bytes() == wal


@pytest.mark.parametrize('source', ['', 'old/result.md', 'execution:exec_test:result',
                                    'execution:exec_test:final_provenance/notes', 'notes:final_provenance',
                                    'execution::final_provenance', 'historical final_provenance'])
def test_similar_or_unclassified_sources_remain_knowledge(tmp_path, source):
    store = RoleMemoryStore(tmp_path)
    fact = store.append_memory_event('qa', horizon='long-term', text='KEEP_LEGACY_FACT', source=source)
    assert store.list_events('qa')[0]['category'] == 'unclassified'
    assert store.retrieve_relevant_events('qa', 'KEEP_LEGACY_FACT')[0]['id'] == event_id(fact)
    assert 'KEEP_LEGACY_FACT' in json.dumps(store.build_pickup_packet('qa'))


def test_v2_frozen_final_provenance_and_index_are_invalidated_without_rewriting_capture(tmp_path):
    import copy
    from goodteam_os.memory_core.context import _pickup_content_digest

    store = RoleMemoryStore(tmp_path)
    store.append_memory_event('orchestrator', horizon='long-term', text='AUDIT_SENTINEL',
                              source='execution:exec_test:final_provenance')
    index = store.read_memory_index('orchestrator')
    index['retrieval_policy'] = 'knowledge_without_execution_v2'
    index['horizons']['long-term']['summary'] = 'AUDIT_SENTINEL'
    index['horizons']['long-term']['summary_sha256'] = hashlib.sha256(b'AUDIT_SENTINEL').hexdigest()
    store.index_path('orchestrator').write_text(json.dumps(index))
    assert 'AUDIT_SENTINEL' not in json.dumps(store.read_memory_index('orchestrator'))
    packet = store.build_pickup_packet('orchestrator')
    packet['provenance']['retrieval_policy'] = 'knowledge_without_execution_v2'
    packet['summaries']['long-term'] = 'AUDIT_SENTINEL'
    packet['relevant_events'] = []
    packet['provenance']['content_sha256'] = _pickup_content_digest(packet)
    original = copy.deepcopy(packet)
    consumed = store.validate_pickup_packet(packet)
    assert 'pickup_policy_changed' in consumed['context_reasons']
    assert 'AUDIT_SENTINEL' not in json.dumps(consumed)
    assert packet == original
