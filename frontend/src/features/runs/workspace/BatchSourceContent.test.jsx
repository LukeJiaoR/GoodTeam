import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { translate } from '../../../i18n';
import { requestJson } from '../../../lib/api/client';
import SourceEvidence from '../../../components/ui/SourceEvidence';
import BatchSourceContent from './BatchSourceContent';

vi.mock('../../../lib/api/client', () => ({ requestJson: vi.fn() }));

it('reads the matching original review only after opening and displays its findings and source quote', async () => {
  const t = (key, params) => translate('en', key, params);
  const head = 'a'.repeat(40);
  requestJson.mockResolvedValueOnce({ project_root: '/isolated/project', jobs: [{ summary: 'Implemented keyboard navigation.' }],
    request: { command: 'internal-request-command' },
    quality: { execution_id: 'quality-1', candidate_commit: head, review_status: 'machine_recorded', validation: {
      available: true, check_status: 'passed', receipt: { receipt_id: 'check-1', observation: {
        output: { stdout: { sha256: 'internal-output-digest', bytes: 72 } } } } } } })
    .mockResolvedValueOnce({ execution_id: 'quality-1', candidate_review_report: { available: true,
      head_sha: head, validation_receipt_id: 'check-1', report: {
        summary: 'The focused navigation behavior is covered.', findings: [{ title: 'Missing focus restoration',
          detail: 'Closing the picker loses keyboard focus.', recommendation: 'Restore focus to its trigger.' }],
        checks: [{ name: 'Keyboard navigation', status: 'checked', detail: 'Inspected the focused test.' }],
        evidence: [{ path: 'src/picker.jsx', line_start: 42, quote: 'trigger.current.focus();', observation: 'Focus returns to the trigger.' }],
      } } });
  const view = render(<SourceEvidence t={t}><BatchSourceContent projectKey="APP" runId="task-batch-1" expectedCandidate={head} t={t} /></SourceEvidence>);
  expect(requestJson).not.toHaveBeenCalled();
  const open = screen.getByRole('button', { name: 'View source evidence' });
  open.focus();
  fireEvent.click(open);
  await screen.findByText('Missing focus restoration');
  const panel = screen.getByRole('dialog');
  expect(panel).toHaveTextContent('Closing the picker loses keyboard focus.');
  expect(panel).toHaveTextContent('Restore focus to its trigger.');
  expect(panel).toHaveTextContent('trigger.current.focus();');
  expect(panel).toHaveTextContent(t('product.evidence.outputUnavailable'));
  expect(panel).not.toHaveTextContent('internal-request-command');
  expect(panel).not.toHaveTextContent('internal-output-digest');
  expect(panel).not.toHaveTextContent('check-1');
  expect(view.container).not.toHaveTextContent('Missing focus restoration');
  expect(requestJson).toHaveBeenCalledTimes(2);
  expect(requestJson.mock.calls.every(([, options]) => !options.method)).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Close evidence' }));
  expect(open).toHaveFocus();
  requestJson.mockResolvedValueOnce({ quality: { candidate_commit: 'b'.repeat(40) } });
  fireEvent.click(open);
  await screen.findByText(t('product.evidence.changed'));
  expect(screen.getByRole('dialog')).not.toHaveTextContent('Missing focus restoration');
  expect(requestJson).toHaveBeenCalledTimes(3);
});
