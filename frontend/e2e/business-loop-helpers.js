import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { expect } from './fixtures.js';

export async function getJson(page, url) {
  const response = await page.request.get(url);
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}

export const workState = (page, key) => getJson(page, `/api/state?project=${key}`);
export const decisions = (data) => data.action_items.filter((item) => item.action === 'approval_decided').reverse();

export async function clickMutation(page, button, endpoint) {
  const pending = page.waitForResponse((response) => (
    response.request().method() === 'POST' && new URL(response.url()).pathname === endpoint
  ));
  await button.click();
  const response = await pending;
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}

export async function snapshot(page, fixture, evidence) {
  const data = await workState(page, fixture.key);
  const stateRoot = path.join(process.env.GOODTEAM_E2E_ROOT, 'workspace', 'state', 'projects', fixture.key);
  if (data.state) {
    expect(JSON.parse(fs.readFileSync(path.join(stateRoot, 'current.json'), 'utf8'))).toEqual(data.state);
  }
  const logPath = path.join(stateRoot, 'action-log.jsonl');
  const ledger = fs.existsSync(logPath)
    ? fs.readFileSync(logPath, 'utf8').trim().split('\n').filter(Boolean).map((line) => JSON.parse(line)) : [];
  const durableDecisions = ledger.filter((item) => item.action === 'approval_decided');
  expect(decisions(data).map((item) => item.workflow_operation_id).sort())
    .toEqual(durableDecisions.map((item) => item.workflow_operation_id).sort());
  expect(new Set(durableDecisions.map((item) => item.workflow_operation_id)).size).toBe(durableDecisions.length);
  evidence.push({ state: data.state, session: data.task_assistant_session, ledger, worker_jobs: data.worker_jobs });
  return data;
}

export async function sendTask(page, fixture, text) {
  await page.goto(`/projects/${fixture.key}/work`);
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill(text);
  const result = await clickMutation(page, page.getByRole('button', { name: 'Send', exact: true }),
    `/api/projects/${fixture.key}/task-assistant/messages`);
  expect(result.workflow_started).toBe(false);
  expect(result.session.proposed_task.exact_request).toBe(text);
  expect(result.session.proposed_task.confirmed).toBe(false);
  expect(result.session.proposed_task.run_id).toBeFalsy();
  await expect(page.getByRole('region', { name: 'Proposed task' })).toContainText(text);
  return result.session.proposed_task;
}

export async function prepareFirstTask(page, fixture, evidence) {
  const imported = await getJson(page, `/api/projects/${fixture.key}/home`);
  expect(imported.readiness.project_mode).toBe('existing_project');
  const endpoint = `/api/projects/${fixture.key}/task-assistant/actions`;
  const started = await clickMutation(page, page.getByRole('button', { name: 'Start this task' }), endpoint);
  expect(started.workflow_started).toBe(false);
  expect(started.task_assistant_session.proposed_task.confirmed).toBe(true);
  expect((await snapshot(page, fixture, evidence)).state).toBeNull();
  await expect(page.getByRole('heading', { name: 'Which project commands should the team use for verification?' })).toBeVisible();
  await expect(page.getByRole('checkbox', { name: 'npm run test', exact: true })).toBeChecked();
  await clickMutation(page, page.getByRole('button', { name: 'Confirm and continue' }), endpoint);
  const preparation = await snapshot(page, fixture, evidence);
  expect(preparation.state).toBeNull();
  await expect(page.getByRole('heading', { name: 'Review the project constraints before continuing.' })).toBeVisible();
  const constraints = preparation.team_workspace.attention_request.input_requirements.constraints
    .filter((value) => typeof value === 'string' && value.trim());
  if (constraints.length) {
    await expect(page.locator('.team-preparation-facts li')).toHaveText(constraints);
  } else {
    await expect(page.getByText('No project constraints were returned. Confirm whether to continue.')).toBeVisible();
  }
  const result = await clickMutation(page, page.getByRole('button', { name: 'Confirm and continue' }), endpoint);
  expect(result.workflow_started).toBe(true);
  const readiness = await getJson(page, `/api/projects/${fixture.key}/home`);
  expect(readiness.readiness.lifecycle).toBe('ready');
  return result.run_id;
}

export async function expectGate(page, key, gate) {
  let data;
  await expect.poll(async () => {
    data = await workState(page, key);
    return data.state?.next_node;
  }).toBe(`await_${gate}_approval`);
  expect(data.state.blockers).toEqual([]);
  expect(data.approval_reviews.filter((review) => review.is_actionable).map((review) => review.approval)).toEqual([gate]);
  return data;
}

export async function approveGate(page, fixture, gate) {
  await expectGate(page, fixture.key, gate);
  await page.goto(`/projects/${fixture.key}/review/${gate}`);
  await expect(page.getByRole('heading', { name: 'Review', exact: true })).toBeVisible();
  const approve = page.getByRole('button', { name: 'Approve', exact: true });
  await expect(approve).toBeEnabled();
  return clickMutation(page, approve, '/api/approve');
}

export async function requestRevision(page, fixture, evidence) {
  const before = await snapshot(page, fixture, evidence);
  const note = 'Include an explicit empty-state acceptance criterion.';
  await page.goto(`/projects/${fixture.key}/work`);
  await page.getByRole('button', { name: 'Request changes', exact: true }).click();
  await page.getByRole('textbox', { name: 'What should change?' }).fill(note);
  await page.getByRole('button', { name: 'Review Task Assistant interpretation' }).click();
  await expect(page.getByRole('button', { name: 'Confirm changes', exact: true })).toBeEnabled();
  const draft = await snapshot(page, fixture, evidence);
  expect(decisions(draft)).toEqual(decisions(before));
  expect(draft.state).toEqual(before.state);
  await page.getByRole('button', { name: 'Confirm changes', exact: true }).click();
  await expect.poll(async () => decisions(await workState(page, fixture.key)).length).toBe(decisions(before).length + 1);
  await expectGate(page, fixture.key, 'rfc');
  const revised = await snapshot(page, fixture, evidence);
  expect(revised.state.change_requests.security).toBe(note);
  expect(revised.state.change_requests.rfc).toBe(note);
  expect(decisions(revised).at(-1)).toMatchObject({ gate: 'security', decision: 'changes_requested', note });
  await expect(page.getByRole('button', { name: 'Approve and continue', exact: true })).toBeEnabled();
}
