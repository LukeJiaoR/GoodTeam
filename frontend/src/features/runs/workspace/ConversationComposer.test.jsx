import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ConversationComposer from './ConversationComposer';

const t = (key) => key;
const message = 'work.taskAssistant.message';
const change = 'work.manager.changeReason';
const submitChange = 'work.manager.intent.reviewInterpretation';

// All tests exercise the same textarea, not a separate Request changes form.
describe('persistent conversation composer', () => {
  it('saves a separate future task while retaining the ordinary unsent question', async () => {
    const onStart = vi.fn();
    const onQueueTask = vi.fn().mockResolvedValue({ session: {} });
    const onDiscussChangeIntent = vi.fn();
    render(<ConversationComposer t={t} onStart={onStart} onQueueTask={onQueueTask}
      activeTask onDiscussChangeIntent={onDiscussChangeIntent} />);
    fireEvent.change(screen.getByRole('textbox', { name: message }), { target: { value: 'Why is the current task blocked?' } });
    fireEvent.click(screen.getByRole('button', { name: 'assistant.project.newTask' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'assistant.project.newTask' }), { target: { value: 'An independent task.\n\nKeep the schema.' } });
    fireEvent.click(screen.getByRole('button', { name: 'assistant.project.saveTask' }));
    await waitFor(() => expect(onQueueTask).toHaveBeenCalledWith('An independent task.\n\nKeep the schema.'));
    expect(onStart).not.toHaveBeenCalled();
    expect(onDiscussChangeIntent).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: message }));
    expect(screen.getByRole('textbox', { name: message })).toHaveValue('Why is the current task blocked?');
  });

  it('retains a future task after a rejected save', async () => {
    render(<ConversationComposer t={t} onStart={vi.fn()} onQueueTask={vi.fn().mockResolvedValue(false)} />);
    fireEvent.click(screen.getByRole('button', { name: 'assistant.project.newTask' }));
    const draft = screen.getByRole('textbox', { name: 'assistant.project.newTask' });
    fireEvent.change(draft, { target: { value: 'Keep this future request.' } });
    fireEvent.click(screen.getByRole('button', { name: 'assistant.project.saveTask' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'assistant.project.saveTask' })).not.toBeDisabled());
    expect(draft).toHaveValue('Keep this future request.');
  });

  it('switches to bound change discussion and retains the ordinary unsent draft', async () => {
    const onStart = vi.fn().mockResolvedValue({});
    const onDiscussChangeIntent = vi.fn().mockResolvedValue({});
    const props = { t, onStart, onDiscussChangeIntent };
    const { rerender } = render(<ConversationComposer {...props} />);
    fireEvent.change(screen.getByRole('textbox', { name: message }), { target: { value: 'A question in progress' } });
    const intent = { id: 'intent-1', status: 'discussing', draft_text: '' };
    rerender(<ConversationComposer {...props} changeIntent={intent} />);
    expect(screen.getAllByRole('textbox')).toHaveLength(1);
    fireEvent.change(screen.getByRole('textbox', { name: change }), { target: { value: 'Keep the schema unchanged.' } });
    fireEvent.click(screen.getByRole('button', { name: submitChange }));
    await waitFor(() => expect(onDiscussChangeIntent).toHaveBeenCalledWith('intent-1', 'Keep the schema unchanged.'));
    expect(onStart).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: message }));
    expect(screen.getByRole('textbox', { name: message })).toHaveValue('A question in progress');
  });

  it('allows a question without changing a pending interpretation', async () => {
    const onStart = vi.fn().mockResolvedValue({});
    const onDiscussChangeIntent = vi.fn();
    const onKeepDiscussingChangeIntent = vi.fn();
    render(<ConversationComposer t={t} onStart={onStart}
      changeIntent={{ id: 'intent-1', status: 'ready_to_confirm', draft_text: 'Ship Google only.', interpretation: { items: [] } }}
      onDiscussChangeIntent={onDiscussChangeIntent} onKeepDiscussingChangeIntent={onKeepDiscussingChangeIntent} />);
    fireEvent.click(screen.getByRole('button', { name: message }));
    fireEvent.change(screen.getByRole('textbox', { name: message }), { target: { value: 'Why this scope?' } });
    fireEvent.click(screen.getByRole('button', { name: 'work.taskAssistant.send' }));
    await waitFor(() => expect(onStart).toHaveBeenCalledWith('Why this scope?'));
    expect(onDiscussChangeIntent).not.toHaveBeenCalled();
    expect(onKeepDiscussingChangeIntent).not.toHaveBeenCalled();
  });

  it('preserves failed input and does not submit twice', async () => {
    let reject;
    const onStart = vi.fn(() => new Promise((_resolve, rejectPromise) => { reject = rejectPromise; }));
    render(<ConversationComposer t={t} onStart={onStart} />);
    const input = screen.getByRole('textbox', { name: message });
    fireEvent.change(input, { target: { value: 'Do not lose this request.' } });
    fireEvent.submit(input.closest('form'));
    fireEvent.submit(input.closest('form'));
    expect(onStart).toHaveBeenCalledTimes(1);
    reject(new Error('offline'));
    await waitFor(() => expect(screen.getByRole('button', { name: 'work.taskAssistant.send' })).not.toBeDisabled());
    expect(input).toHaveValue('Do not lose this request.');
  });

  it('restores an interpreted discussion and follows durable updates before local editing', async () => {
    const original = '  Preserve formatting.  \n\n    keep_indentation()\n';
    const onStart = vi.fn();
    const onDiscussChangeIntent = vi.fn().mockResolvedValue({});
    const props = { t, onStart, onDiscussChangeIntent };
    const intent = {
      id: 'restored-intent', status: 'discussing', draft_text: original,
      interpretation: { summary: 'Previous interpretation', items: [] },
    };
    const { rerender } = render(<ConversationComposer {...props} changeIntent={intent} />);
    expect(screen.getByRole('textbox', { name: change })).toHaveValue(original);
    const updated = `${original}\nKeep the provider boundary.`;
    rerender(<ConversationComposer {...props} changeIntent={{ ...intent, draft_text: updated }} />);
    expect(screen.getAllByRole('textbox')).toHaveLength(1);
    expect(screen.getByRole('textbox', { name: change })).toHaveValue(updated);
    fireEvent.click(screen.getByRole('button', { name: submitChange }));
    await waitFor(() => expect(onDiscussChangeIntent).toHaveBeenCalledWith('restored-intent', updated));
    expect(onDiscussChangeIntent).toHaveBeenCalledTimes(1);
    expect(onStart).not.toHaveBeenCalled();
  });
});
