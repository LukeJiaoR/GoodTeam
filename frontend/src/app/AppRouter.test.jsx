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

test('new project route renders the redesigned portfolio', async () => {
  const router = createMemoryRouter(
    createProductRoutes({ ProjectsComponent: ProjectsStub, ProjectHomeComponent: HomeStub }),
    { initialEntries: ['/projects'] },
  );
  render(<RouterProvider router={router} />);

  expect(await screen.findByRole('heading', { name: 'Projects' })).toBeVisible();
});

test('legacy root still renders the current application', async () => {
  const router = createMemoryRouter(createProductRoutes(), { initialEntries: ['/?project=DEMO'] });
  render(<RouterProvider router={router} />);

  expect(await screen.findByTestId('legacy-app-root')).toBeInTheDocument();
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
