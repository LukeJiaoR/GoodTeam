import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { TimelineItem } from './ConversationTimeline';
import { translate } from '../../../i18n';

describe('ConversationTimeline', () => {
  const t = (key, params) => translate('en', key, params);

  it('renders a compact system chip when an event has no extra details', () => {
    const item = {
      id: 'event-1',
      actor: 'system',
      event: 'progress',
      created_at: '2026-11-10T01:00:00Z',
    };
    const { container } = render(
      <ol>
        <TimelineItem item={item} language="en" t={t} />
      </ol>,
    );

    expect(screen.getByText('The team state changed.')).toBeInTheDocument();
    expect(container.querySelector('.team-system-card')).toBeNull();
    expect(container.querySelector('.team-manager-system-chip')).not.toBeNull();
  });

  it('renders a collapsible system card with details and tags when structured metadata is present', () => {
    const item = {
      id: 'event-2',
      actor: 'manager',
      event: 'work_dispatched',
      created_at: '2026-11-10T01:05:00Z',
      details: {
        phase: 'executing',
        transition: 'worker_job_created',
        pr_number: 42,
        task_ids: ['TASK-1', 'TASK-2'],
      },
      refs: ['run-abc'],
      content: 'Worker runner dispatched command: test --unit',
    };
    const { container } = render(
      <ol>
        <TimelineItem item={item} language="en" t={t} />
      </ol>,
    );

    const card = container.querySelector('.team-system-card');
    expect(card).not.toBeNull();
    expect(container.querySelector('.team-system-tag')).toHaveTextContent('#42');
    expect(container.querySelector('.team-system-card__params')).toHaveTextContent('executing');
    expect(container.querySelector('.team-system-card__params')).toHaveTextContent('worker_job_created');
    expect(container.querySelector('.team-system-card__params')).toHaveTextContent('run-abc');
    expect(container.querySelector('.team-system-card__output')).toHaveTextContent('Worker runner dispatched command: test --unit');
  });

  it('renders user messages with markdown and correct metadata flow', () => {
    const item = {
      id: 'user-1',
      actor: 'user',
      content: 'Please **review** the PR `#42` and verify `vitest` passes.',
      created_at: '2026-11-10T01:10:00Z',
    };
    const { container } = render(
      <ol>
        <TimelineItem item={item} language="en" t={t} />
      </ol>,
    );

    expect(screen.getByText('review')).toBeInTheDocument();
    expect(screen.getByText('vitest')).toBeInTheDocument();
    expect(container.querySelector('.conversation-message-time')).not.toBeNull();
  });
});
