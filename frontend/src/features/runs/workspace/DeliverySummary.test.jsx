import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { en } from '../../../i18n/locales/en';
import { requestJson } from '../../../lib/api/client';
import DeliverySummary from './DeliverySummary';

vi.mock('../../../lib/api/client', () => ({ requestJson: vi.fn() }));
const t = (key) => en[key] || key;
const summary = { run_id: 'run', status: 'needs_verification', simulated: false, attempts: [], artifacts: [
  { key: 'implementation_handoff', version: 'sha256:one', href: '/api/delivery-artifact?run=run&version=sha256:one' },
] };

describe('Delivery summary', () => {
  it('opens the exact report and does not claim verified completion', async () => {
    requestJson.mockResolvedValue({ content: '# Local result\nTests passed for the patch.' });
    render(<DeliverySummary summary={summary} t={t} />);
    expect(screen.getByText(en['work.deliverySummary.unverified'])).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /handoff/i }));
    expect(await screen.findByRole('heading', { name: 'Local result' })).toBeInTheDocument();
    expect(requestJson).toHaveBeenCalledWith(summary.artifacts[0].href);
  });
  it('shows missing and simulated evidence without an open action', () => {
    render(<DeliverySummary summary={{ ...summary, simulated: true, artifacts: [{ key: 'qa_report', href: '', version: '' }] }} t={t} />);
    expect(screen.getByRole('button')).toBeDisabled();
    expect(screen.getByText(en['work.deliverySummary.simulated'])).toBeInTheDocument();
  });
});
