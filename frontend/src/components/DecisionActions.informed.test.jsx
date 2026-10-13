import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import DecisionActions from './DecisionActions';

const t = key => key;
const reason = () => screen.getByRole('textbox');
const continueButton = () => screen.getByRole('button', { name: /work.informedApproval.continue/ });
const enter = text => fireEvent.change(reason(), { target: { value: text } });
const base = () => ({ t, informedApproval: true, decisionContextKey: 'A:run-1:prd:v1:missing', onDecision: vi.fn().mockResolvedValue({}) });

describe('informed Review decision contract', () => {
  it('prevents blank or whitespace mouse and keyboard approval', () => {
    const props = base(); render(<DecisionActions {...props} />);
    expect(continueButton()).toBeDisabled();
    enter('  \n ');
    fireEvent.click(continueButton());
    fireEvent.keyDown(reason(), { key: 'Enter', ctrlKey: true });
    fireEvent.keyDown(reason(), { key: 'Enter', metaKey: true });
    expect(props.onDecision).not.toHaveBeenCalled();
  });

  it('does not submit while an IME composition is committing Enter', () => {
    const props = base(); render(<DecisionActions {...props} />);
    enter('Composed reason.');
    fireEvent.keyDown(reason(), { key: 'Enter', ctrlKey: true, isComposing: true });
    expect(props.onDecision).not.toHaveBeenCalled();
  });

  it('passes the operator reason without silently replacing it with a canned approval', async () => {
    const props = base(); render(<DecisionActions {...props} />);
    const note = '  I checked this planning document manually.\nNot a verified reviewer pass.  ';
    enter(note);
    fireEvent.click(continueButton());
    await waitFor(() => expect(props.onDecision).toHaveBeenCalledWith('approved', note));
    expect(props.onDecision).toHaveBeenCalledTimes(1);
  });

  it('uses one synchronous lock across mouse, keyboard and other decisions', async () => {
    let finish;
    const pending = new Promise(resolve => { finish = resolve; });
    const props = base(); props.onDecision.mockImplementation(() => pending);
    render(<DecisionActions {...props} />);
    enter('Checked the gap.');
    fireEvent.keyDown(reason(), { key: 'Enter', ctrlKey: true });
    fireEvent.keyDown(reason(), { key: 'Enter', metaKey: true });
    fireEvent.click(continueButton());
    fireEvent.click(screen.getByRole('button', { name: 'review.decision.reject' }));
    expect(props.onDecision).toHaveBeenCalledTimes(1);
    await act(async () => { finish({}); await pending; });
  });

  it('retains the reason after failure and allows an explicit retry', async () => {
    const props = base(); props.onDecision.mockRejectedValueOnce(new Error('conflict'));
    render(<DecisionActions {...props} />);
    enter('Checked the current limitation.');
    fireEvent.click(continueButton());
    await screen.findByRole('alert');
    expect(reason()).toHaveValue('Checked the current limitation.');
    fireEvent.click(continueButton());
    await waitFor(() => expect(props.onDecision).toHaveBeenCalledTimes(2));
  });

  it('retains the reason on an unchanged poll but clears it for changed review context', () => {
    const props = base(); const view = render(<DecisionActions {...props} />);
    enter('Old version reason.');
    view.rerender(<DecisionActions {...props} />);
    expect(reason()).toHaveValue('Old version reason.');
    view.rerender(<DecisionActions {...props} decisionContextKey="A:run-1:prd:v2:missing" />);
    expect(reason()).toHaveValue('');
    expect(continueButton()).toBeDisabled();
  });

  it('cannot override the existing disabled-approval gate or another pending mutation', () => {
    const props = base(); const view = render(<DecisionActions {...props} />);
    enter('Checked.');
    for (const override of [{ approveDisabled: true }, { busy: 'save' }]) {
      view.rerender(<DecisionActions {...props} {...override} />);
      fireEvent.keyDown(reason(), { key: 'Enter', ctrlKey: true });
      fireEvent.click(continueButton());
    }
    expect(props.onDecision).not.toHaveBeenCalled();
  });

  it('keeps normal positive-evidence approval and unmodified change feedback', async () => {
    const props = base(); props.informedApproval = false;
    const onRequestChanges = vi.fn().mockResolvedValue({});
    render(<DecisionActions {...props} onRequestChanges={onRequestChanges} />);
    fireEvent.click(screen.getByRole('button', { name: /review.decision.approve/ }));
    await waitFor(() => expect(props.onDecision).toHaveBeenCalledWith('approved', ''));
    await waitFor(() => expect(reason()).toBeEnabled());
    const note = '  Keep indentation.\n\n    original()\n';
    enter(note);
    fireEvent.click(screen.getByRole('button', { name: 'review.decision.changes' }));
    await waitFor(() => expect(onRequestChanges).toHaveBeenCalledWith(note));
  });

  it('preserves Request changes with an empty draft while manual approval is disabled', async () => {
    const props = base(); const onRequestChanges = vi.fn().mockResolvedValue({});
    render(<DecisionActions {...props} onRequestChanges={onRequestChanges} />);
    fireEvent.click(screen.getByRole('button', { name: 'review.decision.changes' }));
    await waitFor(() => expect(onRequestChanges).toHaveBeenCalledWith(''));
    expect(props.onDecision).not.toHaveBeenCalled();
  });
});
