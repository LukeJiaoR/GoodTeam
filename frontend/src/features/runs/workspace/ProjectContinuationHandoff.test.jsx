import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { translate } from '../../../i18n';
import { normalizeProjectAttention, attentionHref, readAttentionSelection, resolveAttentionFocus } from '../../projects/projectAttention';
import { TimelineItem } from './ConversationTimeline';
import ProjectContinuationControls from './ProjectContinuationControls';

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
  view.rerender(<ol><TimelineItem item={item} language="en" t={(key, params) => translate('en', key, params)}
    continuationNoticeId="continuation:new:notice" /></ol>);
  expect(screen.queryByRole('button', { name: 'View iteration and actions' })).not.toBeInTheDocument();
  vi.unstubAllGlobals();
});
