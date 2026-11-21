import { execFileSync } from 'node:child_process';
import { test, expect, importProject } from './fixtures.js';
import { clickMutation, getJson, workState } from './business-loop-helpers.js';

function git(root, ...args) {
  return execFileSync('git', ['-C', root, ...args], { encoding: 'utf8' }).trim();
}

async function prepareTask(page, key, text) {
  const endpoint = `/api/projects/${key}/task-assistant/actions`;
  await page.getByRole('button', { name: 'New task', exact: true }).click();
  await page.getByRole('textbox', { name: 'New task', exact: true }).fill(text);
  const saved = await clickMutation(page, page.getByRole('button', { name: 'Save task', exact: true }), endpoint);
  const queued = saved.session.queued_task_proposals.find(item => item.exact_request === text);
  expect(queued).toMatchObject({ status: 'queued', confirmed: false, run_id: '' });
  await page.getByRole('button', { name: 'Manage tasks', exact: true }).click();
  const selected = await clickMutation(page, page.getByRole('button', { name: 'Prepare this draft', exact: true }), endpoint);
  const proposal = selected.session.proposed_task;
  expect(proposal).toMatchObject({ id: queued.id, exact_request: text, confirmed: false });
  return proposal;
}

async function confirmDraft(page, key, proposal) {
  const start = page.getByRole('button', { name: 'Start this task', exact: true });
  await expect(start).toBeEnabled();
  const result = await clickMutation(page, start, `/api/projects/${key}/task-assistant/actions`);
  expect(result).toMatchObject({ execution_started: true, workflow_started: false });
  const runId = result.run_id;
  const readBatch = async () => (await workState(page, key)).task_assistant_session.project_task_batches?.[runId];
  await expect.poll(async () => (await readBatch())?.status, { timeout: 30_000 }).toBe('review');
  const batch = await readBatch();
  expect(batch).toMatchObject({ selected_proposal_id: proposal.id, proposal_ids: [proposal.id], confirmed: true });
  const report = await getJson(page, `/api/projects/${key}/iteration-batches/${runId}`);
  expect(report).toMatchObject({
    run_id: batch.dispatch_run_id,
    all_ok: true,
    jobs: [{ project_task_id: batch.task_ids[0], outcome: 'ok', attribution_status: 'captured' }],
  });
  return batch;
}

test('business loop: confirm two project tasks and inspect their isolated Worker results', async ({ page, fixture }) => {
  git(fixture.root, 'init', '-q');
  git(fixture.root, 'add', '.');
  git(fixture.root, '-c', 'user.name=GoodTeam fixture', '-c', 'user.email=fixture@example.invalid',
    '-c', 'commit.gpgSign=false', 'commit', '-qm', 'Initial acceptance fixture');
  const baseline = git(fixture.root, 'rev-parse', 'HEAD');
  await importProject(page, fixture);
  const first = await prepareTask(page, fixture.key, 'Implement a searchable project list.');
  const existingProject = page.getByRole('radio', { name: 'Existing project', exact: true });
  await expect(existingProject).toBeVisible();
  await existingProject.check();
  await clickMutation(page, page.getByRole('button', { name: 'Confirm project type', exact: true }),
    `/api/projects/${fixture.key}/task-assistant/actions`);

  const unconfirmed = await workState(page, fixture.key);
  expect(unconfirmed.worker_jobs).toEqual([]);
  expect(Object.keys(unconfirmed.task_assistant_session.project_task_batches || {})).toEqual([]);
  const original = await confirmDraft(page, fixture.key, first);

  const second = await prepareTask(page, fixture.key, 'Build a task activity filter.');
  const next = await confirmDraft(page, fixture.key, second);
  expect(next.run_id).not.toBe(original.run_id);
  const final = await workState(page, fixture.key);
  expect(final.task_assistant_session.project_task_batches[original.run_id]?.status).toBe('review');
  expect(git(fixture.root, 'rev-parse', 'HEAD')).toBe(baseline);
  expect(git(fixture.root, 'diff', '--name-only', baseline)).toBe('');
});
