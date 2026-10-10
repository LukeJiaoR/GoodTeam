import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import WorkApprovalAction from './WorkApprovalAction';
import { TaskAttention } from './TaskAttention';

const t = (key) => key;
const reasonLabel = 'work.informedApproval.reason';
const continueLabel = 'work.informedApproval.continue';
function props(overrides = {}) {
  return {
    projectKey: 'A', gate: 'rfc', t, onDecide: vi.fn().mockResolvedValue({}),
    brief: {
      schema_version: 'manager_approval_brief.v1',
      approval: 'rfc',
      approval_target: { run_id: 'run-1', target_type: 'review', target_id: 'rfc', task_id: 'task-1', artifact_key: 'rfc', artifact_version: 'sha256:abc' },
      recommendation: { verdict: 'caution', reasons: [{ kind: 'missing_evidence', text: 'Independent review is missing.' }] },
      evidence: [{ kind: 'reviewer', status: 'missing' }],
    },
    ...overrides,
  };
}
function enterReason(text = 'I inspected the change and accept this bounded risk.') {
  fireEvent.change(screen.getByRole('textbox', { name: reasonLabel }), { target: { value: text } });
}

afterEach(() => vi.restoreAllMocks());

describe('version-bound informed Work decision', () => {
  it('rejects empty and whitespace reasons through both click and keyboard', () => {
    const value = props(); render(<WorkApprovalAction {...value} />);
    const button = screen.getByRole('button', { name: continueLabel });
    expect(button).toBeDisabled();
    enterReason('   ');
    fireEvent.click(button);
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter', ctrlKey: true });
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter', metaKey: true });
    expect(value.onDecide).not.toHaveBeenCalled();
  });

  it('sends the typed reason and exact server target without synthesizing a pass', async () => {
    const value = props(); render(<WorkApprovalAction {...value} />);
    enterReason('  I checked the missing evidence manually.  ');
    fireEvent.click(screen.getByRole('button', { name: continueLabel }));
    await waitFor(() => expect(value.onDecide).toHaveBeenCalledTimes(1));
    expect(value.onDecide).toHaveBeenCalledWith('rfc', 'approved', 'I checked the missing evidence manually.', value.brief.approval_target);
  });

  it('holds the submission lock for mouse and keyboard until the request settles', async () => {
    let finish;
    const pending = new Promise(resolve => { finish = resolve; });
    const value = props({ onDecide: vi.fn(() => pending) });
    render(<WorkApprovalAction {...value} />);
    enterReason();
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter', ctrlKey: true });
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter', metaKey: true });
    fireEvent.click(screen.getByRole('button', { name: continueLabel }));
    expect(value.onDecide).toHaveBeenCalledTimes(1);
    await act(async () => { finish({}); await pending; });
  });

  it('preserves an informed reason on failure and permits an explicit retry', async () => {
    const value = props({ onDecide: vi.fn().mockRejectedValueOnce(new Error('rejected')).mockResolvedValueOnce({}) });
    render(<WorkApprovalAction {...value} />);
    enterReason('Checked manually.');
    fireEvent.click(screen.getByRole('button', { name: continueLabel }));
    await screen.findByRole('alert');
    expect(screen.getByRole('textbox')).toHaveValue('Checked manually.');
    fireEvent.click(screen.getByRole('button', { name: continueLabel }));
    await waitFor(() => expect(value.onDecide).toHaveBeenCalledTimes(2));
    expect(value.onDecide.mock.calls[1][2]).toBe('Checked manually.');
  });

  it.each(['project', 'run', 'version', 'evidence'])('clears an old reason when %s changes, but not on an unchanged poll', (change) => {
    const value = props();
    const view = render(<WorkApprovalAction {...value} />);
    enterReason('Reason for the old context.');
    view.rerender(<WorkApprovalAction {...value} brief={structuredClone(value.brief)} />);
    expect(screen.getByRole('textbox')).toHaveValue('Reason for the old context.');
    const next = { ...value, brief: structuredClone(value.brief) };
    if (change === 'project') next.projectKey = 'B';
    if (change === 'run') next.brief.approval_target.run_id = 'run-2';
    if (change === 'version') next.brief.approval_target.artifact_version = 'sha256:def';
    if (change === 'evidence') next.brief.recommendation.reasons[0].text = 'A different gap.';
    view.rerender(<WorkApprovalAction {...next} />);
    expect(screen.getByRole('textbox')).toHaveValue('');
    expect(screen.getByRole('button', { name: continueLabel })).toBeDisabled();
    expect(value.onDecide).not.toHaveBeenCalled();
  });

  it('does not let a late failure alter the new version confirmation', async () => {
    let fail;
    const pending = new Promise((resolve, reject) => { fail = reject; });
    const value = props({ onDecide: vi.fn(() => pending) });
    const view = render(<WorkApprovalAction {...value} />);
    enterReason('Old version reason.');
    fireEvent.click(screen.getByRole('button', { name: continueLabel }));
    const next = { ...value, brief: structuredClone(value.brief) };
    next.brief.approval_target.artifact_version = 'sha256:new';
    view.rerender(<WorkApprovalAction {...next} />);
    enterReason('New version reason.');
    await act(async () => { fail(new Error('old request')); await pending.catch(() => {}); });
    expect(screen.getByRole('textbox')).toHaveValue('New version reason.');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('keeps the bound quick path for an explicit positive recommendation', async () => {
    const value = props(); value.brief.recommendation.verdict = 'approve';
    render(<WorkApprovalAction {...value} />);
    fireEvent.click(screen.getByRole('button', { name: 'work.manager.approvalBrief.approve' }));
    await waitFor(() => expect(value.onDecide).toHaveBeenCalledTimes(1));
    expect(value.onDecide).toHaveBeenCalledWith('rfc', 'approved', '', value.brief.approval_target);
  });

  it('cannot manufacture permission or an unbound approval', () => {
    const value = props(); delete value.brief.approval_target.artifact_version;
    const view = render(<WorkApprovalAction {...value} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    view.rerender(<TaskAttention
      attention={{ kind: 'approval', target_id: 'rfc', actions: [], approval_brief: props().brief }}
      projectKey="A" onDecide={value.onDecide} t={t}
    />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(value.onDecide).not.toHaveBeenCalled();
  });

  it('disables confirmation during a different parent mutation', () => {
    const value = props();
    const view = render(<WorkApprovalAction {...value} />);
    enterReason();
    view.rerender(<WorkApprovalAction {...value} busy="intent-start" />);
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter', ctrlKey: true });
    expect(value.onDecide).not.toHaveBeenCalled();
  });
});
