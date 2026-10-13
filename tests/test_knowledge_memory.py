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
    assert rebuilt['retrieval_policy'] == 'knowledge_without_execution_v1'
    assert 'AUDIT_LOG' not in rebuilt['horizons']['short-term']['summary']
    assert store.event_path('qa').read_bytes() == wal
