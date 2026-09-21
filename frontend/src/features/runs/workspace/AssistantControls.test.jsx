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
  it('requires cost consent and submits a read-only runtime check', () => {
    const onAction = vi.fn();
    mount({ onAction });
    const check = screen.getByRole('button', { name: 'Check connection' });
    expect(check).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(check);
    expect(onAction).toHaveBeenCalledWith(expect.objectContaining({ action: 'check_runtime', confirmed: true }));
    expect(screen.getByRole('link', { name: 'Configure runtime' })).toHaveAttribute('href', '/projects/DEMO/team');
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
    mount({ onAction, pending: { id: 'p1', status: 'pending', expected_team_revision: 1,
      preview: { schema_version: 'team_decision_preview.v1', team_revision: 1, changes: [] },
      reason: 'Disable QA', commands: [{ kind: 'disable_role', role: 'qa' }] } });
    expect(onAction).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Team change' }));
    expect(onAction).toHaveBeenCalledWith({ action: 'confirm_team_decision', proposal_id: 'p1' });
  });

  it.each([null, { schema_version: 'team_decision_preview.v1', team_revision: 2, changes: [] }])('cannot confirm without a preview matching the proposed revision', (preview) => {
      const onAction = vi.fn();
      mount({ onAction, pending: { id: 'p1', status: 'pending', expected_team_revision: 1, preview } });
      fireEvent.click(screen.getByRole('button', { name: 'Confirm Team change' }));
      expect(onAction).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole('button', { name: 'Dismiss proposal' }));
      expect(onAction).toHaveBeenCalledWith({ action: 'dismiss_team_decision', proposal_id: 'p1' });
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
