import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ConversationComposer from './ConversationComposer';
import ConversationChangeContext from './ConversationChangeContext';

const t = (key) => key;
const target = { project_key: 'A', approval: 'rfc', run_id: 'r', target_type: 'review', target_id: 'rfc', artifact_key: 'rfc', artifact_version: 'v1' };
const saved = (text) => ({ intent_commit: { id: 'i', project_key: 'A', mission: { run_id: 'r' }, target, status: 'discussing', draft_text: text } });
describe('same-composer exact change handoff', () => {
  it('saves existing text without interpretation or confirmation and restores the durable draft', async () => {
    const text = '  Web only.\n\n    no_dependencies()\n';
    const begin = vi.fn().mockResolvedValue(saved(text));
    const send = vi.fn();
    const discuss = vi.fn();
    const props = { activeTask: true, changeTarget: target, onStartChangeIntent: begin, onStart: send, onDiscussChangeIntent: discuss, t };
    const { rerender } = render(<ConversationComposer {...props} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: text } });
    fireEvent.click(screen.getByRole('button', { name: 'work.changeContext.begin' }));
    await waitFor(() => expect(begin).toHaveBeenCalledWith('rfc', target, text));
    await waitFor(() => expect(screen.getByRole('textbox')).toHaveValue(''));
    expect(send).not.toHaveBeenCalled(); expect(discuss).not.toHaveBeenCalled();
    rerender(<ConversationComposer {...props} changeTarget={null} changeIntent={saved(text).intent_commit} />);
    expect(screen.getAllByRole('textbox')).toHaveLength(1);
    expect(screen.getByRole('textbox')).toHaveValue(text);
    expect(screen.getByText('work.changeContext.discussing.title')).toBeVisible();
  });
  it('preserves text on failed or unverified response and prevents double handoff', async () => {
    let finish;
    const begin = vi.fn(() => new Promise((resolve) => { finish = resolve; }));
    render(<ConversationComposer t={t} activeTask changeTarget={target} onStartChangeIntent={begin} onStart={vi.fn()} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Keep this requirement.' } });
    const button = screen.getByRole('button', { name: 'work.changeContext.begin' });
    fireEvent.click(button); fireEvent.click(button);
    expect(begin).toHaveBeenCalledTimes(1);
    finish({});
    await waitFor(() => expect(screen.getByText('work.changeContext.unconfirmed')).toBeVisible());
    expect(screen.getByRole('textbox')).toHaveValue('Keep this requirement.');
  });
  it('makes no promise of queuing or hot-steering when no exact review gate exists', () => {
    const begin = vi.fn();
    render(<ConversationChangeContext active t={t} target={null} onBegin={begin} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.getByText('work.changeContext.noBoundary')).toBeVisible();
    expect(begin).not.toHaveBeenCalled();
  });
  it.each(['discussing', 'ready_to_confirm', 'committing', 'ambiguous', 'stale', 'committed', 'cancelled'])('projects %s from durable state without presenting an execution action', (status) => {
    render(<ConversationChangeContext active t={t} intent={{ id: 'i', status }} target={target} onBegin={vi.fn()} />);
    expect(screen.getByText(`work.changeContext.${status}.title`)).toBeVisible();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
  it('shows the recorded decision without claiming the changed result passed', () => {
    render(<ConversationChangeContext active t={t} target={target}
      recordedChange={{ event: 'change_request_committed', details: { intent_status: 'committed' } }} />);
    expect(screen.getByText('work.changeContext.committed.body')).toBeVisible();
    expect(screen.getByRole('button', { name: 'work.changeContext.begin' })).toBeVisible();
  });

});
