import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { en } from '../../../i18n/locales/en';
import { requestJson } from '../../../lib/api/client';
import DeliverySummary from './DeliverySummary';

vi.mock('../../../lib/api/client', () => ({ requestJson: vi.fn() }));
afterEach(() => requestJson.mockReset());
const t = (key) => en[key] || key;
const artifact = { key: 'implementation_handoff', version: 'sha256:one',
  href: '/api/delivery-artifact?project=DEMO&run=run&key=implementation_handoff&version=sha256%3Aone' };
const summary = { project_key: 'DEMO', run_id: 'run', status: 'needs_verification', simulated: false, attempts: [], artifacts: [artifact] };
const response = { content: '# Local result\nTests passed for the patch.', project_key: 'DEMO', run_id: 'run', artifact: artifact.key, version: artifact.version };
const open = () => fireEvent.click(screen.getByRole('button', { name: /handoff/i }));

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

describe('Delivery summary', () => {
  it('labels historical delivery and keeps technical evidence collapsed', () => {
    render(<DeliverySummary summary={summary} historical t={t} />);
    expect(screen.getByText('Historical task delivery · read-only')).toBeVisible();
    expect(screen.getByText('Report version').closest('details')).not.toHaveAttribute('open');
    expect(screen.getByText('Worker attempts for this run').closest('details')).not.toHaveAttribute('open');
    expect(screen.getByText('Verification')).toBeVisible();
    expect(screen.getByText('Next step')).toBeVisible();
  });
  it('opens the exact report only on request and does not claim verified completion', async () => {
    requestJson.mockResolvedValue(response);
    render(<DeliverySummary summary={summary} t={t} />);
    expect(requestJson).not.toHaveBeenCalled();
    expect(screen.getByText(en['work.deliverySummary.unverified'])).toBeInTheDocument();
    open();
    expect(await screen.findByRole('heading', { name: 'Local result' })).toBeInTheDocument();
    expect(requestJson).toHaveBeenCalledWith(artifact.href, { signal: expect.any(AbortSignal), cache: 'no-store' });
  });

  it.each(['project_key', 'run_id', 'artifact', 'version'])('rejects a response with the wrong %s', async (field) => {
    requestJson.mockResolvedValue({ ...response, [field]: 'OTHER' });
    render(<DeliverySummary summary={summary} t={t} />);
    open();
    expect(await screen.findByRole('alert')).toHaveTextContent(en['work.deliverySummary.readMismatch']);
    expect(screen.queryByRole('heading', { name: 'Local result' })).not.toBeInTheDocument();
  });

  it.each([
    'https://external.invalid/report',
    '/api/delivery-artifact?project=OTHER&run=run&key=implementation_handoff&version=sha256%3Aone',
    `${artifact.href}&run=other`,
  ])('never fetches an external, foreign or ambiguous report URL: %s', (href) => {
    render(<DeliverySummary summary={{ ...summary, artifacts: [{ ...artifact, href }] }} t={t} />);
    expect(screen.getByRole('button', { name: /handoff/i })).toBeDisabled();
    expect(requestJson).not.toHaveBeenCalled();
  });

  it('aborts a read and ignores its late response after closing', async () => {
    const pending = deferred();
    requestJson.mockReturnValue(pending.promise);
    render(<DeliverySummary summary={summary} t={t} />);
    open();
    const signal = requestJson.mock.calls[0][1].signal;
    fireEvent.click(screen.getByRole('button', { name: en['work.plan.close'] }));
    expect(signal.aborted).toBe(true);
    await act(async () => pending.resolve(response));
    expect(screen.queryByRole('heading', { name: 'Local result' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /handoff/i })).toBeEnabled();
  });

  it('cannot show a previous run or version after re-entry', async () => {
    const pending = deferred();
    requestJson.mockReturnValue(pending.promise);
    const view = render(<DeliverySummary summary={summary} t={t} />);
    open();
    const signal = requestJson.mock.calls[0][1].signal;
    const otherArtifact = { ...artifact, version: 'sha256:two', href: artifact.href.replace('run=run', 'run=next').replace('sha256%3Aone', 'sha256%3Atwo') };
    view.rerender(<DeliverySummary summary={{ ...summary, run_id: 'next', artifacts: [otherArtifact] }} t={t} />);
    expect(signal.aborted).toBe(true);
    await act(async () => pending.resolve(response));
    expect(screen.queryByRole('heading', { name: 'Local result' })).not.toBeInTheDocument();
    expect(screen.getByText('sha256:two')).toBeInTheDocument();
  });

  it('clears previously displayed content before a failing re-read', async () => {
    requestJson.mockResolvedValueOnce(response).mockRejectedValueOnce(new Error('The report changed. Reload Work.'));
    render(<DeliverySummary summary={summary} t={t} />);
    open();
    expect(await screen.findByRole('heading', { name: 'Local result' })).toBeInTheDocument();
    open();
    expect(await screen.findByRole('alert')).toHaveTextContent('The report changed. Reload Work.');
    expect(screen.queryByRole('heading', { name: 'Local result' })).not.toBeInTheDocument();
  });
});


it('discards delivery review evidence when the same run receives a newer artifact version', async () => {
  const pending = deferred();
  requestJson.mockReturnValue(pending.promise);
  const view = render(<DeliverySummary summary={summary} t={t} />);
  fireEvent.click(screen.getByRole('button', { name: 'Review evidence for this delivery' }));
  const signal = requestJson.mock.calls[0][1].signal;
  view.rerender(<DeliverySummary summary={{ ...summary, artifacts: [{ ...artifact, version: 'sha256:new' }] }} t={t} />);
  expect(signal.aborted).toBe(true);
  await act(async () => pending.resolve({ schema_version: 'goodteam.task-reviews.v1', project_key: 'DEMO', run_id: 'run', reviews: [] }));
  expect(screen.queryByText('No registered review evidence for this task. This does not prove that no execution occurred.')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Review evidence for this delivery' })).toHaveAttribute('aria-expanded', 'false');
});
