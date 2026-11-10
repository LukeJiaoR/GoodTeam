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

  it('renders project understanding reply without repeating full inventory markdown', () => {
    const rawInventory = 'Stack: React, Python\nEntry points: frontend/src/main.jsx, src/server.py\nCommands: npm run build\nRisks: Repository scan reached the bounded entry limit; deeper indexing is recommended.';
    const item = {
      id: 'understanding-1',
      actor: 'task_assistant',
      event: 'project_understanding_ready',
      content: rawInventory,
      details: {
        inventory_scan: { complete: true, files_seen: 200, directories_seen: 15 },
      },
      created_at: '2026-11-10T01:12:00Z',
    };
    const { container } = render(
      <ol>
        <TimelineItem item={item} language="en" t={t} />
      </ol>,
    );

    // Structured ProjectUnderstandingReply component is rendered
    expect(container.querySelector('.project-understanding-reply')).not.toBeNull();
    // Raw inventory text is NOT duplicated outside the component in a secondary MarkdownView
    const rawMarkdownBlocks = container.querySelectorAll('.team-manager-event__bubble > .md-renderer');
    expect(rawMarkdownBlocks.length).toBe(1); // Only the intro copy
    expect(container.querySelector('.team-manager-event__note')).toBeNull();
  });

  it('keeps full system event summary and budget limits accessible in card', () => {
    const item = {
      id: 'event-continuation',
      actor: 'system',
      event: 'project_continuation_authorized',
      details: {
        task_ids: ['APP-101', 'APP-102', 'APP-103'],
        max_batches: 5,
        max_manager_calls: 10,
      },
      created_at: '2026-11-10T01:15:00Z',
    };
    const { container } = render(
      <ol>
        <TimelineItem item={item} language="en" t={t} />
      </ol>,
    );

    const card = container.querySelector('.team-system-card');
    expect(card).not.toBeNull();

    // The chip text has the full summary in title attribute
    const chipText = container.querySelector('.team-manager-system-chip__text');
    expect(chipText).toHaveAttribute('title');
    expect(chipText.getAttribute('title')).toContain('APP-101, APP-102, APP-103');
    expect(chipText.getAttribute('title')).toContain('5 batches');
    expect(chipText.getAttribute('title')).toContain('10 Manager calls');

    // The expanded card body renders the complete summary text and budget parameters
    expect(container.querySelector('.team-system-card__summary-text')).toHaveTextContent('APP-101, APP-102, APP-103');
    expect(container.querySelector('.team-system-card__params')).toHaveTextContent('5');
    expect(container.querySelector('.team-system-card__params')).toHaveTextContent('10');
  });
});
