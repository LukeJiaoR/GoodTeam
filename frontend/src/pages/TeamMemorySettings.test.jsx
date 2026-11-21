import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ToastProvider } from '../components/ui/ToastProvider';
import { WorkspaceProvider } from '../context/WorkspaceContext';
import TeamSkillsPage from './TeamSkillsPage';
import MemorySettingsPage from './MemorySettingsPage';

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderPage(ui, { initialEntry, routePath }) {
  return render(
    <ToastProvider>
      <WorkspaceProvider initialProjectId="proj-1">
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route path={routePath} element={ui} />
          </Routes>
        </MemoryRouter>
      </WorkspaceProvider>
    </ToastProvider>,
  );
}

describe('TeamSkillsPage and MemorySettingsPage', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('TeamSkillsPage supports Manager skills, Role provider options, GitHub + zip preview/install, and encoded skill IDs', async () => {
    const fetchMock = vi.fn(async (url, init = {}) => {
      if (url === '/api/projects') {
        return jsonResponse({ projects: [{ id: 'proj-1', name: 'Demo', path: '/tmp/demo' }] });
      }
      if (url === '/api/projects/proj-1/roles') {
        return jsonResponse({
          roles: [
            {
              key: 'coder',
              name: 'Coder',
              description: 'Writes code',
              execution_policy: 'workspace_write',
              write_domains: ['src/'],
              allowed_tools: [],
              adapter: 'codex',
              provider_id: null,
              model: 'gpt-5.4',
              reasoning_effort: 'high',
              skill_bindings: [{ skill_id: 'builtin:coding', mode: 'required' }],
              is_builtin: true,
            },
          ],
        });
      }
      if (url === '/api/projects/proj-1/manager-settings') {
        return jsonResponse({
          settings: {
            model: '',
            reasoning_effort: 'high',
            additional_prompt: '',
            provider_id: null,
            skill_bindings: [{ skill_id: 'builtin:coding', mode: 'required' }],
          },
        });
      }
      if (url === '/api/providers') {
        return jsonResponse({
          active: {},
          providers: [
            { id: 'prov-1', agent: 'codex', name: 'CorpProxy', base_url: 'https://proxy.example/v1', model: 'gpt-5.4', has_api_key: true },
          ],
        });
      }
      if (url === '/api/skills') {
        return jsonResponse({
          skills: [
            { id: 'builtin:coding', name: 'Coding', description: 'Built-in coding skill', source_type: 'builtin', commit_sha: '' },
            { id: 'github:alpha-skill', name: 'Alpha Skill', description: 'Remote skill', source_type: 'github', commit_sha: 'abcdef123456' },
          ],
        });
      }
      if (url === '/api/skills/github/preview' && init.method === 'POST') {
        return jsonResponse({
          commit_sha: '1234567890',
          skills: [{ rel_path: 'skills/alpha', name: 'Alpha Skill', description: 'Preview desc', file_count: 2 }],
        });
      }
      if (url === '/api/skills/github/install' && init.method === 'POST') {
        return jsonResponse({ skills: [{ id: 'github:alpha-skill', name: 'Alpha Skill' }] }, 201);
      }
      if (url === '/api/skills/zip/preview' && init.method === 'POST') {
        return jsonResponse({
          skills: [{ rel_path: 'bundle-skill', name: 'Bundle Skill', description: 'From zip', file_count: 1 }],
        });
      }
      if (url === '/api/skills/zip/install' && init.method === 'POST') {
        return jsonResponse({ skills: [{ id: 'zip:bundle-skill', name: 'Bundle Skill' }] }, 201);
      }
      if (url === '/api/skills/github%3Aalpha-skill/content') {
        return jsonResponse({ id: 'github:alpha-skill', content: '# Alpha Skill\n\nFollow rules.' });
      }
      return jsonResponse({});
    });
    globalThis.fetch = fetchMock;

    renderPage(<TeamSkillsPage />, {
      initialEntry: '/projects/proj-1/team',
      routePath: '/projects/:projectId/team',
    });

    // Tab 1: Manager Skills + Role Editor modal provider dropdown
    expect(await screen.findByText('Manager 自身绑定的 Skills')).toBeInTheDocument();
    expect(await screen.findByText('Coder')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '新建自定义角色' }));
    const providerSelect = await screen.findByLabelText('Provider');
    expect(providerSelect).toHaveTextContent('跟随全局默认');
    expect(providerSelect).toHaveTextContent('本机 CLI 登录态');
    expect(providerSelect).toHaveTextContent('CorpProxy');
    fireEvent.click(screen.getByRole('button', { name: '取消' }));

    // Switch to Tab 2: Skill 能力库
    fireEvent.click(screen.getByRole('tab', { name: /Skill 能力库/ }));
    expect(await screen.findByText('GitHub 链接安装')).toBeInTheDocument();

    // GitHub preview + install
    fireEvent.change(screen.getByLabelText('GitHub 仓库链接或 owner/repo'), {
      target: { value: 'acme/skills-pack' },
    });
    fireEvent.click(screen.getByRole('button', { name: '预览' }));
    expect(await screen.findByText('Preview desc')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '安装已选 (1)' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/skills/github/install',
        expect.objectContaining({ method: 'POST' }),
      );
    });

    // Zip upload preview + install
    const zipInput = screen.getByTestId('zip-upload-input');
    const fakeZip = new File(['PK\x03\x04test'], 'pack.zip', { type: 'application/zip' });
    fireEvent.change(zipInput, { target: { files: [fakeZip] } });
    expect(await screen.findByText('From zip')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '安装已选 (1)' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/skills/zip/install',
        expect.objectContaining({ method: 'POST' }),
      );
    });

    // View SKILL.md uses URL-encoded skill_id (github%3Aalpha-skill)
    const viewButtons = screen.getAllByRole('button', { name: '查看 SKILL.md' });
    fireEvent.click(viewButtons[1]);
    expect(await screen.findByText('Follow rules.')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/skills/github%3Aalpha-skill/content',
      expect.any(Object),
    );
  });

  it('MemorySettingsPage masks Provider API keys, switches back to native CLI auth, and saves memory', async () => {
    const fetchMock = vi.fn(async (url, init = {}) => {
      if (url === '/api/projects') {
        return jsonResponse({ projects: [{ id: 'proj-1', name: 'Demo', path: '/tmp/demo' }] });
      }
      if (url === '/api/projects/proj-1/memory') {
        return jsonResponse({
          files: [{ filename: 'project.md', exists: true, bytes: 32 }],
        });
      }
      if (url === '/api/projects/proj-1/memory?filename=project.md') {
        return jsonResponse({ filename: 'project.md', content: '# Project Notes' });
      }
      if (url === '/api/projects/proj-1/memory' && init.method === 'PUT') {
        return jsonResponse({ filename: 'project.md', bytes: 40, content: '# Updated Notes' });
      }
      if (url === '/api/providers') {
        return jsonResponse({
          active: { codex: 'prov-1' },
          providers: [
            {
              id: 'prov-1',
              agent: 'codex',
              name: 'CustomGateway',
              base_url: 'https://gw.example/v1',
              model: 'gpt-5.4',
              has_api_key: true,
            },
          ],
        });
      }
      if (url === '/api/providers/activate' && init.method === 'POST') {
        return jsonResponse({
          active: {},
          providers: [
            {
              id: 'prov-1',
              agent: 'codex',
              name: 'CustomGateway',
              base_url: 'https://gw.example/v1',
              model: 'gpt-5.4',
              has_api_key: true,
            },
          ],
        });
      }
      if (url === '/api/providers/test' && init.method === 'POST') {
        return jsonResponse({ ok: true, model_count: 4, sample_models: ['gpt-5.4'] });
      }
      if (url === '/api/projects/proj-1/manager-settings') {
        return jsonResponse({
          settings: { model: 'gpt-5.4', reasoning_effort: 'high', additional_prompt: '', provider_id: null, skill_bindings: [] },
        });
      }
      if (url.startsWith('/api/usage')) {
        return jsonResponse({
          usage: [{ adapter: 'codex', model: 'gpt-5.4', provider_id: 'prov-1', calls: 3, input_tokens: 1200, cached_input_tokens: 200, output_tokens: 450 }],
        });
      }
      if (url === '/api/doctor') {
        return jsonResponse({
          state_dir: '/tmp/state',
          codex: { available: true, version: '0.171.0' },
          claude: { available: true, version: '2.1.0' },
          git: { available: true, version: '2.45.0' },
          gh: { available: true, authenticated: true, account: 'dev' },
        });
      }
      return jsonResponse({});
    });
    globalThis.fetch = fetchMock;

    renderPage(<MemorySettingsPage />, {
      initialEntry: '/projects/proj-1/settings',
      routePath: '/projects/:projectId/settings',
    });

    // Provider masking: shows 已配置密钥 badge and password-type API key input
    expect(await screen.findByText('已配置密钥')).toBeInTheDocument();
    const keyInput = screen.getByLabelText(/API Key/);
    expect(keyInput).toHaveAttribute('type', 'password');
    expect(keyInput).toHaveValue('');

    // Switch back to native CLI login
    fireEvent.click(screen.getByRole('button', { name: '切回本机 CLI 登录态' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/providers/activate',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ agent: 'codex', provider_id: null }),
        }),
      );
    });

    // Edit & save memory
    const memorySection = screen.getByLabelText('项目记忆管理');
    expect(await within(memorySection).findByText('Project Notes')).toBeInTheDocument();
    fireEvent.click(within(memorySection).getByRole('button', { name: '编辑' }));
    fireEvent.change(within(memorySection).getByLabelText('编辑记忆 Markdown 内容'), {
      target: { value: '# Updated Notes' },
    });
    fireEvent.click(within(memorySection).getByRole('button', { name: '保存文档' }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/projects/proj-1/memory',
        expect.objectContaining({ method: 'PUT' }),
      );
    });

    // Token usage table renders row + totals row
    const usageSection = screen.getByLabelText('Token 用量统计');
    const tokenCells = await within(usageSection).findAllByText('1,200');
    expect(tokenCells).toHaveLength(2);
  });
});
