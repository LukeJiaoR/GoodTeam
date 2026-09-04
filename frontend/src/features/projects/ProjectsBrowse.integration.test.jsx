import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, test, vi } from 'vitest';
import ProjectsPage from './ProjectsPage';

afterEach(() => {
  vi.unstubAllGlobals();
});

function response(payload, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  }));
}

function renderWithRealPicker(fetchMock) {
  vi.stubGlobal('fetch', fetchMock);
  const api = {
    listProjects: vi.fn().mockResolvedValue({ projects: [] }),
    importProject: vi.fn(),
  };
  render(
    <MemoryRouter>
      <ProjectsPage api={api} navigate={vi.fn()} language="zh-CN" />
    </MemoryRouter>,
  );
}

test('real Browse fallback localizes the dialog, traps focus, closes on Escape, and restores focus', async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn((url) => {
    if (url === '/api/fs/choose') return response({ status: 'unsupported' });
    if (String(url).startsWith('/api/fs/list')) {
      return response({ path: '/workspace', parent: '/', entries: [] });
    }
    throw new Error(`Unexpected fetch: ${url}`);
  });
  renderWithRealPicker(fetchMock);
  await user.click((await screen.findAllByRole('button', { name: '添加项目' }))[0]);
  const browse = screen.getByRole('button', { name: '浏览' });

  await user.click(browse);

  const dialog = await screen.findByRole('dialog', { name: '选择项目根目录' });
  expect(dialog).toHaveAccessibleDescription('浏览本地目录并选择要加载到 GoodTeam 的项目根目录。');
  const close = screen.getByRole('button', { name: '关闭目录浏览器' });
  expect(close).toHaveFocus();

  await user.tab({ shift: true });
  expect(screen.getByRole('button', { name: '选择此文件夹' })).toHaveFocus();
  await user.keyboard('{Escape}');

  expect(screen.queryByRole('dialog', { name: '选择项目根目录' })).not.toBeInTheDocument();
  expect(screen.getByRole('dialog', { name: '加载本地项目' })).toBeVisible();
  expect(browse).toHaveFocus();
});

test('real localized picker selection updates only the project path callback', async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn((url) => {
    if (url === '/api/fs/choose') return response({ status: 'unsupported' });
    if (String(url).startsWith('/api/fs/list')) {
      return response({ path: '/workspace/chosen', parent: '/workspace', entries: [] });
    }
    throw new Error(`Unexpected fetch: ${url}`);
  });
  renderWithRealPicker(fetchMock);
  await user.click((await screen.findAllByRole('button', { name: '添加项目' }))[0]);
  const browse = screen.getByRole('button', { name: '浏览' });

  await user.click(browse);
  await user.click(await screen.findByRole('button', { name: '选择此文件夹' }));

  expect(screen.getByLabelText('项目目录')).toHaveValue('/workspace/chosen');
  expect(screen.queryByRole('dialog', { name: '选择项目根目录' })).not.toBeInTheDocument();
  expect(screen.getByRole('dialog', { name: '加载本地项目' })).toBeVisible();
  expect(browse).toHaveFocus();
});
