import { StrictMode } from 'react';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { ApiError, requestJson } from '../../lib/api/client';
import ProjectsPage from './ProjectsPage';
import { importProject } from './projectApi';
import { normalizeProjectsPayload } from './projectList';
import {
  projectErrorMessageKey,
  projectRecoveryPresentation,
} from './projectPresentation';

const chooseLocalPath = vi.hoisted(() => vi.fn());

vi.mock('../../hooks/useDirectoryPicker', () => ({
  useDirectoryPicker: () => ({
    chooseLocalPath,
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

function renderProjects({ api, language = 'en', navigate = vi.fn(), ...pageProps } = {}) {
  const result = render(
    <MemoryRouter>
      <ProjectsPage
        api={api}
        navigate={navigate}
        language={language}
        {...pageProps}
      />
    </MemoryRouter>,
  );
  return { ...result, navigate };
}

function project({
  key,
  name,
  lifecycle,
  attentionCount = 0,
  activeRun = null,
}) {
  return {
    project: {
      project_key: key,
      display_name: name,
      root: `/work/${key.toLowerCase()}`,
    },
    readiness: {
      lifecycle,
      updated_at: '2026-07-17T08:00:00Z',
    },
    attention_count: attentionCount,
    active_run: activeRun,
  };
}

beforeEach(() => {
  chooseLocalPath.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('requestJson', () => {
  test('normalizes a structured server error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: 'project_root_not_readable',
      message: 'denied',
      details: { root: '/private/repo' },
      recovery: ['fix_permissions', 'choose_another_directory'],
      request_id: 'request-1',
    }), {
      status: 403,
      headers: { 'Content-Type': 'application/json' },
    })));

    const error = await requestJson('/api/projects').catch((caught) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 403,
      code: 'project_root_not_readable',
      details: { root: '/private/repo' },
      recovery: ['fix_permissions', 'choose_another_directory'],
      requestId: 'request-1',
    });
  });

  test.each([
    ['malformed JSON', '{"projects":'],
    ['non-JSON', '<!doctype html><title>Proxy error</title>'],
  ])('rejects a successful %s response as a stable ApiError', async (_label, body) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body, {
      status: 200,
      headers: { 'Content-Type': 'text/html' },
    })));

    await expect(requestJson('/api/projects')).rejects.toMatchObject({
      name: 'ApiError',
      status: 200,
      code: 'invalid_response',
    });
  });

  test.each([
    ['empty', ''],
    ['null', 'null'],
    ['array', '[]'],
    ['string', '"payload"'],
    ['number', '42'],
  ])('rejects a successful %s body as invalid_response', async (_label, body) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body, { status: 200 })));

    await expect(requestJson('/api/projects')).rejects.toMatchObject({
      code: 'invalid_response',
      status: 200,
    });
  });

  test('normalizes a fetch-level network failure as an offline ApiError', async () => {
    const failure = new TypeError('offline');
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(failure));

    await expect(requestJson('/api/projects')).rejects.toMatchObject({
      name: 'ApiError',
      code: 'offline',
      recovery: ['check_service', 'retry_request'],
    });
  });

  test('rejects array-shaped structured error details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: 'invalid_request',
      message: 'bad request',
      details: ['not', 'an', 'object'],
    }), { status: 400 })));

    await expect(requestJson('/api/projects')).rejects.toMatchObject({
      code: 'invalid_request',
      details: {},
    });
  });
});

test.each([
  'invalid_request',
  'invalid_project_root',
  'project_root_not_absolute',
  'invalid_project_key',
  'invalid_project_mode',
  'project_not_found',
  'project_root_not_found',
  'project_root_not_directory',
  'project_root_not_readable',
  'project_root_unavailable',
  'project_workspace_not_isolated',
  'project_readiness_mismatch',
  'project_readiness_missing',
  'same_origin_required',
  'internal_error',
  'invalid_response',
])('maps known project error %s to an explicit translation key', (code) => {
  expect(projectErrorMessageKey(code)).toBe(`projects.errors.${code}`);
});

test('maps unknown project errors to generic localized copy', () => {
  expect(projectErrorMessageKey('raw_server_secret')).toBe('product.errors.generic');
});

test.each([
  ['choose_another_directory', 'choose'],
  ['retry', 'retry'],
  ['retry_request', 'retry'],
  ['retry_from_goodteam_origin', 'reload_page'],
  ['reload_project', 'retry'],
  ['return_to_projects', 'return'],
  ['fix_permissions', 'guidance'],
  ['report_request_id', 'guidance'],
  ['edit_project_settings', 'ignore'],
  ['choose_project_mode', 'ignore'],
  ['choose_project', 'ignore'],
])('classifies known recovery %s without raw-key rendering', (recovery, presentation) => {
  expect(projectRecoveryPresentation(recovery)).toBe(presentation);
});

test('safely ignores unknown recovery identifiers', () => {
  expect(projectRecoveryPresentation('open_untrusted_url')).toBe('ignore');
});

test('project import posts only the selected root', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    created: true,
    home_url: '/projects/DEMO/home',
  }), {
    status: 201,
    headers: { 'Content-Type': 'application/json' },
  }));
  vi.stubGlobal('fetch', fetchMock);

  await importProject({ root: '/work/demo', displayName: 'Ignored', projectKey: 'IGNORED' });

  expect(fetchMock).toHaveBeenCalledWith('/api/projects/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ root: '/work/demo' }),
  });
});

test('shows a shape-matching skeleton, then renders one workspace row per project', async () => {
  const list = deferred();
  const api = {
    listProjects: vi.fn(() => list.promise),
    importProject: vi.fn(),
  };
  renderProjects({ api });

  const loading = screen.getByRole('status', { name: 'Reading local projects' });
  expect(loading).toHaveAttribute('aria-busy', 'true');
  expect(within(loading).getAllByTestId('project-skeleton')).toHaveLength(3);

  list.resolve({
    projects: [
      project({ key: 'LEGACY', name: 'Legacy', lifecycle: 'legacy' }),
      project({ key: 'READY', name: 'Ready', lifecycle: 'ready' }),
      project({
        key: 'RUNNING',
        name: 'Running',
        lifecycle: 'ready',
        activeRun: { run_id: 'run-7', next_node: 'implementation' },
      }),
    ],
  });

  expect(await screen.findByRole('heading', { name: 'Choose a project' })).toBeVisible();
  expect(screen.queryByTestId('project-group')).not.toBeInTheDocument();
  expect(screen.getAllByRole('button', { name: /Enter workspace:/ })).toHaveLength(3);
  expect(screen.getByRole('button', { name: 'Enter workspace: Running' })).toHaveTextContent('Active work');
  expect(screen.getByRole('button', { name: 'Enter workspace: Ready' })).toHaveTextContent('Ready');
});

test('enters the Task Assistant Work conversation directly from a project row', async () => {
  const user = userEvent.setup();
  const navigate = vi.fn();
  const api = {
    listProjects: vi.fn().mockResolvedValue({
      projects: [project({ key: 'DEMO SPACE', name: 'Demo', lifecycle: 'ready' })],
    }),
    importProject: vi.fn(),
  };
  renderProjects({ api, navigate });

  await user.click(await screen.findByRole('button', { name: 'Enter workspace: Demo' }));

  expect(navigate).toHaveBeenCalledWith('/projects/DEMO%20SPACE/work');
});

test('renders a localized empty launcher after the project list resolves', async () => {
  const api = {
    listProjects: vi.fn().mockResolvedValue({ projects: [] }),
    importProject: vi.fn(),
  };

  renderProjects({ api, language: 'zh-CN' });

  expect(await screen.findByRole('heading', { name: '添加第一个项目' })).toBeVisible();
  expect(screen.getByText('选择一个本地代码仓库并在工作区中打开。')).toBeVisible();
});

test('opens project import on demand, traps focus, closes, and restores the launcher action', async () => {
  const user = userEvent.setup();
  const api = {
    listProjects: vi.fn().mockResolvedValue({ projects: [] }),
    importProject: vi.fn(),
  };
  renderProjects({ api });
  const addButton = (await screen.findAllByRole('button', { name: 'Add project' }))[0];

  await user.click(addButton);

  const dialog = screen.getByRole('dialog', { name: 'Load a local project' });
  expect(dialog).toHaveAccessibleDescription('GoodTeam records the project without changing files in the selected directory.');
  expect(screen.getByLabelText('Project directory')).toHaveFocus();
  const closeButton = screen.getByRole('button', { name: 'Close project import' });
  closeButton.focus();
  await user.tab({ shift: true });
  expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();

  await user.keyboard('{Escape}');
  expect(screen.queryByRole('dialog', { name: 'Load a local project' })).not.toBeInTheDocument();
  await waitFor(() => expect(addButton).toHaveFocus());
});

test('loads a selected project once and enters its Task Assistant conversation', async () => {
  const user = userEvent.setup();
  const navigate = vi.fn();
  const api = {
    listProjects: vi.fn().mockResolvedValue({ projects: [] }),
    importProject: vi.fn().mockResolvedValue({
      created: true,
      project: { project_key: 'DEMO', display_name: 'Demo' },
      home_url: '/projects/DEMO/home',
    }),
  };
  renderProjects({ api, navigate });

  await user.click((await screen.findAllByRole('button', { name: 'Add project' }))[0]);
  await user.type(screen.getByLabelText('Project directory'), '/tmp/demo');
  await user.click(screen.getByRole('button', { name: 'Load project' }));

  expect(api.importProject).toHaveBeenCalledTimes(1);
  expect(api.importProject).toHaveBeenCalledWith({ root: '/tmp/demo' });
  expect(navigate).toHaveBeenCalledWith('/projects/DEMO/work');
});

test('rejects a relative project path inline without posting and returns focus to the field', async () => {
  const user = userEvent.setup();
  const api = {
    listProjects: vi.fn().mockResolvedValue({ projects: [] }),
    importProject: vi.fn(),
  };
  renderProjects({ api });
  await user.click((await screen.findAllByRole('button', { name: 'Add project' }))[0]);
  const input = screen.getByLabelText('Project directory');
  await user.type(input, 'relative/project');

  await user.click(screen.getByRole('button', { name: 'Load project' }));

  expect(api.importProject).not.toHaveBeenCalled();
  expect(await screen.findByText('Enter an absolute project directory.')).toBeVisible();
  expect(input).toHaveValue('relative/project');
  expect(input).toHaveFocus();
});

test('uses the directory picker and writes its selection into the absolute path field', async () => {
  const user = userEvent.setup();
  const api = {
    listProjects: vi.fn().mockResolvedValue({ projects: [] }),
    importProject: vi.fn(),
  };
  renderProjects({ api });
  await user.click((await screen.findAllByRole('button', { name: 'Add project' }))[0]);
  const input = screen.getByLabelText('Project directory');
  expect(screen.getByText('The directory stays unchanged during import.')).toBeVisible();
  await user.type(input, '/work/current');

  await user.click(screen.getByRole('button', { name: 'Browse' }));

  expect(chooseLocalPath).toHaveBeenCalledTimes(1);
  const options = chooseLocalPath.mock.calls[0][0];
  expect(options).toMatchObject({ mode: 'directory', initialPath: '/work/current' });
  act(() => options.onSelected('/work/selected'));
  expect(input).toHaveValue('/work/selected');
  expect(screen.getByText('Directory selected and ready to load.')).toBeVisible();
});

test('duplicate imports enter the existing Work space', async () => {
  const user = userEvent.setup();
  const navigate = vi.fn();
  const api = {
    listProjects: vi.fn().mockResolvedValue({ projects: [] }),
    importProject: vi.fn().mockResolvedValue({
      created: false,
      project: { project_key: 'KNOWN', display_name: 'Known' },
      home_url: '/projects/KNOWN/home',
    }),
  };
  renderProjects({ api, navigate });

  await user.click((await screen.findAllByRole('button', { name: 'Add project' }))[0]);
  await user.type(screen.getByLabelText('Project directory'), '/work/known');
  await user.click(screen.getByRole('button', { name: 'Load project' }));

  expect(navigate).toHaveBeenCalledWith('/projects/KNOWN/work');
});

test('rejects an import response without a project key and stays in the dialog', async () => {
  const user = userEvent.setup();
  const navigate = vi.fn();
  const api = {
    listProjects: vi.fn().mockResolvedValue({ projects: [] }),
    importProject: vi.fn().mockResolvedValue({ home_url: '/projects/LEGACY/home' }),
  };
  renderProjects({ api, navigate });

  await user.click((await screen.findAllByRole('button', { name: 'Add project' }))[0]);
  await user.type(screen.getByLabelText('Project directory'), '/work/broken');
  await user.click(screen.getByRole('button', { name: 'Load project' }));

  expect(await screen.findByText('GoodTeam received an invalid server response.')).toBeVisible();
  expect(navigate).not.toHaveBeenCalled();
});

test('aborts the project list request on unmount without surfacing an error', () => {
  let signal;
  const api = {
    listProjects: vi.fn((options) => {
      signal = options.signal;
      return new Promise(() => {});
    }),
    importProject: vi.fn(),
  };
  const { unmount } = renderProjects({ api });

  expect(signal.aborted).toBe(false);
  unmount();
  expect(signal.aborted).toBe(true);
});

test('ignores an aborted list failure while the page remains mounted', async () => {
  const request = deferred();
  const api = {
    listProjects: vi.fn(() => request.promise),
    importProject: vi.fn(),
  };
  renderProjects({ api });
  const aborted = new DOMException('Aborted', 'AbortError');

  await act(async () => request.reject(aborted));

  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('shows localized server recovery inline and preserves the entered path', async () => {
  const user = userEvent.setup();
  const api = {
    listProjects: vi.fn().mockResolvedValue({ projects: [] }),
    importProject: vi.fn().mockRejectedValue(Object.assign(new Error('denied'), {
      code: 'project_root_not_readable',
      recovery: ['fix_permissions', 'choose_another_directory'],
    })),
  };
  renderProjects({ api });
  await user.click((await screen.findAllByRole('button', { name: 'Add project' }))[0]);
  const input = screen.getByLabelText('Project directory');
  await user.type(input, '/private/repo');
  await user.click(screen.getByRole('button', { name: 'Load project' }));

  expect(await screen.findByText('GoodTeam cannot read this directory.')).toBeVisible();
  expect(screen.getByText('Check directory permissions')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Choose another directory' }));
  expect(chooseLocalPath).toHaveBeenCalledTimes(1);
  expect(input).toHaveValue('/private/repo');
});

test('localizes unknown errors, ignores unknown recovery, and exposes the request ID', async () => {
  const user = userEvent.setup();
  const api = {
    listProjects: vi.fn().mockResolvedValue({ projects: [] }),
    importProject: vi.fn().mockRejectedValue(Object.assign(new Error('sensitive raw text'), {
      code: 'unmapped_backend_error',
      recovery: ['open_untrusted_url'],
      requestId: 'req-safe-42',
    })),
  };
  renderProjects({ api, language: 'zh-CN' });
  await user.click((await screen.findAllByRole('button', { name: '添加项目' }))[0]);
  await user.type(screen.getByLabelText('项目目录'), '/private/repo');
  await user.click(screen.getByRole('button', { name: '加载项目' }));

  expect(await screen.findByText('GoodTeam 无法完成此请求。')).toBeVisible();
  expect(screen.getByText('请求 ID：req-safe-42')).toBeVisible();
  expect(screen.queryByText(/unmapped_backend_error|open_untrusted_url|sensitive raw text/))
    .not.toBeInTheDocument();
});

test('runs origin recovery through the injected reload action', async () => {
  const user = userEvent.setup();
  const reloadPage = vi.fn();
  const api = {
    listProjects: vi.fn().mockResolvedValue({ projects: [] }),
    importProject: vi.fn().mockRejectedValue(Object.assign(new Error('origin'), {
      code: 'same_origin_required',
      recovery: ['retry_from_goodteam_origin'],
    })),
  };
  renderProjects({ api, reloadPage });
  await user.click((await screen.findAllByRole('button', { name: 'Add project' }))[0]);
  await user.type(screen.getByLabelText('Project directory'), '/work/demo');
  await user.click(screen.getByRole('button', { name: 'Load project' }));

  await user.click(await screen.findByRole('button', { name: 'Retry from the GoodTeam application' }));
  expect(reloadPage).toHaveBeenCalledTimes(1);
});

test.each([
  ['retry', 'Try again'],
  ['retry_request', 'Try again'],
  ['reload_project', 'Reload project'],
])('executes %s recovery by resubmitting the preserved root', async (recovery, label) => {
  const user = userEvent.setup();
  const navigate = vi.fn();
  const api = {
    listProjects: vi.fn().mockResolvedValue({ projects: [] }),
    importProject: vi.fn()
      .mockRejectedValueOnce(Object.assign(new Error('retry'), {
        code: 'internal_error',
        recovery: [recovery],
      }))
      .mockResolvedValueOnce({ project: { project_key: 'RETRIED' } }),
  };
  renderProjects({ api, navigate });
  await user.click((await screen.findAllByRole('button', { name: 'Add project' }))[0]);
  await user.type(screen.getByLabelText('Project directory'), '/work/retry');
  await user.click(screen.getByRole('button', { name: 'Load project' }));

  await user.click(await screen.findByRole('button', { name: label }));

  expect(api.importProject).toHaveBeenCalledTimes(2);
  expect(api.importProject).toHaveBeenLastCalledWith({ root: '/work/retry' });
  expect(navigate).toHaveBeenCalledWith('/projects/RETRIED/work');
});

test('return-to-projects recovery clears the inline error and focuses the preserved field', async () => {
  const user = userEvent.setup();
  const api = {
    listProjects: vi.fn().mockResolvedValue({ projects: [] }),
    importProject: vi.fn().mockRejectedValue(Object.assign(new Error('return'), {
      code: 'project_readiness_mismatch',
      recovery: ['return_to_projects'],
    })),
  };
  renderProjects({ api });
  await user.click((await screen.findAllByRole('button', { name: 'Add project' }))[0]);
  const input = screen.getByLabelText('Project directory');
  await user.type(input, '/work/return');
  await user.click(screen.getByRole('button', { name: 'Load project' }));

  await user.click(await screen.findByRole('button', { name: 'Return to projects' }));

  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(input).toHaveValue('/work/return');
  expect(input).toHaveFocus();
});

test.each([
  { label: 'missing project object', payload: { projects: [{}] } },
  {
    label: 'empty project key',
    payload: { projects: [project({ key: '', name: 'Broken', lifecycle: 'ready' })] },
  },
  {
    label: 'invalid root',
    payload: { projects: [{
      ...project({ key: 'BROKEN', name: 'Broken', lifecycle: 'ready' }),
      project: { project_key: 'BROKEN', display_name: 'Broken', root: null },
    }] },
  },
  {
    label: 'invalid readiness',
    payload: { projects: [{
      ...project({ key: 'BROKEN', name: 'Broken', lifecycle: 'ready' }),
      readiness: null,
    }] },
  },
  {
    label: 'invalid attention count',
    payload: { projects: [{
      ...project({ key: 'BROKEN', name: 'Broken', lifecycle: 'ready' }),
      attention_count: -1,
    }] },
  },
  {
    label: 'invalid active run',
    payload: { projects: [{
      ...project({ key: 'BROKEN', name: 'Broken', lifecycle: 'ready' }),
      active_run: { run_id: '', next_node: 'work' },
    }] },
  },
])('turns $label list payload into a stable list error', async ({ payload }) => {
  const api = {
    listProjects: vi.fn().mockResolvedValue(payload),
    importProject: vi.fn(),
  };
  renderProjects({ api });

  expect(await screen.findByRole('alert')).toHaveTextContent('GoodTeam could not complete this request.');
  expect(screen.queryByRole('button', { name: /Enter workspace:/ })).not.toBeInTheDocument();
});

test('normalizes unknown lifecycle to legacy and keeps the first duplicate key', () => {
  const payload = {
    projects: [
      project({ key: 'SAME', name: 'First', lifecycle: 'future_state' }),
      project({ key: 'SAME', name: 'Second', lifecycle: 'ready' }),
    ],
  };

  expect(normalizeProjectsPayload(payload)).toMatchObject([
    {
      project: { project_key: 'SAME', display_name: 'First' },
      readiness: { lifecycle: 'legacy' },
    },
  ]);
});

test('survives StrictMode list cancellation and renders only the current request', async () => {
  const signals = [];
  const api = {
    listProjects: vi.fn(({ signal }) => {
      signals.push(signal);
      return Promise.resolve({
        projects: [project({ key: 'CURRENT', name: 'Current', lifecycle: 'ready' })],
      });
    }),
    importProject: vi.fn(),
  };

  render(
    <StrictMode>
      <MemoryRouter>
        <ProjectsPage api={api} navigate={vi.fn()} language="en" />
      </MemoryRouter>
    </StrictMode>,
  );

  expect(await screen.findByRole('button', { name: 'Enter workspace: Current' })).toBeVisible();
  expect(api.listProjects).toHaveBeenCalledTimes(2);
  expect(signals[0].aborted).toBe(true);
  expect(signals[1].aborted).toBe(false);
});

test('retry replaces the list error with only the fresh normalized response', async () => {
  const user = userEvent.setup();
  const api = {
    listProjects: vi.fn()
      .mockRejectedValueOnce(new Error('first failed'))
      .mockResolvedValueOnce({
        projects: [project({ key: 'FRESH', name: 'Fresh', lifecycle: 'ready' })],
      }),
    importProject: vi.fn(),
  };
  renderProjects({ api });
  await screen.findByRole('alert');

  await user.click(screen.getByRole('button', { name: 'Try again' }));

  expect(await screen.findByRole('button', { name: 'Enter workspace: Fresh' })).toBeVisible();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('renders no first-slice dead navigation', async () => {
  const api = {
    listProjects: vi.fn().mockResolvedValue({ projects: [] }),
    importProject: vi.fn(),
  };
  renderProjects({ api });
  await screen.findByRole('heading', { name: 'Add your first project' });

  expect(screen.queryByRole('link', { name: /inbox/i })).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: /activity/i })).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: /settings/i })).not.toBeInTheDocument();
  expect(screen.queryAllByRole('link')).toHaveLength(0);
});

test('combines project search with attention filtering and recovers from empty results', async () => {
  const user = userEvent.setup();
  const api = { listProjects: vi.fn().mockResolvedValue({ projects: [
    project({ key: 'ALPHA', name: 'Alpha', lifecycle: 'ready', activeRun: { run_id: 'run-a', next_node: 'implementation' } }),
    project({ key: 'BETA', name: 'Beta', lifecycle: 'attention_required', attentionCount: 1, activeRun: { run_id: 'run-b', next_node: 'implementation' } }),
  ] }) };
  const { navigate } = renderProjects({ api });
  await user.click(await screen.findByRole('button', { name: 'Needs attention', exact: true }));
  expect(screen.getByRole('button', { name: 'Enter workspace: Beta' })).toHaveTextContent('Needs attention');
  expect(screen.queryByRole('button', { name: 'Enter workspace: Alpha' })).not.toBeInTheDocument();
  await user.type(screen.getByRole('searchbox', { name: 'Search projects by name or path' }), ' /work/alpha ');
  expect(screen.getByText('No matches. Try another search or clear your filters.')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Clear filters' }));
  expect(screen.getAllByRole('button', { name: /Enter workspace:/ })).toHaveLength(2);
  await user.click(screen.getByRole('button', { name: 'Active run', exact: true }));
  expect(screen.getAllByRole('button', { name: /Enter workspace:/ })).toHaveLength(2);
  expect(navigate).not.toHaveBeenCalled();
});

test('shows a short folder label and reveals the full project path without entering Work', async () => {
  const user = userEvent.setup();
  const api = { listProjects: vi.fn().mockResolvedValue({ projects: [project({ key: 'DEMO', name: 'Demo', lifecycle: 'ready' })] }) };
  const { navigate } = renderProjects({ api });
  expect(await screen.findByText('Local folder · demo')).toBeVisible();
  expect(screen.getByText('/work/demo')).not.toBeVisible();
  await user.click(screen.getByText('File location'));
  expect(screen.getByText('/work/demo')).toBeVisible();
  expect(navigate).not.toHaveBeenCalled();
});
