import fs from 'node:fs';
import path from 'node:path';
import { test, expect, importProject } from './fixtures.js';
import {
  approveGate, clickMutation, decisions, expectGate, getJson, prepareFirstTask,
  requestRevision, sendTask, snapshot, workState,
} from './business-loop-helpers.js';

const firstRequest = 'Implement a searchable project list.';
const secondRequest = 'Build a task activity filter.';

async function completeFirstTask(page, fixture, evidence) {
  const proposal = await sendTask(page, fixture, firstRequest);
  const unconfirmed = await snapshot(page, fixture, evidence);
  expect(unconfirmed.state).toBeNull();
  expect(unconfirmed.worker_jobs).toEqual([]);
  expect(decisions(unconfirmed)).toEqual([]);
  const runId = await prepareFirstTask(page, fixture, evidence);
  expect(runId).toBe(`task-${proposal.id}`);
  await approveGate(page, fixture, 'takeover');
  await expectGate(page, fixture.key, 'prd');
  await approveGate(page, fixture, 'prd');
  await approveGate(page, fixture, 'rfc');
  await expectGate(page, fixture.key, 'security');
  await requestRevision(page, fixture, evidence);
  for (const gate of ['rfc', 'security', 'release']) {
    await test.step(`Approve ${gate} through the review page`, async () => {
      await approveGate(page, fixture, gate);
      await snapshot(page, fixture, evidence);
    });
  }
  const completed = await snapshot(page, fixture, evidence);
  expect(completed.state).toMatchObject({ run_id: runId, current_node: 'complete', next_node: '', blockers: [] });
  expect(completed.active_run).toBe(false);
  expect(completed.approval_reviews.some((review) => review.is_actionable)).toBe(false);
  expect(decisions(completed).map(({ gate, decision }) => [gate, decision])).toEqual([
    ['takeover', 'approved'],
    ['prd', 'approved'], ['rfc', 'approved'], ['security', 'changes_requested'], ['rfc', 'approved'],
    ['security', 'approved'], ['release', 'approved'],
  ]);
  expect(completed.worker_jobs.length).toBeGreaterThan(0);
  expect(completed.worker_jobs.every((job) => job.adapter === 'fake' && job.status === 'completed')).toBe(true);
  return { proposal, completed, runId };
}

async function startSecondTask(page, fixture, first) {
  const proposal = await sendTask(page, fixture, secondRequest);
  expect(proposal.id).not.toBe(first.proposal.id);
  const draft = await workState(page, fixture.key);
  expect(draft.state).toEqual(first.completed.state);
  expect(draft.task_assistant_session.archived_task_proposals).toEqual([
    expect.objectContaining({ id: first.proposal.id, run_id: first.runId, terminal_status: 'complete', exact_request: firstRequest }),
  ]);
  const result = await clickMutation(page, page.getByRole('button', { name: 'Start this task' }),
    `/api/projects/${fixture.key}/task-assistant/actions`);
  expect(result.workflow_started).toBe(true);
  expect(result.run_id).toBe(`task-${proposal.id}`);
  expect(result.run_id).not.toBe(first.runId);
  const current = await expectGate(page, fixture.key, 'takeover');
  expect(current.state.idea).toBe(secondRequest);
  expect(current.state.change_requests).toEqual({});
  expect(current.task_assistant_session.proposed_task.run_id).toBe(result.run_id);
  return result.run_id;
}

async function verifyHistory(page, fixture, { first, secondRunId }) {
  const history = await getJson(page, `/api/projects/${fixture.key}/runs`);
  expect(history.runs.map(({ run_id, status }) => ({ run_id, status }))).toEqual([
    { run_id: secondRunId, status: 'active' }, { run_id: first.runId, status: 'archived' },
  ]);
  const archived = await getJson(page, `/api/projects/${fixture.key}/runs/${first.runId}`);
  expect(archived.state).toEqual(first.completed.state);
  await page.goto(`/projects/${fixture.key}/work/runs/${first.runId}`);
  await expect(page.getByText('Historical runs are read-only and visually separate from the active run.')).toBeVisible();
  await expect(page.getByRole('heading', { name: firstRequest, exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Approve and continue', exact: true })).toHaveCount(0);
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('heading', { name: firstRequest, exact: true })).toBeVisible();
  const current = await workState(page, fixture.key);
  expect(current.state.run_id).toBe(secondRunId);
  expect(decisions(current)).toEqual(decisions(first.completed));
}

test('business loop: import, confirm, prepare, revise, reapprove, complete, and start a second task', async ({ page, fixture }, testInfo) => {
  const evidence = [];
  try {
    await test.step('Import an isolated project through the UI', () => importProject(page, fixture));
    const first = await test.step('Complete the first task with an explicit revision', () => completeFirstTask(page, fixture, evidence));
    const secondRunId = await test.step('Submit and explicitly start a second task', () => startSecondTask(page, fixture, first));
    await test.step('Verify the old run and current task remain separate', () => verifyHistory(page, fixture, { first, secondRunId }));
    await snapshot(page, fixture, evidence);
    expect(fs.readFileSync(path.join(fixture.root, 'src', 'main.js'), 'utf8')).toBe("export const fixture = 'read-only source';\n");
  } finally {
    await testInfo.attach('business-loop-evidence', {
      body: JSON.stringify({ project: fixture.key, snapshots: evidence }, null, 2), contentType: 'application/json',
    });
  }
});
