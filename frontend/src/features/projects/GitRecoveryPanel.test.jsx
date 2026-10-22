import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import GitRecoveryPanel from './GitRecoveryPanel';
import { gitRecoveryApi } from './gitRecoveryApi';

function apiFor(state = 'unborn') {
  return {
    read: vi.fn().mockResolvedValue({ git_baseline: { state, baseline_required: state !== 'valid_head' } }),
    recover: vi.fn().mockResolvedValue({ initialized: true }),
  };
}
async function open(api, key = 'A') {
  const view = render(<GitRecoveryPanel projectKey={key} api={api} />);
  expect(api.read).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Git baseline and recovery' }));
  await waitFor(() => expect(screen.queryByText('Checking Git status…')).not.toBeInTheDocument());
  return view;
}
function select(value = 'app.py\nREADME.md') {
  fireEvent.change(screen.getByRole('textbox'), { target: { value } });
  fireEvent.click(screen.getByRole('button', { name: 'Review selected files' }));
}

test.each([
  ['valid_head', 'Valid Git commit'], ['unborn', 'Initial commit missing'],
  ['non_git', 'Git is not initialized'], ['invalid', 'Git baseline is invalid'],
  ['unavailable', 'Git status unavailable'], ['unexpected', 'Git status unavailable'],
])('shows server state %s without guessing readiness', async (state, label) => {
  const api = apiFor(state);
  await open(api);
  expect(screen.getByRole('status')).toHaveTextContent(label);
  expect(Boolean(screen.queryByRole('textbox'))).toBe(['unborn', 'non_git'].includes(state));
  expect(api.recover).not.toHaveBeenCalled();
});

test('requires exact selection and confirmation; refreshes actual state after POST', async () => {
  const api = apiFor();
  await open(api);
  select();
  expect(api.recover).not.toHaveBeenCalled();
  api.read.mockResolvedValue({ git_baseline: { state: 'valid_head', baseline_required: false, head_sha: 'abc123' } });
  fireEvent.click(screen.getByRole('button', { name: 'Confirm initial commit' }));
  await screen.findByText('abc123');
  expect(api.recover).toHaveBeenCalledExactlyOnceWith('A', ['app.py', 'README.md']);
  expect(screen.getByRole('status')).toHaveTextContent('Valid Git commit');
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
});

test.each(['', '../secret', '/absolute', '.git/config', '.goodteam/data', 'a/../b', 'a//b', 'C:/test', 'a\\b'])('rejects invalid selection %s', async (value) => {
  const api = apiFor();
  await open(api); select(value);
  expect(screen.getByRole('alert')).toHaveTextContent('Enter regular relative file paths');
  expect(screen.queryByRole('button', { name: 'Confirm initial commit' })).not.toBeInTheDocument();
  expect(api.recover).not.toHaveBeenCalled();
});

test('cancel and edits discard confirmation', async () => {
  const api = apiFor(); await open(api); select();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(api.recover).not.toHaveBeenCalled();
  select();
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'other.py' } });
  expect(screen.queryByRole('button', { name: 'Confirm initial commit' })).not.toBeInTheDocument();
});

test('does not treat initialized true as proof; preserves server warning', async () => {
  const api = apiFor(); api.recover.mockResolvedValue({ initialized: true, warnings: ['Commit verification failed'] });
  await open(api); select();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm initial commit' }));
  await screen.findByText(/Commit verification failed/);
  expect(screen.getByRole('status')).toHaveTextContent('Initial commit missing');
});

test('uncertain POST never retries and failed readback disables recovery', async () => {
  const api = apiFor(); await open(api); select();
  api.recover.mockRejectedValue(new Error('Connection lost'));
  api.read.mockRejectedValue(new Error('Offline'));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm initial commit' }));
  await screen.findByText('Connection lost');
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Git status unavailable'));
  expect(api.recover).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
});

test('readback failure preserves the server recovery error and warnings', async () => {
  const api = apiFor(); await open(api); select();
  api.recover.mockResolvedValue({ error: 'Git commit failed', warnings: ['Selected file was rejected'] });
  api.read.mockRejectedValue(new Error('Offline'));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm initial commit' }));
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Git status unavailable'));
  expect(screen.getByLabelText('Recovery response')).toHaveTextContent('Git commit failed');
  expect(screen.getByLabelText('Recovery response')).toHaveTextContent('Selected file was rejected');
  expect(screen.getByLabelText('Recovery response')).toHaveTextContent('Could not read current Git status');
});

test('refresh clears confirmation when the repository becomes invalid', async () => {
  const api = apiFor(); await open(api); select();
  api.read.mockResolvedValue({ git_baseline: { state: 'invalid', baseline_required: true, error: 'Trusted Git unavailable' } });
  fireEvent.click(screen.getByRole('button', { name: 'Refresh Git status' }));
  await screen.findByText('Trusted Git unavailable');
  expect(screen.queryByRole('button', { name: 'Confirm initial commit' })).not.toBeInTheDocument();
  expect(api.recover).not.toHaveBeenCalled();
});

test('project switch drops pending results and selected files', async () => {
  const api = apiFor(); const view = await open(api); select();
  let finish;
  api.recover.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm initial commit' }));
  view.rerender(<GitRecoveryPanel projectKey="B" api={api} />);
  await act(async () => finish({ warnings: ['old result'] }));
  expect(screen.queryByText(/old result/)).not.toBeInTheDocument();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(api.read).toHaveBeenCalledTimes(1);
});

test('duplicate confirmation starts only one request', async () => {
  const api = apiFor(); await open(api); select();
  let finish;
  api.recover.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const button = screen.getByRole('button', { name: 'Confirm initial commit' });
  fireEvent.click(button); fireEvent.click(button);
  expect(api.recover).toHaveBeenCalledTimes(1);
  await act(async () => finish({}));
});

test('API sends only explicit files and no remote creation', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, text: async () => '{}' });
  vi.stubGlobal('fetch', fetch);
  try {
    await gitRecoveryApi.recover('A', ['a.py']);
    expect(fetch).toHaveBeenCalledWith('/api/git-init', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ project_key: 'A', initial_files: ['a.py'], create_remote: false }),
    }));
    await gitRecoveryApi.read('A/B');
    expect(fetch).toHaveBeenLastCalledWith('/api/projects/A%2FB/home', { cache: 'no-store' });
  } finally { vi.unstubAllGlobals(); }
});
