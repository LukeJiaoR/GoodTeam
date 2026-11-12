"""Committed waiting decisions reconcile their own lost acknowledgement only."""
import pytest

from goodteam_os import project_outcome_discovery as discovery
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from test_delivery_backlog_judgment import test_delivery_does_not_complete_remaining_backlog_or_hide_changed_facts as scenario


@pytest.mark.parametrize('followup', [False, True])
def test_committed_wait_ack_loss_preserves_active_loop(tmp_path, monkeypatch, followup):
    original = TaskAssistantSessionStore._mutate
    lost = []
    def lose_ack(self, change):
        result = original(self, change)
        value = result.get('project_continuation') or {}
        if not lost and value.get('reason') == 'backlog_judgment_waiting' and value.get('discovery_pending') is None:
            lost.append(True)
            raise OSError('fixture: committed wait acknowledgement lost')
        return result
    monkeypatch.setattr(TaskAssistantSessionStore, '_mutate', lose_ack)
    scenario(tmp_path, monkeypatch, False, followup)
    assert lost == [True]


def test_old_wait_does_not_hide_an_uncommitted_new_attempt(tmp_path, monkeypatch):
    store = TaskAssistantSessionStore(tmp_path, 'DEMO')
    old = {'id': 'grant', 'status': 'active', 'reason': 'backlog_judgment_waiting',
           'authorization_digest': 'original', 'discovery_pending': None,
           'discovery_last': {'status': 'no_followup', 'packet_digest': 'old-packet', 'trigger_ref': 'old-trigger'},
           'discovery_consumed': ['old-trigger']}
    store._mutate(lambda s: {**s, 'project_continuation': old})
    def unavailable(**kwargs):
        raise OSError('fixture: failure before any new result was committed')
    monkeypatch.setattr(discovery, '_discover_next_iteration', unavailable)
    with pytest.raises(OSError, match='before any new result'):
        discovery.discover_next_iteration(workspace=tmp_path, project_key='DEMO', root=tmp_path,
            project_config=tmp_path / 'unused.yaml', value=old)
    assert store.read()['project_continuation'] == old


def test_uncommitted_finish_keeps_new_pending_not_an_old_wait(tmp_path, monkeypatch):
    original = TaskAssistantSessionStore._mutate
    finishes = []
    captured = []
    def fail_before_second_finish(self, change):
        if change.__name__ == 'finished':
            finishes.append(True)
            if len(finishes) == 2:
                captured.append(self.read()['project_continuation'])
                raise OSError('fixture: new null decision not committed')
        return original(self, change)
    monkeypatch.setattr(TaskAssistantSessionStore, '_mutate', fail_before_second_finish)
    scenario(tmp_path, monkeypatch, False, True)
    assert len(captured) == 1
    pending = captured[0]['discovery_pending']
    assert pending['packet_digest'] != captured[0]['discovery_last']['packet_digest']
    assert pending['trigger_ref'] not in captured[0]['discovery_consumed']
    # Its reserved selection is replayed on the scenario's next tick, not a new model call.
    assert pending['selection']['new_task'] is None
