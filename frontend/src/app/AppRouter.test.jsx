import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, Link, RouterProvider } from 'react-router-dom';
import { expect, test } from 'vitest';
import { createProductRoutes } from './AppRouter';

function ProjectsStub() {
  return (
    <main>
      <h1>Projects</h1>
      <Link to="/projects/DEMO/home">Demo</Link>
    </main>
  );
}

function HomeStub() {
  return <h1>Project home</h1>;
}

function WorkStub() {
  return <h1>Project work</h1>;
}

test('new project route renders the redesigned portfolio', async () => {
  const router = createMemoryRouter(
    createProductRoutes({ ProjectsComponent: ProjectsStub, ProjectHomeComponent: HomeStub }),
    { initialEntries: ['/projects'] },
  );
  render(<RouterProvider router={router} />);

  expect(await screen.findByRole('heading', { name: 'Projects' })).toBeVisible();
});

test('root redirects to the redesigned project portfolio', async () => {
  const router = createMemoryRouter(
    createProductRoutes({ ProjectsComponent: ProjectsStub, ProjectHomeComponent: HomeStub }),
    { initialEntries: ['/'] },
  );
  render(<RouterProvider router={router} />);

  expect(await screen.findByRole('heading', { name: 'Projects' })).toBeVisible();
  expect(router.state.location.pathname).toBe('/projects');
});

test('legacy root bookmarks redirect to the redesigned project Home', async () => {
  const router = createMemoryRouter(
    createProductRoutes({ ProjectsComponent: ProjectsStub, ProjectHomeComponent: HomeStub }),
    { initialEntries: ['/?project=DEMO%20SPACE'] },
  );
  render(<RouterProvider router={router} />);

  expect(await screen.findByRole('heading', { name: 'Project home' })).toBeVisible();
  expect(router.state.location.pathname).toBe('/projects/DEMO%20SPACE/home');
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

test('browser back returns from project Home to Projects', async () => {
  const user = userEvent.setup();
  const router = createMemoryRouter(
    createProductRoutes({ ProjectsComponent: ProjectsStub, ProjectHomeComponent: HomeStub }),
    { initialEntries: ['/projects'] },
  );
  render(<RouterProvider router={router} />);

  await user.click(await screen.findByRole('link', { name: 'Demo' }));
  expect(await screen.findByRole('heading', { name: 'Project home' })).toBeVisible();
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
  expect(container.querySelector('.product-work-chrome')).toBeInTheDocument();
  expect(container.querySelector('.product-work-navigation')).toHaveAttribute('aria-label', 'Project navigation');
  expect(container.querySelectorAll('.product-work-navigation a')).toHaveLength(5);
  expect(container.querySelector('.product-work-navigation a.is-active')).toHaveAttribute('href', '/projects/DEMO/work');
  expect(container.querySelector('.product-work-tool--inbox')).toHaveAttribute('href', '/inbox');
  expect(container.querySelector('.product-navigation')).not.toBeInTheDocument();
  expect(container.querySelector('.product-project-navigation')).not.toBeInTheDocument();
  expect(container.querySelector('.product-breadcrumb')).not.toBeInTheDocument();
});

test('project Home receives the decoded project key and active language', async () => {
  const user = userEvent.setup();

  function RoutedHome({ language, projectKey }) {
    return <h1>{`${projectKey}:${language}`}</h1>;
  }

  const router = createMemoryRouter(
    createProductRoutes({ ProjectsComponent: ProjectsStub, ProjectHomeComponent: RoutedHome }),
    { initialEntries: ['/projects/DEMO%20SPACE/home'] },
  );
  render(<RouterProvider router={router} />);

  expect(await screen.findByRole('heading', { name: 'DEMO SPACE:en' })).toBeVisible();

  const languageSelect = screen.getByRole('combobox', { name: 'Language' });
  expect(Array.from(languageSelect.options, (option) => option.value)).toEqual(['auto', 'en', 'zh-CN']);
  await user.selectOptions(languageSelect, 'zh-CN');
  expect(await screen.findByRole('heading', { name: 'DEMO SPACE:zh-CN' })).toBeVisible();
  expect(screen.getByRole('combobox', { name: '语言' })).toHaveValue('zh-CN');
});
