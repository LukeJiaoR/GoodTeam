import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { translate } from '../../i18n';
import { TimelineItem } from '../../features/runs/workspace/ConversationTimeline';

it('offers a side-panel source only for retained reports, without request or identity envelopes', () => {
  const t = (key, params) => translate('en', key, params);
  const message = { actor: 'task_assistant', event: 'conversation_replied', content: 'The implementation is ready for review.',
    details: { manager_execution: { request: { prompt: 'internal prompt' }, job_id: 'internal-job' } } };
  const view = render(<ol><TimelineItem item={message} language="en" t={t} /></ol>);
  expect(screen.queryByRole('button', { name: 'View source evidence' })).not.toBeInTheDocument();
  const summary = 'Added keyboard navigation to the project list. The focused navigation check passed.';
  const source = { worker_results: [{ project_task_id: 'APP-1', summary, job_id: 'internal-worker' }],
    continuation: { request: { prompt: 'internal prompt' } } };
  view.rerender(<ol><TimelineItem item={{ ...message, details: { ...message.details, source_evidence: source } }} language="en" t={t} /></ol>);
  expect(view.container).not.toHaveTextContent(summary);
  const open = screen.getByRole('button', { name: 'View source evidence' });
  open.focus();
  fireEvent.click(open);
  const panel = screen.getByRole('dialog', { name: 'Source evidence' });
  expect(panel).toHaveTextContent(summary);
  expect(panel).not.toHaveTextContent('internal prompt');
  expect(panel).not.toHaveTextContent('internal-job');
  expect(panel).not.toHaveTextContent('internal-worker');
  expect(panel.querySelector('pre')).toBeNull();
  expect(view.container).not.toContainElement(panel);
  fireEvent.click(screen.getByRole('button', { name: 'Close evidence' }));
  expect(open).toHaveFocus();
});

it('reports partial inspection findings and unfinished work in the conversation before any source is opened', () => {
  const t = (key, params) => translate('zh-CN', key, params);
  const item = { actor: 'task_assistant', event: 'project_understanding_incomplete',
    content: '技术栈 / Stack: React, Python\n入口 / Entry points: frontend/src/main.jsx, src/server.py\n命令 / Commands: npm run build\n风险 / Risks: Repository scan reached the bounded entry limit; deeper indexing is recommended.',
    details: { inventory_scan: { complete: false, files_seen: 500, directories_seen: 30, snapshot_omitted_files: 2 } } };
  const view = render(<ol><TimelineItem item={item} language="zh-CN" t={t} /></ol>);
  expect(view.container).toHaveTextContent('React, Python');
  expect(view.container).toHaveTextContent(t('assistant.understanding.discovered', { entries: 2, commands: 1 }));
  expect(view.container).toHaveTextContent(t('assistant.understanding.limit.entries'));
  expect(view.container).toHaveTextContent(t('assistant.understanding.next.incomplete'));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(view.container).not.toHaveTextContent('npm run build');
  fireEvent.click(screen.getByRole('button', { name: t('assistant.understanding.sources') }));
  expect(screen.getByRole('dialog')).toHaveTextContent('npm run build');
  expect(screen.getByRole('dialog')).not.toHaveTextContent('inventory_scan');
  fireEvent.click(screen.getByRole('button', { name: t('product.evidence.close') }));
  view.rerender(<ol><TimelineItem item={{ actor: 'task_assistant', event: 'project_iteration_choice_recorded',
    content: '建议先补齐导航验证，再继续下一轮。' }} language="zh-CN" t={t} /></ol>);
  expect(view.container).toHaveTextContent('建议先补齐导航验证，再继续下一轮。');
});
