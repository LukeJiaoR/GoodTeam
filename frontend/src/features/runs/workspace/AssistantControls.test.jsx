import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { en } from '../../../i18n/locales/en';
import AssistantControls from './AssistantControls';

const t = (key) => en[key] || key;
const runtime = { provider: 'codex', model: 'gpt-example', reasoning_depth: 'high', status: 'unverified' };
function mount(props = {}) {
  return render(<MemoryRouter><AssistantControls projectKey="DEMO" runtime={runtime} t={t} {...props} /></MemoryRouter>);
}

describe('Assistant recovery and confirmation', () => {
  it('keeps runtime configuration and connection checks out of the conversation', () => {
    const onAction = vi.fn();
    mount({ onAction });
    expect(screen.queryByRole('button', { name: 'Check connection' })).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Configure runtime' })).not.toBeInTheDocument();
    expect(screen.queryByText(/gpt-example/)).not.toBeInTheDocument();
    expect(onAction).not.toHaveBeenCalled();
  });

  it('retries the original request by identity and attempt, without resending text', () => {
    const onAction = vi.fn();
    mount({ onAction, requests: { m1: { text: 'Original', attempt: 2, status: 'failed', error: { code: 'model_unavailable' } } } });
    fireEvent.click(screen.getByRole('button', { name: 'Retry request' }));
    expect(onAction).toHaveBeenCalledWith({ action: 'retry_message', message_id: 'm1', expected_attempt: 2 });
    expect(screen.getByText('Original')).toBeInTheDocument();
  });

  it('shows the exact proposed command and does not confirm until clicked', () => {
    const onAction = vi.fn();
    mount({ onAction, pending: { id: 'p1', status: 'pending', reason: 'Disable QA', commands: [{ kind: 'disable_role', role: 'qa' }] } });
    expect(onAction).not.toHaveBeenCalled();
    expect(screen.getByText('qa')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Team change' }));
    expect(onAction).toHaveBeenCalledWith({ action: 'confirm_team_decision', proposal_id: 'p1' });
  });

  it('never offers retry for interrupted work or mutation controls for history', () => {
    const props = { requests: { m1: { text: 'Original', status: 'interrupted', error: { code: 'interrupted' } } } };
    const view = mount(props);
    expect(screen.queryByRole('button', { name: 'Retry request' })).not.toBeInTheDocument();
    view.unmount();
    mount({ ...props, readOnly: true });
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});
