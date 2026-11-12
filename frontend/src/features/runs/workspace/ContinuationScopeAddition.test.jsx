import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ContinuationScopeAddition from './ContinuationScopeAddition';

describe('explicit scope addition', () => {
  it('keeps selection unapproved until confirmation and invalidates it when displayed scope changes', () => {
    const onAction = vi.fn();
    const value = { id: 'grant', task_ids: ['A'], batches_started: 1, max_batches: 3, manager_calls: 1, max_manager_calls: 3 };
    const snapshot = { version: 'v1', candidates: [{ task_id: 'B', title: 'Later B', status: 'todo', role: 'implementation',
      execution_policy: 'workspace_write', description: 'Document B', write_domains: ['docs'], acceptance_criteria: ['Reviewed B'] }] };
    const props = { value, snapshot, onAction, zh: false };
    const { rerender } = render(<ContinuationScopeAddition {...props} />);
    const button = screen.getByRole('button', { name: 'Confirm additions to current grant' });
    expect(button.disabled).toBe(true);
    fireEvent.click(screen.getAllByRole('checkbox')[0]);
    expect(onAction).not.toHaveBeenCalled();
    expect(button.disabled).toBe(true);
    fireEvent.click(screen.getAllByRole('checkbox')[1]);
    fireEvent.click(button);
    expect(onAction).toHaveBeenCalledWith(expect.objectContaining({ action: 'add_project_continuation_tasks',
      continuation_id: 'grant', task_ids: ['B'], expected_backlog_version: 'v1', confirmed: true, billing_acknowledged: true }));
    const key = onAction.mock.calls[0][0].idempotency_key;
    fireEvent.click(button);
    expect(onAction.mock.calls[1][0].idempotency_key).toBe(key);
    rerender(<ContinuationScopeAddition {...props} snapshot={{ ...snapshot, version: 'v2' }} />);
    expect(button.disabled).toBe(true);
    expect(screen.getAllByRole('checkbox')[1].checked).toBe(false);
    fireEvent.click(screen.getAllByRole('checkbox')[1]);
    fireEvent.click(button);
    expect(onAction.mock.calls[2][0].idempotency_key).not.toBe(key);
    rerender(<ContinuationScopeAddition {...props} value={{ ...value, manager_calls: 3 }} />);
    expect(button.disabled).toBe(true);
  });
});
