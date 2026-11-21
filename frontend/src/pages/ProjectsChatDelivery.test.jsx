import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ToastProvider } from '../components/ui/ToastProvider';
import { WorkspaceProvider } from '../context/WorkspaceContext';
import ProjectsPage, { SAFE_REMOVE_WARNING } from './ProjectsPage';
import ChatPage from './ChatPage';
import DeliveryPage from './DeliveryPage';
import AutopilotBar from '../components/AutopilotBar';
import BacklogSidebar from '../components/BacklogSidebar';

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderWithRouter(ui, { initialEntries = ['/projects'], routePath = '/projects', projectId = 'proj-1' } = {}) {
  return render(
    <ToastProvider>
      <WorkspaceProvider initialProjectId={projectId}>
        <MemoryRouter initialEntries={initialEntries}>
          <Routes>
            <Route path={routePath} element={ui} />
            <Route path="/projects/:projectId/chat" element={<div>Chat Route</div>} />
            <Route path="/projects/:projectId/delivery" element={<div>Delivery Route</div>} />
          </Routes>
        </MemoryRouter>
      </WorkspaceProvider>
    </ToastProvider>,
  );
}

describe('ProjectsPage, ChatPage, and DeliveryPage', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('ProjectsPage renders doctor status and shows explicit safe-remove warning before deleting', async () => {
    const fetchMock = vi.fn(async (url, init = {}) => {
      if (url === '/api/projects' && (!init.method || init.method === 'GET')) {
        return jsonResponse({
          projects: [
            {
              id: 'proj-1',
              name: 'DemoRepo',
              path: '/tmp/demo-repo',
              default_branch: 'main',
              active_runs: 1,
            },
          ],
        });
      }
      if (url === '/api/doctor') {
        return jsonResponse({
          state_dir: '/tmp/state',
          codex: { available: true, version: 'codex 0.171.0' },
          claude: { available: true, version: 'claude 2.1.0' },
          git: { available: true, version: 'git 2.45.0' },
          gh: { available: true, authenticated: true, account: 'octocat', version: 'gh 2.50.0' },
        });
      }
      if (url === '/api/projects/proj-1' && init.method === 'DELETE') {
        return jsonResponse({ deleted: true, id: 'proj-1' });
      }
      return jsonResponse({});
    });
    globalThis.fetch = fetchMock;

    renderWithRouter(<ProjectsPage />);

    expect(await screen.findByText('DemoRepo')).toBeInTheDocument();
    expect(screen.getByText('1 个运行中')).toBeInTheDocument();
    expect(await screen.findByText(/已登录 @octocat/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /移除项目 DemoRepo/ }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(SAFE_REMOVE_WARNING)).toBeInTheDocument();
    expect(within(dialog).getByText('/tmp/demo-repo')).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: '确认移除记录' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/projects/proj-1',
        expect.objectContaining({ method: 'DELETE' }),
      );
    });
  });

  it('ChatPage renders Manager conversation, Sub-agent RunCard, Autopilot start/stop, and Backlog', async () => {
    const fetchMock = vi.fn(async (url, init = {}) => {
      if (url === '/api/projects') {
        return jsonResponse({
          projects: [{ id: 'proj-1', name: 'DemoRepo', path: '/tmp/demo', default_branch: 'main' }],
        });
      }
      if (url === '/api/projects/proj-1/messages' && (!init.method || init.method === 'GET')) {
        return jsonResponse({
          messages: [
            { id: 'msg-1', role: 'user', content: '请实现登录表单', attachments: [], created_at: '2026-11-21T00:00:00Z' },
            { id: 'msg-2', role: 'manager', content: '已派发 **Coder** 处理。', attachments: [], meta: { status: 'completed' }, created_at: '2026-11-21T00:00:02Z' },
          ],
        });
      }
      if (url === '/api/projects/proj-1/runs') {
        return jsonResponse({
          runs: [
            {
              id: 'run-1',
              project_id: 'proj-1',
              parent_message_id: 'msg-2',
              role_key: 'coder',
              adapter: 'codex',
              model: 'gpt-5.4',
              execution_policy: 'workspace_write',
              status: 'running',
              changed_files: ['src/login.js'],
              scope_ok: true,
              summary: '正在编写登录组件',
            },
          ],
        });
      }
      if (url === '/api/runs/run-1' && (!init.method || init.method === 'GET')) {
        return jsonResponse({
          run: { id: 'run-1' },
          events: [{ seq: 1, kind: 'tool', payload: { message: 'Edited src/login.js' } }],
          diff_text: '',
        });
      }
      if (url === '/api/runs/run-1/stop' && init.method === 'POST') {
        return jsonResponse({
          run: { id: 'run-1', status: 'stopped', role_key: 'coder', adapter: 'codex', changed_files: ['src/login.js'] },
        });
      }
      if (url === '/api/projects/proj-1/tasks') {
        return jsonResponse({ tasks: [{ id: 'task-1', title: '编写测试', status: 'todo' }] });
      }
      if (url === '/api/projects/proj-1/autopilot') {
        return jsonResponse({ status: 'idle', running: false, round: 0, max_rounds: 5 });
      }
      if (url === '/api/projects/proj-1/autopilot/start' && init.method === 'POST') {
        return jsonResponse({ status: 'running', running: true, round: 1, max_rounds: 3, objective: '补齐单测' });
      }
      if (url === '/api/projects/proj-1/autopilot/stop' && init.method === 'POST') {
        return jsonResponse({ status: 'stopped', running: false, round: 1, max_rounds: 3, objective: '补齐单测' });
      }
      if (url === '/api/projects/proj-1/deliveries') {
        return jsonResponse({ deliveries: [] });
      }
      return jsonResponse({});
    });
    globalThis.fetch = fetchMock;

    renderWithRouter(<ChatPage />, {
      initialEntries: ['/projects/proj-1/chat'],
      routePath: '/projects/:projectId/chat',
    });

    expect(await screen.findByText('Sub-agent · coder')).toBeInTheDocument();
    expect(screen.getByText('1 个改动文件')).toBeInTheDocument();
    expect(screen.getByText('前往交付审阅')).toBeInTheDocument();

    // Expand run card to load live logs
    fireEvent.click(screen.getByRole('button', { name: /Sub-agent · coder/ }));
    expect(await screen.findByText(/Edited src\/login\.js/)).toBeInTheDocument();

    // Stop active run
    fireEvent.click(screen.getByRole('button', { name: '停止运行' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/runs/run-1/stop',
        expect.objectContaining({ method: 'POST' }),
      );
    });

    // Start & Stop Autopilot
    fireEvent.change(screen.getByLabelText('多轮续跑目标'), { target: { value: '补齐单测' } });
    fireEvent.change(screen.getByLabelText('最大轮次'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: '启动续跑' }));

    expect(await screen.findByText('自主续跑中')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '停止续跑' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/projects/proj-1/autopilot/stop',
        expect.objectContaining({ method: 'POST' }),
      );
    });
  });

  it('DeliveryPage renders colorized diff, loud scope_ok=false warning, and Merge / PR / Discard controls', async () => {
    const fetchMock = vi.fn(async (url, init = {}) => {
      if (url === '/api/projects') {
        return jsonResponse({
          projects: [{ id: 'proj-1', name: 'DemoRepo', path: '/tmp/demo', default_branch: 'main' }],
        });
      }
      if (url === '/api/projects/proj-1/deliveries') {
        return jsonResponse({
          deliveries: [
            {
              id: 'deliv-1',
              project_id: 'proj-1',
              branch_name: 'goodteam/run-1',
              base_ref: 'main',
              status: 'pending',
              role_key: 'coder',
              scope_ok: false,
              summary: 'Added auth helper',
              changed_files: ['src/auth.py'],
              diff_text: 'diff --git a/src/auth.py b/src/auth.py\n@@ -1,2 +1,2 @@\n-old_line()\n+new_line()',
            },
          ],
        });
      }
      if (url === '/api/deliveries/deliv-1/merge' && init.method === 'POST') {
        return jsonResponse({ merge_commit: 'abc1234' });
      }
      if (url === '/api/deliveries/deliv-1/pr' && init.method === 'POST') {
        return jsonResponse({ pr: { url: 'https://github.com/org/repo/pull/42' } });
      }
      return jsonResponse({});
    });
    globalThis.fetch = fetchMock;

    renderWithRouter(<DeliveryPage />, {
      initialEntries: ['/projects/proj-1/delivery'],
      routePath: '/projects/:projectId/delivery',
    });

    expect(await screen.findByText(/检测到超出允许写域的文件改动/)).toBeInTheDocument();
    expect(screen.getByTestId('diff-viewer')).toBeInTheDocument();
    expect(screen.getByText('+new_line()')).toHaveClass('v2-diff__line--add');
    expect(screen.getByText('-old_line()')).toHaveClass('v2-diff__line--del');

    // Merge button
    fireEvent.click(screen.getByRole('button', { name: '合入本地主分支' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/deliveries/deliv-1/merge',
        expect.objectContaining({ method: 'POST' }),
      );
    });

    // Open PR form and submit
    fireEvent.click(screen.getByRole('button', { name: '推分支并创建 PR' }));
    fireEvent.change(screen.getByLabelText('PR 标题'), { target: { value: 'feat: auth helper' } });
    fireEvent.click(screen.getByRole('button', { name: '确认推送并创建 PR' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/deliveries/deliv-1/pr',
        expect.objectContaining({ method: 'POST' }),
      );
    });
  });

  it('BacklogSidebar deduplicates SSE+HTTP created tasks and AutopilotBar blocks start while stopping', async () => {
    const fetchMock = vi.fn(async (url, init = {}) => {
      if (url === '/api/projects/proj-1/tasks' && init.method === 'POST') {
        return jsonResponse({ task: { id: 'task-dup', title: '已由SSE插入的任务', status: 'todo' } }, 201);
      }
      return jsonResponse({});
    });
    globalThis.fetch = fetchMock;

    let currentTasks = [{ id: 'task-dup', title: '已由SSE插入的任务', status: 'todo' }];
    const onTasksChange = vi.fn((updater) => {
      currentTasks = typeof updater === 'function' ? updater(currentTasks) : updater;
    });

    render(
      <ToastProvider>
        <AutopilotBar autopilot={{ status: 'stopping', running: false, round: 2, max_rounds: 5, objective: '收尾' }} />
        <BacklogSidebar
          projectId="proj-1"
          tasks={currentTasks}
          onTasksChange={onTasksChange}
          onClose={() => {}}
        />
      </ToastProvider>,
    );

    // AutopilotBar hides start button & disables stop button while stopping
    expect(screen.queryByRole('button', { name: '启动续跑' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '正在停止' })).toBeDisabled();

    // Creating a task that already arrived via SSE does not duplicate it
    const input = screen.getByLabelText('新建待办任务');
    fireEvent.change(input, { target: { value: '已由SSE插入的任务' } });
    fireEvent.submit(input.closest('form'));

    await waitFor(() => expect(onTasksChange).toHaveBeenCalled());
    expect(currentTasks).toHaveLength(1);
    expect(currentTasks[0].id).toBe('task-dup');
  });
});
