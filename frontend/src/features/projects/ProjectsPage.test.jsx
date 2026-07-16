import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { ApiError, requestJson } from '../../lib/api/client';
import ProjectsPage from './ProjectsPage';
import { importProject } from './projectApi';

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

function renderProjects({ api, language = 'en', navigate = vi.fn() } = {}) {
  const result = render(
    <MemoryRouter>
      <ProjectsPage api={api} navigate={navigate} language={language} />
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

test('shows a shape-matching skeleton, then groups projects in product priority order', async () => {
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
      project({ key: 'PREP', name: 'Preparing', lifecycle: 'preparing' }),
      project({
        key: 'RUNNING',
        name: 'Running',
        lifecycle: 'ready',
        activeRun: { run_id: 'run-7', next_node: 'implementation' },
      }),
      project({
        key: 'ATTENTION',
        name: 'Attention',
        lifecycle: 'attention_required',
        attentionCount: 2,
        activeRun: { run_id: 'run-8', next_node: 'approval' },
      }),
    ],
  });

  const groups = await screen.findAllByTestId('project-group');
  expect(groups.map((group) => within(group).getByRole('heading', { level: 3 }).textContent))
    .toEqual(['Needs attention', 'Active runs', 'Preparing', 'Ready', 'Legacy projects']);
  expect(screen.getAllByRole('article')).toHaveLength(5);
});

test('renders a localized empty portfolio after the project list resolves', async () => {
  const api = {
    listProjects: vi.fn().mockResolvedValue({ projects: [] }),
    importProject: vi.fn(),
  };

  renderProjects({ api, language: 'zh-CN' });

  expect(await screen.findByRole('heading', { name: '尚未加载项目' })).toBeVisible();
  expect(screen.getByText('选择一个本地目录以添加首个项目。')).toBeVisible();
});

test('loads a selected project once and navigates to its server home URL', async () => {
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

  await user.type(await screen.findByLabelText('Project directory'), '/tmp/demo');
  await user.click(screen.getByRole('button', { name: 'Load project' }));

  expect(api.importProject).toHaveBeenCalledTimes(1);
  expect(api.importProject).toHaveBeenCalledWith({ root: '/tmp/demo' });
  expect(navigate).toHaveBeenCalledWith('/projects/DEMO/home');
});

test('uses the directory picker and writes its selection into the absolute path field', async () => {
  const user = userEvent.setup();
  const api = {
    listProjects: vi.fn().mockResolvedValue({ projects: [] }),
    importProject: vi.fn(),
  };
  renderProjects({ api });
  const input = await screen.findByLabelText('Project directory');
  await user.type(input, '/work/current');

  await user.click(screen.getByRole('button', { name: 'Browse' }));

  expect(chooseLocalPath).toHaveBeenCalledTimes(1);
  const options = chooseLocalPath.mock.calls[0][0];
  expect(options).toMatchObject({ mode: 'directory', initialPath: '/work/current' });
  act(() => options.onSelected('/work/selected'));
  expect(input).toHaveValue('/work/selected');
});

test('navigates duplicate imports to the returned Home URL', async () => {
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

  await user.type(await screen.findByLabelText('Project directory'), '/work/known');
  await user.click(screen.getByRole('button', { name: 'Load project' }));

  expect(navigate).toHaveBeenCalledWith('/projects/KNOWN/home');
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
  const input = await screen.findByLabelText('Project directory');
  await user.type(input, '/private/repo');
  await user.click(screen.getByRole('button', { name: 'Load project' }));

  expect(await screen.findByText('GoodTeam cannot read this directory.')).toBeVisible();
  expect(screen.getByText('Check directory permissions')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Choose another directory' })).toBeVisible();
  expect(input).toHaveValue('/private/repo');
});

test('renders no first-slice dead navigation', async () => {
  const api = {
    listProjects: vi.fn().mockResolvedValue({ projects: [] }),
    importProject: vi.fn(),
  };
  renderProjects({ api });
  await screen.findByRole('heading', { name: 'No projects loaded' });

  expect(screen.queryByRole('link', { name: /inbox/i })).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: /activity/i })).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: /settings/i })).not.toBeInTheDocument();
  expect(screen.queryAllByRole('link')).toHaveLength(0);
});
