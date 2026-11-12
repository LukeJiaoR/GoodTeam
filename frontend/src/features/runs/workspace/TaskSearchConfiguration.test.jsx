import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi, test, expect } from 'vitest';
import TaskSearchConfiguration from './TaskSearchConfiguration';
import { getProviders } from '../../settings/providerApi';
vi.mock('../../settings/providerApi', () => ({ getProviders: vi.fn() }));

test('search defaults off and explicit save pins provider revision without executing', async () => {
  getProviders.mockResolvedValue({ providers: [{ id: 'p', revision: 3, name: 'Existing provider' }] });
  const onAction = vi.fn().mockResolvedValue({});
  render(<TaskSearchConfiguration item={{ id: 'q', revision: 4, web_search: null, assignment: { role: 'research', execution_policy: 'read_only' } }} onAction={onAction} language="en" />);
  expect(screen.getByText('Saved Web Search: Off')).toBeInTheDocument();
  expect(getProviders).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('Configure search'));
  expect(screen.getByRole('checkbox')).not.toBeChecked();
  fireEvent.click(screen.getByRole('checkbox'));
  expect(screen.getByRole('button', { name: /Acknowledge/ })).toBeDisabled();
  await screen.findByText('Existing provider (revision 3)');
  fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'p' } });
  fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'search-model' } });
  fireEvent.change(screen.getByLabelText('Maximum requests'), { target: { value: '2' } });
  fireEvent.click(screen.getByRole('button', { name: /Acknowledge/ }));
  await waitFor(() => expect(onAction).toHaveBeenCalledWith({ action: 'configure_queued_search', proposal_id: 'q',
    expected_revision: 4, confirmed: true, web_search: { provider: { id: 'p', revision: 3 },
      model: 'search-model', max_requests: 2, timeout_seconds: 30 } }));
  expect(onAction).toHaveBeenCalledTimes(1);
});

test('unsupported read-only role cannot enable search', async () => {
  getProviders.mockResolvedValue({ providers: [] });
  render(<TaskSearchConfiguration item={{ id: 'qa', revision: 1, web_search: null,
    assignment: { role: 'qa', execution_policy: 'read_only' } }} language="en" />);
  fireEvent.click(screen.getByText('Configure search'));
  expect(screen.getByRole('checkbox')).toBeDisabled();
  await waitFor(() => expect(getProviders).toHaveBeenCalled());
});
