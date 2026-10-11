import { StrictMode } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Link, MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { expect, test, vi } from 'vitest';
import LoadProjectPanel from './LoadProjectPanel';
import ProjectsPage from './ProjectsPage';

vi.mock('../../hooks/useDirectoryPicker', () => ({
  useDirectoryPicker: () => ({
    chooseLocalPath: vi.fn(),
    directoryPickerModal: null,
  }),
}));

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function CurrentRoute() {
  return <output data-testid="current-route">{useLocation().pathname}</output>;
}

function renderLauncher(importProject) {
  const api = {
    listProjects: vi.fn().mockResolvedValue({ projects: [] }),
    importProject,
  };
  const result = render(
    <MemoryRouter initialEntries={['/projects']}>
      <Link to="/elsewhere">Leave projects</Link>
      <CurrentRoute />
      <Routes>
        <Route path="/projects" element={<ProjectsPage api={api} language="en" />} />
        <Route path="*" element={<p>Another page</p>} />
      </Routes>
    </MemoryRouter>,
  );
  return { ...result, api };
}

async function startImport(user, root = '/work/pending') {
  await user.click((await screen.findAllByRole('button', { name: 'Add project' }))[0]);
  fireEvent.change(screen.getByLabelText('Project directory'), { target: { value: root } });
  await user.click(screen.getByRole('button', { name: 'Load project' }));
}

const dismissals = [
  ['close button', (user) => user.click(screen.getByRole('button', { name: 'Close project import' }))],
  ['Escape', (user) => user.keyboard('{Escape}')],
  ['backdrop', () => fireEvent.mouseDown(screen.getByRole('dialog').parentElement)],
  ['footer dismissal', (user) => user.click(screen.getByRole('button', { name: 'Close', exact: true }))],
];

test.each(dismissals)('%s during import prevents late success from navigating', async (_label, dismiss) => {
  const user = userEvent.setup();
  const request = deferred();
  const { api } = renderLauncher(vi.fn(() => request.promise));
  await startImport(user);

  await dismiss(user);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  await act(async () => request.resolve({ project: { project_key: 'LATE' } }));

  expect(api.importProject).toHaveBeenCalledWith({ root: '/work/pending' });
  expect(screen.getByTestId('current-route')).toHaveTextContent(/^\/projects$/);
});

test('leaving Projects while importing prevents the late success from changing the new route', async () => {
  const user = userEvent.setup();
  const request = deferred();
  renderLauncher(vi.fn(() => request.promise));
  await startImport(user);

  await user.click(screen.getByRole('link', { name: 'Leave projects' }));
  expect(screen.getByTestId('current-route')).toHaveTextContent('/elsewhere');
  await act(async () => request.resolve({ project: { project_key: 'LATE' } }));

  expect(screen.getByTestId('current-route')).toHaveTextContent('/elsewhere');
});

test.each([
  ['success', (request) => request.resolve({ project: { project_key: 'OLD' } })],
  ['failure', (request) => request.reject(Object.assign(new Error('Failed'), {
    code: 'project_root_not_readable', recovery: ['choose_another_directory'],
  }))],
  ['invalid response', (request) => request.resolve({})],
])('an older dismissed %s cannot replace a newer pending import', async (_label, settleOld) => {
  const user = userEvent.setup();
  const oldRequest = deferred();
  const newRequest = deferred();
  const { api } = renderLauncher(vi.fn()
    .mockReturnValueOnce(oldRequest.promise)
    .mockReturnValueOnce(newRequest.promise));
  await startImport(user, '/work/old');
  await user.click(screen.getByRole('button', { name: 'Close project import' }));
  await startImport(user, '/work/new');

  await act(async () => settleOld(oldRequest));

  expect(screen.getByTestId('current-route')).toHaveTextContent(/^\/projects$/);
  expect(screen.getByLabelText('Project directory')).toHaveValue('/work/new');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Load project' })).toBeDisabled();
  expect(api.importProject).toHaveBeenCalledTimes(2);
  expect(api.importProject).toHaveBeenLastCalledWith({ root: '/work/new' });

  await act(async () => newRequest.resolve({ project: { project_key: 'NEW' } }));

  expect(screen.getByTestId('current-route')).toHaveTextContent('/projects/NEW/work');
});

test.each(dismissals)('%s invalidates the response before the owner unmounts the dialog', async (_label, dismiss) => {
  const user = userEvent.setup();
  const request = deferred();
  const onImported = vi.fn();
  const onClose = vi.fn();
  const api = { importProject: vi.fn(() => request.promise) };
  render(<LoadProjectPanel api={api} language="en" onClose={onClose} onImported={onImported} />);
  fireEvent.change(screen.getByLabelText('Project directory'), { target: { value: '/work/pending' } });
  await user.click(screen.getByRole('button', { name: 'Load project' }));

  await dismiss(user);
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('dialog')).toBeInTheDocument();
  await act(async () => request.resolve({ project: { project_key: 'LATE' } }));
  fireEvent.submit(screen.getByRole('dialog'));

  expect(onImported).not.toHaveBeenCalled();
  expect(api.importProject).toHaveBeenCalledTimes(1);
});

test('unmounting a pending dialog does not adopt its late success', async () => {
  const user = userEvent.setup();
  const request = deferred();
  const onImported = vi.fn();
  const api = { importProject: vi.fn(() => request.promise) };
  const { unmount } = render(<LoadProjectPanel api={api} language="en" onImported={onImported} />);
  fireEvent.change(screen.getByLabelText('Project directory'), { target: { value: '/work/pending' } });
  await user.click(screen.getByRole('button', { name: 'Load project' }));

  unmount();
  await act(async () => request.resolve({ project: { project_key: 'LATE' } }));

  expect(onImported).not.toHaveBeenCalled();
});

test('same-turn duplicate submits and later clicks post once, and current success survives StrictMode', async () => {
  const user = userEvent.setup();
  const request = deferred();
  const onImported = vi.fn();
  const api = { importProject: vi.fn(() => request.promise) };
  render(
    <StrictMode>
      <LoadProjectPanel api={api} language="en" onImported={onImported} />
    </StrictMode>,
  );
  fireEvent.change(screen.getByLabelText('Project directory'), { target: { value: '  /work/current  ' } });
  const dialog = screen.getByRole('dialog');
  act(() => {
    fireEvent.submit(dialog);
    fireEvent.submit(dialog);
  });
  await user.dblClick(screen.getByRole('button', { name: 'Load project' }));

  expect(api.importProject).toHaveBeenCalledTimes(1);
  expect(api.importProject).toHaveBeenCalledWith({ root: '/work/current' });
  const payload = { created: false, project: { project_key: 'CURRENT' } };
  await act(async () => request.resolve(payload));

  expect(onImported).toHaveBeenCalledExactlyOnceWith(payload);
  expect(screen.getByRole('button', { name: 'Load project' })).toBeEnabled();
});

test('Escape already handled by a nested picker does not dismiss or invalidate the active import', async () => {
  const user = userEvent.setup();
  const request = deferred();
  renderLauncher(vi.fn(() => request.promise));
  await startImport(user);
  const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
  event.preventDefault();
  fireEvent(document, event);
  fireEvent.mouseDown(screen.getByLabelText('Project directory'));

  expect(screen.getByRole('dialog')).toBeInTheDocument();
  await act(async () => request.resolve({ project: { project_key: 'CURRENT' } }));

  expect(screen.getByTestId('current-route')).toHaveTextContent('/projects/CURRENT/work');
});

test('Cancel before submitting closes the dialog without starting an import', async () => {
  const user = userEvent.setup();
  const { api } = renderLauncher(vi.fn());
  await user.click((await screen.findAllByRole('button', { name: 'Add project' }))[0]);
  fireEvent.change(screen.getByLabelText('Project directory'), { target: { value: '/work/unused' } });

  await user.click(screen.getByRole('button', { name: 'Cancel', exact: true }));

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(api.importProject).not.toHaveBeenCalled();
});
