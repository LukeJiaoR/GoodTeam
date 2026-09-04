import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  createMemoryRouter,
  Link,
  RouterProvider,
  useOutletContext,
  useParams,
} from 'react-router-dom';
import { expect, test } from 'vitest';
import { createProductRoutes } from './AppRouter';

function ProjectsStub() {
  return (
    <main>
      <h1>Projects</h1>
      <Link to="/projects/DEMO/team">Demo</Link>
    </main>
  );
}

function WorkStub() {
  return <h1>Project work</h1>;
}

function TeamStub() {
  return <h1>Project team</h1>;
}

test('new project route renders the redesigned portfolio', async () => {
  const router = createMemoryRouter(
    createProductRoutes({ ProjectsComponent: ProjectsStub }),
    { initialEntries: ['/projects'] },
  );
  render(<RouterProvider router={router} />);

  expect(await screen.findByRole('heading', { name: 'Projects' })).toBeVisible();
});

test('root redirects to the redesigned project portfolio', async () => {
  const router = createMemoryRouter(
    createProductRoutes({ ProjectsComponent: ProjectsStub }),
    { initialEntries: ['/'] },
  );
  render(<RouterProvider router={router} />);

  expect(await screen.findByRole('heading', { name: 'Projects' })).toBeVisible();
  expect(router.state.location.pathname).toBe('/projects');
});

test('project root bookmarks enter the new Team workspace', async () => {
  const router = createMemoryRouter(
    createProductRoutes({ TeamComponent: TeamStub }),
    { initialEntries: ['/?project=DEMO%20SPACE'] },
  );
  render(<RouterProvider router={router} />);

  expect(await screen.findByRole('heading', { name: 'Project team' })).toBeVisible();
  expect(router.state.location.pathname).toBe('/projects/DEMO%20SPACE/team');
});

test('legacy compatibility route redirects into redesigned Work', async () => {
  const router = createMemoryRouter(
    createProductRoutes({ WorkComponent: WorkStub }),
    { initialEntries: ['/legacy?project=DEMO'] },
  );
  render(<RouterProvider router={router} />);

  expect(await screen.findByRole('heading', { name: 'Project work' })).toBeVisible();
  expect(router.state.location.pathname).toBe('/projects/DEMO/work');
});

test('browser back returns from Team to the project launcher', async () => {
  const user = userEvent.setup();
  const router = createMemoryRouter(
    createProductRoutes({ ProjectsComponent: ProjectsStub, TeamComponent: TeamStub }),
    { initialEntries: ['/projects'] },
  );
  render(<RouterProvider router={router} />);

  await user.click(await screen.findByRole('link', { name: 'Demo' }));
  expect(await screen.findByRole('heading', { name: 'Project team' })).toBeVisible();
  await router.navigate(-1);
  expect(await screen.findByRole('heading', { name: 'Projects' })).toBeVisible();
});

test('Work uses one project-level header without duplicate navigation or breadcrumbs', async () => {
  const router = createMemoryRouter(
    createProductRoutes({ WorkComponent: WorkStub }),
    { initialEntries: ['/projects/DEMO/work/runs/run-active'] },
  );
  const { container } = render(<RouterProvider router={router} />);

  expect(await screen.findByRole('heading', { name: 'Project work' })).toBeVisible();
  expect(container.querySelector('.product-shell')).toHaveClass('product-shell--work');
  expect(container.querySelector('.work-workspace-shell')).toContainElement(
    screen.getByRole('heading', { name: 'Project work' }),
  );
  expect(container.querySelector('.product-work-chrome')).toBeInTheDocument();
  expect(container.querySelector('.product-work-navigation')).toHaveAttribute('aria-label', 'Project navigation');
  expect(container.querySelectorAll('.product-work-navigation a')).toHaveLength(4);
  expect(container.querySelector('.product-work-navigation a.is-active')).toHaveAttribute('href', '/projects/DEMO/work');
  expect(screen.getByRole('link', { name: 'Team' })).toHaveAttribute('href', '/projects/DEMO/team');
  expect(screen.queryByRole('link', { name: 'Team settings' })).not.toBeInTheDocument();
  expect(container.querySelector('.product-work-tool--inbox')).not.toBeInTheDocument();
  expect(container.querySelector('.product-navigation')).not.toBeInTheDocument();
  expect(container.querySelector('.product-project-navigation')).not.toBeInTheDocument();
  expect(container.querySelector('.product-breadcrumb')).not.toBeInTheDocument();
});

test('Team uses the same project-level Work chrome', async () => {
  const router = createMemoryRouter(
    createProductRoutes({ TeamComponent: TeamStub }),
    { initialEntries: ['/projects/DEMO/team'] },
  );
  const { container } = render(<RouterProvider router={router} />);

  expect(await screen.findByRole('heading', { name: 'Project team' })).toBeVisible();
  expect(container.querySelector('.product-shell')).toHaveClass('product-shell--work');
  expect(container.querySelector('.product-work-navigation a.is-active')).toHaveAttribute('href', '/projects/DEMO/team');
  expect(container.querySelector('.product-project-navigation')).not.toBeInTheDocument();
});

test('the active Workbench navigation item controls the Work context drawer', async () => {
  const user = userEvent.setup();
  const router = createMemoryRouter(
    createProductRoutes({ WorkComponent: WorkStub }),
    { initialEntries: ['/projects/DEMO/work'] },
  );
  render(<RouterProvider router={router} />);

  const workbench = await screen.findByRole('link', { name: 'Workbench' });
  expect(workbench).toHaveAttribute('aria-controls', 'work-context-drawer');
  expect(workbench).toHaveAttribute('aria-expanded', 'false');

  await user.hover(workbench);
  expect(workbench).toHaveAttribute('aria-expanded', 'true');

  await user.unhover(workbench);
  await waitFor(() => expect(workbench).toHaveAttribute('aria-expanded', 'false'));
});

test.each([
  ['/inbox', '/projects'],
  ['/activity', '/projects'],
  ['/settings', '/projects'],
])('old global route %s returns to the project launcher', async (initialPath, destination) => {
  const router = createMemoryRouter(
    createProductRoutes({ ProjectsComponent: ProjectsStub }),
    { initialEntries: [initialPath] },
  );
  render(<RouterProvider router={router} />);

  expect(await screen.findByRole('heading', { name: 'Projects' })).toBeVisible();
  expect(router.state.location.pathname).toBe(destination);
});

test.each([
  '/projects/DEMO/home',
  '/projects/DEMO/settings/general',
])('old project route %s enters Team', async (initialPath) => {
  const router = createMemoryRouter(
    createProductRoutes({ TeamComponent: TeamStub }),
    { initialEntries: [initialPath] },
  );
  render(<RouterProvider router={router} />);

  expect(await screen.findByRole('heading', { name: 'Project team' })).toBeVisible();
  expect(router.state.location.pathname).toBe('/projects/DEMO/team');
});

test('the project launcher chrome excludes legacy global navigation', async () => {
  const router = createMemoryRouter(
    createProductRoutes({ ProjectsComponent: ProjectsStub }),
    { initialEntries: ['/projects'] },
  );
  const { container } = render(<RouterProvider router={router} />);

  expect(await screen.findByRole('heading', { name: 'Projects' })).toBeVisible();
  expect(container.querySelector('.product-shell')).toHaveClass('product-shell--projects');
  expect(container.querySelector('.product-navigation')).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: /Inbox|Activity|Settings/ })).not.toBeInTheDocument();
});

test('Team receives the decoded project key and active language through the new shell', async () => {
  const user = userEvent.setup();

  function RoutedTeam() {
    const { projectKey = '' } = useParams();
    const { language } = useOutletContext();
    return <h1>{`${projectKey}:${language}`}</h1>;
  }

  const router = createMemoryRouter(
    createProductRoutes({ TeamComponent: RoutedTeam }),
    { initialEntries: ['/projects/DEMO%20SPACE/team'] },
  );
  render(<RouterProvider router={router} />);

  expect(await screen.findByRole('heading', { name: 'DEMO SPACE:en' })).toBeVisible();

  const languageSelect = screen.getByRole('combobox', { name: 'Language' });
  expect(Array.from(languageSelect.options, (option) => option.value)).toEqual(['auto', 'en', 'zh-CN']);
  await user.selectOptions(languageSelect, 'zh-CN');
  expect(await screen.findByRole('heading', { name: 'DEMO SPACE:zh-CN' })).toBeVisible();
  expect(screen.getByRole('combobox', { name: '语言' })).toHaveValue('zh-CN');
});
