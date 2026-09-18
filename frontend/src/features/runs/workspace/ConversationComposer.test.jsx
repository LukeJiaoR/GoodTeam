import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ConversationComposer from './ConversationComposer';

const t = (key) => key;
const message = 'work.taskAssistant.message';
const change = 'work.manager.changeReason';
const submitChange = 'work.manager.intent.reviewInterpretation';

// All tests exercise the same textarea, not a separate Request changes form.
describe('persistent conversation composer', () => {
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

  it('recovers a seeded change draft without creating another input', () => {
    render(<ConversationComposer t={t} onStart={vi.fn()}
      changeIntent={{ id: 'intent-1', status: 'discussing', draft_text: 'Saved before reload.', interpretation: null }} />);
    expect(screen.getAllByRole('textbox')).toHaveLength(1);
    expect(screen.getByRole('textbox', { name: change })).toHaveValue('Saved before reload.');
  });
});
