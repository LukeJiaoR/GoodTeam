import { test, expect, importProject } from './fixtures.js';

test('project search and filters survive reload and Back, with native new-tab navigation', async ({ page, context, fixture }) => {
  await importProject(page, fixture);
  await page.goto('/projects?source=bookmark');
  const search = page.getByRole('searchbox');
  await search.fill(fixture.name);
  await expect(page).toHaveURL(new RegExp(`q=${fixture.name}`));
  const project = page.getByRole('link', { name: `Enter workspace: ${fixture.name}` });
  const popupPromise = context.waitForEvent('page');
  await project.click({ modifiers: ['ControlOrMeta'] });
  const popup = await popupPromise;
  await expect(popup).toHaveURL(new RegExp(`/projects/${fixture.key}/work$`));
  await popup.close();
  await page.getByRole('group', { name: 'Filter results' }).getByRole('button', { name: 'Active run', exact: true }).click();
  await expect(page).toHaveURL(/filter=active/);
  await expect(page.getByText('No matches. Try another search or clear your filters.')).toBeVisible();
  await page.reload();
  await expect(search).toHaveValue(fixture.name);
  await expect(page.getByRole('button', { name: 'Active run', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: 'Clear filters', exact: true }).click();
  await expect(page).toHaveURL(/\/projects\?source=bookmark$/);
  await page.goBack();
  await expect(search).toHaveValue(fixture.name);
  await page.goBack();
  await expect(project).toBeVisible();
  await project.click();
  await expect(page).toHaveURL(new RegExp(`/projects/${fixture.key}/work$`));
  await page.goBack();
  await expect(search).toHaveValue(fixture.name);
});

test('shared workspace surfaces remain usable at phone, tablet and desktop widths', async ({ page, fixture }) => {
  await importProject(page, fixture);
  for (const width of [375, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    for (const section of ['work', 'team', 'review', 'knowledge', 'settings']) {
      await page.goto(`/projects/${fixture.key}/${section}`);
      await expect(section === 'work'
        ? page.getByRole('heading', { name: 'Task Assistant', exact: true })
        : page.locator('main h1')).toBeVisible();
      await expect(page.getByRole('link', { name: 'Projects', exact: true })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
  }
  await page.goto(`/projects/${fixture.key}/settings`);
  await page.getByRole('button', { name: 'Dark', exact: true }).click();
  await expect(page.locator('html')).toHaveClass(/dark/);
  expect(await page.locator('html').evaluate(node => getComputedStyle(node).colorScheme)).toBe('dark');
  await page.getByRole('combobox', { name: 'Language', exact: true }).last().selectOption('zh-CN');
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN');
  // The shared fixture deliberately restores English on every navigation.
  await page.reload();
  await expect(page.locator('html')).toHaveClass(/dark/);
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
});
