import { execFileSync } from 'node:child_process';
import path from 'node:path';
import process from 'node:process';
import { test, expect, importProject } from './fixtures.js';
import { clickMutation, getJson, sendTask, workState } from './business-loop-helpers.js';

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
  expect(result).toMatchObject({ execution_started: true });
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

  const second = await sendTask(page, fixture, 'Build a task activity filter.');
  const next = await confirmDraft(page, fixture.key, second);
  expect(next.run_id).not.toBe(original.run_id);
  const final = await workState(page, fixture.key);
  expect(final.task_assistant_session.project_task_batches[original.run_id]?.status).toBe('review');
  expect(git(fixture.root, 'rev-parse', 'HEAD')).toBe(baseline);
  expect(git(fixture.root, 'diff', '--name-only', baseline)).toBe('');
});

test('one click starts the displayed Manager recommendation and reuses its batch on replay', async ({ page, fixture }) => {
  git(fixture.root, 'init', '-q');
  git(fixture.root, 'add', '.');
  git(fixture.root, '-c', 'user.name=GoodTeam fixture', '-c', 'user.email=fixture@example.invalid',
    '-c', 'commit.gpgSign=false', 'commit', '-qm', 'Initial recommendation fixture');
  await importProject(page, fixture);
  const e2eRoot = process.env.GOODTEAM_E2E_ROOT;
  // 通过真实计划服务保存建议，模型选择由本地夹具提供。
  const choice = JSON.parse(execFileSync(process.env.GOODTEAM_E2E_PYTHON || '../.venv/bin/python', ['-c', `
import json
from pathlib import Path
import sys
from goodteam_os.project_iteration import project_iteration_backlog
from goodteam_os.project_iteration_planner import plan_next_iteration
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.web import _project_config_for_key

workspace, root, key = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
task = LocalTaskStore(root, key).create_task(title="Check the project entry point",
    description="Inspect the isolated fixture.", role="implementation")
result = plan_next_iteration(workspace=workspace, project_key=key, root=root,
    project_config=_project_config_for_key(workspace, key),
    expected_backlog_version=project_iteration_backlog(root, key)["version"],
    confirmed=True, manager_runner=lambda **_: {"selection": {
        "selected_task_ids": [task["id"]], "compared_task_ids": [task["id"]],
        "rationale": "Start with the eligible original task from the project backlog."}})
print(json.dumps(result["iteration_choice"]))
`, path.join(e2eRoot, 'workspace'), fixture.root, fixture.key], { encoding: 'utf8', env: {
    ...process.env,
    PYTHONPATH: path.resolve('../src'),
    GOODTEAM_HOME: path.join(e2eRoot, 'home'),
    GOODTEAM_STATE_DIR: path.join(e2eRoot, 'operator-state'),
    GOODTEAM_PROJECTS_REGISTRY: path.join(e2eRoot, 'home', 'projects.json'),
  } }));
  await page.reload();
  await page.locator('.project-conversation-planning > summary').click();
  const before = (await workState(page, fixture.key)).task_assistant_session;
  expect(before.queued_task_proposals ?? []).toEqual([]);
  expect(Object.keys(before.project_task_batches ?? {})).toEqual([]);
  const endpoint = `/api/projects/${fixture.key}/task-assistant/actions`;
  const first = await clickMutation(page, page.getByRole('button', { name: 'Run selected tasks now (uses model)' }), endpoint);
  expect(first.execution_started).toBe(true);
  await expect.poll(async () => (await workState(page, fixture.key)).task_assistant_session
    .project_task_batches[first.run_id]?.status, { timeout: 30_000 }).toBe('review');
  const replay = await page.request.post(endpoint, { data: {
    action: 'start_iteration_choice', expected_choice_event_id: choice.source_event_id, confirmed: true,
  } });
  expect(replay.status()).toBe(202);
  expect((await replay.json()).run_id).toBe(first.run_id);
  const final = await workState(page, fixture.key);
  expect(Object.keys(final.task_assistant_session.project_task_batches)).toEqual([first.run_id]);
  const report = await getJson(page, `/api/projects/${fixture.key}/iteration-batches/${first.run_id}`);
  expect(report).toMatchObject({ all_ok: true, jobs: [{ project_task_id: choice.selected_task_id, outcome: 'ok' }] });
});
