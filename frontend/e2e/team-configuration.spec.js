import { expect, importProject, test } from './fixtures.js';

test('choosing Codex for a new worker saves the default Sol model without dispatching work', async ({ page, fixture }) => {
  await importProject(page, fixture);
  await page.goto(`/projects/${fixture.key}/team`);
  await page.getByRole('button', { name: 'Add worker', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Role', { exact: true }).selectOption('research');
  await dialog.getByLabel('Name', { exact: true }).fill('Default Codex Worker');
  await dialog.getByLabel('Agent', { exact: true }).selectOption('codex');
  await expect(dialog.getByLabel('Model', { exact: true })).toHaveValue('gpt-5.6-sol');
  await dialog.getByRole('button', { name: 'Add worker', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await page.reload();
  const response = await page.request.get(`/api/state?project=${fixture.key}`);
  expect(response.ok()).toBeTruthy();
  const state = await response.json();
  const worker = state.team.roles.find((role) => role.role === 'research').workers.find((item) => item.name === 'Default Codex Worker');
  expect(worker).toMatchObject({ provider: 'codex', model: 'gpt-5.6-sol', reasoning_depth: 'default' });
  expect(state.active_run).toBeFalsy();
});

test('bulk settings persist and keep paused workers, individual instructions and other roles intact', async ({ page, fixture }) => {
  await importProject(page, fixture);
  await page.goto(`/projects/${fixture.key}/team`);
  const readTeam = async () => {
    const response = await page.request.get(`/api/state?project=${fixture.key}`);
    expect(response.ok()).toBeTruthy();
    return (await response.json()).team;
  };
  const team = await readTeam();
  const role = team.roles.find((item) => item.role === 'implementation');
  const worker = role.workers[0];
  const otherRoles = team.roles.filter((item) => item.role !== role.role);

  await expect(page.getByRole('article')).toHaveCount(0);
  await page.getByRole('button', { name: 'Show roles in Design & implementation', exact: true }).click();
  const roleRow = page.getByRole('article', { name: role.label, exact: true });
  await roleRow.getByRole('button', { name: `Pause ${role.label} / ${worker.name} (${worker.id})`, exact: true }).click();
  await expect(roleRow.getByRole('button', { name: `Activate ${role.label} / ${worker.name} (${worker.id})`, exact: true })).toBeVisible();
  await roleRow.getByRole('button', { name: `Edit ${role.label} / ${worker.name} (${worker.id})`, exact: true }).click();
  let dialog = page.getByRole('dialog');
  await dialog.getByText('Additional instructions (optional)', { exact: true }).click();
  await dialog.getByLabel('Additional instructions', { exact: true }).fill('Keep this worker-specific instruction.');
  await dialog.getByRole('button', { name: 'Save worker', exact: true }).click();
  await expect(dialog).toHaveCount(0);

  await page.getByRole('button', { name: 'Bulk configure', exact: true }).click();
  dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: 'Clear selection', exact: true }).click();
  await expect(dialog.getByRole('button', { name: 'Apply configuration', exact: true })).toBeDisabled();
  await dialog.getByRole('button', { name: 'Show roles in Design & implementation', exact: true }).click();
  await dialog.getByRole('checkbox', { name: /Implementation/ }).check();
  await dialog.getByLabel('Agent', { exact: true }).selectOption('fake');
  await dialog.getByRole('button', { name: 'Enter model ID', exact: true }).click();
  await dialog.getByLabel('Model', { exact: true }).fill('isolated-test-model');
  await expect(dialog.getByRole('region', { name: 'Changes to apply' })).toContainText('Update 1 workers · Add 0 workers');
  await dialog.getByRole('button', { name: 'Apply configuration', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await page.reload();
  await page.getByRole('button', { name: 'Show roles in Design & implementation', exact: true }).click();
  await expect(page.getByText('fake · isolated-test-model', { exact: true })).toBeVisible();

  const saved = await readTeam();
  expect(saved.roles.find((item) => item.role === role.role).workers[0]).toMatchObject({
    id: worker.id, name: worker.name, lifecycle: 'inactive', provider: 'fake', model: 'isolated-test-model',
    reasoning_depth: 'default', additional_prompt: 'Keep this worker-specific instruction.',
  });
  expect(saved.manager).toEqual(team.manager);
  expect(saved.roles.filter((item) => item.role !== role.role)).toEqual(otherRoles);
  const events = await page.request.get(`/api/projects/${fixture.key}/team/events`);
  const bulkEvents = (await events.json()).events.filter((event) => event.reason === 'Configure workers in selected roles');
  expect(bulkEvents).toHaveLength(1);
  expect(bulkEvents[0].target.id).toBe(worker.id);
  expect(fixture.errors).toEqual([]);
});
