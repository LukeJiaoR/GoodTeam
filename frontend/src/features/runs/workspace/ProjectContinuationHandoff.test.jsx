import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { translate } from '../../../i18n';
import { normalizeProjectAttention, attentionHref, readAttentionSelection, resolveAttentionFocus } from '../../projects/projectAttention';
import { TimelineItem } from './ConversationTimeline';
import ProjectContinuationControls from './ProjectContinuationControls';
import ProjectConversation from './ProjectConversation';
import { readingScope, saveReadingPosition } from './conversationReading';

it('offers a current bilingual Manager handoff through existing controls without starting work', () => {
  vi.stubGlobal('requestAnimationFrame', callback => callback());
  const notice = { id: 'continuation:grant:notice', continuation_id: 'grant', phase: 'no_followup',
    reason: 'discovery_no_followup', kind: 'needs_input', delivered_task_ids: ['APP-1'],
    delivery_receipt_ids: ['sha256:receipt'], rationale: 'More project context is needed.',
    batches_started: 1, max_batches: 2, manager_calls: 2, max_manager_calls: 2 };
  const value = { id: 'grant', status: 'completed', reason: notice.reason,
    batches_started: 1, max_batches: 2, manager_calls: 2, max_manager_calls: 2 };
  const session = { project_key: 'APP', project_continuation: value, project_continuation_notice: notice };
  const item = { id: notice.id, actor: 'task_assistant', event: 'project_continuation_handoff',
    content: 'English durable source content.', details: notice };
  const onAction = vi.fn();
  const ui = language => <div className="product-work-page">
    <ProjectContinuationControls snapshot={{ candidates: [] }} session={session}
      projectKey="APP" onAction={onAction} canStart language={language} />
    <ol><TimelineItem item={item} language={language} t={(key, params) => translate(language, key, params)}
      continuationNoticeId={notice.id} /></ol>
  </div>;
  const view = render(ui('zh-CN'));
  expect(screen.getByText(/这不代表项目已经完成/)).toBeInTheDocument();
  expect(screen.getByText(/Manager 判断依据：More project context is needed/)).toBeInTheDocument();
  expect(screen.queryByText(item.content)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '查看迭代与处理操作' }));
  expect(screen.getByRole('region', { name: '项目自动迭代' })).toHaveFocus();
  expect(onAction).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '保留结果并结束' }));
  expect(onAction).toHaveBeenCalledExactlyOnceWith({ action: 'stop_project_continuation', continuation_id: 'grant' });

  const attention = { id: notice.id, kind: notice.kind, source: 'continuation', run_id: '',
    target_id: 'grant', title: 'Project iteration needs attention', reason: notice.reason };
  const projection = normalizeProjectAttention({ schema_version: 'project_attention.v1', items: [attention],
    counts: { blocked: 0, approval: 0, needs_input: 1, total: 1 } });
  expect(projection.items).toEqual([attention]);
  const selection = readAttentionSelection(new URL(attentionHref('APP', attention), 'https://local.test').searchParams);
  const data = { task_assistant_session: session,
    team_workspace: { schema_version: 'team_workspace.v1', mission: null } };
  const resolve = () => resolveAttentionFocus({ selection, projectKey: 'APP', data,
    projects: [{ project: { project_key: 'APP' }, attention: projection }] });
  expect(resolve()).toMatchObject({ status: 'current', target: 'continuation' });
  view.rerender(ui('en'));
  expect(screen.getByText(/This does not mean the project is complete/)).toBeInTheDocument();
  // A resolved or replaced grant cannot offer an action through an old message/link.
  data.task_assistant_session = { ...session, project_continuation_notice: null };
  expect(resolve()).toEqual({ status: 'changed' });
  // Stopping future dispatch cannot finish an unresolved owned batch.
  view.rerender(<ProjectContinuationControls snapshot={{ candidates: [] }} projectKey="APP" onAction={onAction}
    session={{ project_continuation: { ...value, status: 'blocked', pending_batch: { run_id: 'batch' } },
      project_continuation_notice: { ...notice, phase: 'blocked', kind: 'blocked' },
      project_task_batches: { batch: { run_id: 'batch', status: 'blocked', task_ids: ['APP-2'],
        recovery: { version: 'recovery-v1', can_abandon: true } } } }} />);
  expect(screen.queryByRole('button', { name: 'Keep results and finish' })).not.toBeInTheDocument();
  const abandon = screen.getByRole('button', { name: 'Abandon this batch' });
  expect(abandon).toBeDisabled();
  fireEvent.click(screen.getByLabelText(/I confirm abandoning this batch/));
  fireEvent.click(abandon);
  expect(onAction.mock.lastCall[0]).toMatchObject({ action: 'abandon_project_batch', run_id: 'batch',
    expected_recovery_version: 'recovery-v1', confirmed: true });
  view.rerender(<ol><TimelineItem item={item} language="en" t={(key, params) => translate('en', key, params)}
    continuationNoticeId="continuation:new:notice" /></ol>);
  expect(screen.queryByRole('button', { name: 'View iteration and actions' })).not.toBeInTheDocument();
  // A retained handoff must not divert the paused reader from current approval.
  saveReadingPosition({ scope: readingScope('APP'), following: false,
    anchor: { id: item.id, offset: 0 }, seenId: item.id, attentionKey: '' });
  const conversation = attention => <div className="product-work-page">
    <ProjectConversation projectKey="APP" iterationSession={session} iterationBacklog={{ candidates: [] }}
      timeline={[item]} assistantState={{}} attention={attention} language="en"
      t={(key, params) => translate('en', key, params)} onAssistantAction={onAction} />
  </div>;
  view.rerender(conversation({ kind: 'approval', id: 'current-approval' }));
  const pendingLabel = translate('en', 'assistant.project.viewPending');
  fireEvent.click(screen.getByRole('button', { name: pendingLabel }));
  expect(screen.getByRole('region', { name: pendingLabel })).toHaveFocus();
  view.rerender(conversation(null));
  fireEvent.click(screen.getByRole('button', { name: pendingLabel }));
  expect(screen.getByRole('region', { name: 'Automatic project iteration' })).toHaveFocus();
  localStorage.clear();
  vi.unstubAllGlobals();
});
