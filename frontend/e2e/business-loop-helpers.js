import { expect } from './fixtures.js';

export async function getJson(page, url) {
  const response = await page.request.get(url);
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}

export const workState = (page, key) => getJson(page, `/api/state?project=${key}`);

export async function clickMutation(page, button, endpoint) {
  const pending = page.waitForResponse(response => (
    response.request().method() === 'POST' && new URL(response.url()).pathname === endpoint
  ));
  await button.click();
  const response = await pending;
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}

export async function sendTask(page, fixture, text) {
  await page.goto(`/projects/${fixture.key}/work`);
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill(text);
  const receipt = await clickMutation(page, page.getByRole('button', { name: 'Send', exact: true }),
    `/api/projects/${fixture.key}/task-assistant/messages`);
  expect(receipt.accepted).toBe(true);
  await expect(page.getByRole('region', { name: 'Proposed task' })).toContainText(text);
  const { proposed_task: proposal } = (await workState(page, fixture.key)).task_assistant_session;
  expect(proposal).toMatchObject({ exact_request: text, confirmed: false, run_id: '' });
  return proposal;
}
