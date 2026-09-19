import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ConversationComposer from './ConversationComposer';
import TeamWorkspace from './TeamWorkspace';
import { MemoryRouter } from 'react-router-dom';

function Router({ children }) { return <MemoryRouter>{children}</MemoryRouter>; }

const t = (key) => key;
const data = { state: { run_id: 'r1' }, team_workspace: {
  mission: { source: 'task_assistant', status: 'ready' }, manager_timeline: [],
  execution_actors: [], outcome_tasks: [], capabilities: [],
} };

describe('incremental chat feedback on the existing composer', () => {
  it('uses Enter once and leaves Shift+Enter and composition alone', async () => {
    const onStart = vi.fn().mockResolvedValue({});
    render(<ConversationComposer t={t} onStart={onStart} />);
    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: 'Exact text\nincluding newlines.' } });
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true });
    fireEvent.keyDown(input, { key: 'Enter', keyCode: 229 });
    expect(onStart).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(onStart).toHaveBeenCalledTimes(1));
    expect(onStart).toHaveBeenCalledWith('Exact text\nincluding newlines.');
  });

  it('shows feedback in the timeline and reconciles the recorded message by ID', () => {
    const delivery = { id: 'm1', projectKey: 'DEMO', runId: 'r1', text: 'A request in flight', status: 'sending' };
    const props = { data, t, language: 'en', projectKey: 'DEMO', conversationDelivery: delivery };
    const view = render(<TeamWorkspace {...props} />, { wrapper: Router });
    expect(screen.getByText(delivery.text)).toBeVisible();
    expect(screen.getByText('Sending…')).toBeVisible();
    const next = { ...data, team_workspace: { ...data.team_workspace,
      manager_timeline: [{ id: 'm1', actor: 'user', event: 'mission_requested', content: delivery.text }] } };
    view.rerender(<TeamWorkspace {...props} data={next} />);
    expect(screen.getAllByText(delivery.text)).toHaveLength(1);
    view.rerender(<TeamWorkspace {...props} data={next} conversationDelivery={{ ...delivery, status: 'unconfirmed' }} />);
    expect(screen.queryByText('Sending…')).not.toBeInTheDocument();
    expect(screen.getByText(/Request not confirmed/)).toBeVisible();
    view.rerender(<TeamWorkspace {...props} projectKey="OTHER" />);
    expect(screen.queryByText(delivery.text)).not.toBeInTheDocument();
  });

  it('keeps actual task and team proposal replies visible', () => {
    const timeline = ['task_proposed', 'team_decision_proposed'].map((event, i) => ({
      id: `reply${i}`, actor: 'task_assistant', event, content: `Exact assistant explanation ${i}`,
    }));
    const translate = (key) => key.startsWith('work.manager.events.') ? 'Summary label' : key;
    render(<TeamWorkspace data={{ ...data, team_workspace: { ...data.team_workspace, manager_timeline: timeline } }}
      projectKey="DEMO" language="en" t={translate} />, { wrapper: Router });
    for (const item of timeline) expect(screen.getByText(item.content)).toBeVisible();
  });
});
