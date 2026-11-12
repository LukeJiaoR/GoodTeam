import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi, test, expect } from 'vitest';
import TaskSearchConfiguration from './TaskSearchConfiguration';
import { getProviders } from '../../settings/providerApi';
vi.mock('../../settings/providerApi', () => ({ getProviders: vi.fn() }));

test('search defaults off and explicit save pins provider revision without executing', async () => {
  getProviders.mockResolvedValue({ providers: [{ id: 'p', revision: 3, name: 'Existing provider' }] });
  const onAction = vi.fn().mockResolvedValue({});
  render(<TaskSearchConfiguration item={{ id: 'q', revision: 4, web_search: null }} onAction={onAction} language="en" />);
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

test('reopening requires a fresh provider revision and one save stays in flight', async () => {
  getProviders.mockReset();
  const policy = { provider: { id: 'p', revision: 3 }, model: 'search-model', max_requests: 1, timeout_seconds: 30 };
  getProviders.mockResolvedValueOnce({ providers: [{ id: 'p', revision: 3, name: 'Existing provider' }] });
  let finishSave;
  const onAction = vi.fn(() => new Promise(resolve => { finishSave = resolve; }));
  render(<TaskSearchConfiguration item={{ id: 'q', revision: 4, web_search: policy }} onAction={onAction} language="en" />);
  fireEvent.click(screen.getByText('Configure search'));
  await screen.findByText('Existing provider (revision 3)');
  fireEvent.click(screen.getByText('Cancel'));
  let finishLoad;
  getProviders.mockImplementationOnce(() => new Promise(resolve => { finishLoad = resolve; }));
  fireEvent.click(screen.getByText('Configure search'));
  expect(screen.getByRole('button', { name: /Acknowledge/ })).toBeDisabled();
  expect(screen.queryByText('Existing provider (revision 3)')).not.toBeInTheDocument();
  finishLoad({ providers: [{ id: 'p', revision: 4, name: 'Existing provider' }] });
  await screen.findByText('Existing provider (revision 4)');
  fireEvent.click(screen.getByRole('button', { name: /Acknowledge/ }));
  fireEvent.submit(screen.getByRole('button', { name: /Acknowledge/ }).closest('form'));
  expect(onAction).toHaveBeenCalledTimes(1);
  expect(onAction.mock.calls[0][0].web_search.provider.revision).toBe(4);
  expect(screen.getByText('Cancel')).toBeDisabled();
  finishSave(false);
  await waitFor(() => expect(screen.getByText('Cancel')).not.toBeDisabled());
  fireEvent.click(screen.getByText('Cancel'));
  getProviders.mockRejectedValueOnce(new Error('Provider list unavailable'));
  fireEvent.click(screen.getByText('Configure search'));
  await screen.findByRole('alert');
  expect(screen.getByRole('button', { name: /Acknowledge/ })).toBeDisabled();
  expect(onAction).toHaveBeenCalledTimes(1);
});
