import fs from 'node:fs';
import { test, expect, importProject } from './fixtures.js';
import { approveGate, clickMutation, expectGate, getJson, prepareFirstTask, sendTask, workState } from './business-loop-helpers.js';

const artifact = (page, key, name) => getJson(page, `/api/artifact?project=${key}&key=${name}`);
const proposalFields = (binding) => ({
  expected_run_id: binding.run_id, expected_artifact_key: binding.artifact_key,
  expected_artifact_version: binding.base_version, expected_proposal_id: binding.proposal_id,
  expected_proposal_version: binding.proposal_version,
});

async function startAtPrd(page, fixture) {
  await importProject(page, fixture);
  await sendTask(page, fixture, 'Implement a searchable project list with explicit empty-state acceptance criteria.');
  await prepareFirstTask(page, fixture, []);
  await approveGate(page, fixture, 'takeover');
  await expectGate(page, fixture.key, 'prd');
  await page.goto(`/projects/${fixture.key}/review/prd`);
}

async function propose(page, fixture) {
  const result = await clickMutation(page, page.getByRole('button', { name: 'Propose refinement', exact: true }), '/api/refine-artifact');
  const current = await artifact(page, fixture.key, 'prd_proposed');
  expect(current.proposal_binding).toEqual(result.proposal_binding);
  const heading = current.content.split('\n').find((line) => line.startsWith('# ')).slice(2);
  await expect(page.locator('.review-proposal-content')).toContainText(heading);
  return current;
}

test('real HTTP proposals: replacement in another tab, exact accept/discard, and stale-tab isolation', async ({ page, context, fixture }) => {
  await startAtPrd(page, fixture);
  const first = await propose(page, fixture);
  const other = await context.newPage();
  await other.goto(`/projects/${fixture.key}/review/prd`);
  const replacement = await propose(other, fixture);
  expect(replacement.proposal_binding.proposal_id).not.toBe(first.proposal_binding.proposal_id);

  const before = await workState(page, fixture.key);
  const stale = await page.request.post('/api/accept-refinement', { data: {
    project_key: fixture.key, artifact: 'prd', ...proposalFields(first.proposal_binding),
  } });
  expect(stale.status()).toBe(400);
  expect((await stale.json()).message).toContain('proposal changed');
  expect((await workState(page, fixture.key)).state).toEqual(before.state);

  const acceptedResponse = other.waitForResponse((response) => response.url().endsWith('/api/accept-refinement'));
  await other.getByRole('button', { name: 'Accept revision', exact: true }).click();
  const accepted = await acceptedResponse;
  expect(accepted.ok(), await accepted.text()).toBe(true);
  expect(accepted.request().postDataJSON()).toEqual({
    project_key: fixture.key, artifact: 'prd', ...proposalFields(replacement.proposal_binding),
  });
  await expect(other.locator('.review-proposal')).toHaveCount(0);
  expect((await artifact(page, fixture.key, 'prd')).content).toBe(replacement.content);

  await page.bringToFront();
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await expect.poll(async () => {
    const button = page.getByRole('button', { name: 'Accept revision', exact: true });
    return await button.count() === 0 || !await button.isEnabled();
  }).toBe(true);

  const discarded = await propose(other, fixture);
  const discardResponse = other.waitForResponse((response) => response.url().endsWith('/api/discard-refinement'));
  await other.getByRole('button', { name: 'Discard proposal', exact: true }).click();
  const response = await discardResponse;
  expect(response.ok(), await response.text()).toBe(true);
  expect(response.request().postDataJSON()).toEqual({
    project_key: fixture.key, artifact: 'prd', ...proposalFields(discarded.proposal_binding),
  });
  await expect(other.locator('.review-proposal')).toHaveCount(0);
  await other.close();
});

test('intermediate edit blocks release until an upstream revision is reviewed and delivery is regenerated', async ({ page, fixture }) => {
  await startAtPrd(page, fixture);
  for (const gate of ['prd', 'rfc', 'security']) await approveGate(page, fixture, gate);
  const release = await expectGate(page, fixture.key, 'release');
  const oldQa = await artifact(page, fixture.key, 'qa_report');
  const editedContent = `${oldQa.content}\nHuman QA correction: explicitly verify the empty state.\n`;
  const editedResponse = await page.request.post('/api/artifact', { data: {
    project_key: fixture.key, artifact: 'qa_report', content: editedContent,
    expected_run_id: oldQa.run_id, expected_artifact_version: oldQa.artifact_version,
  } });
  expect(editedResponse.ok(), await editedResponse.text()).toBe(true);
  const edited = await editedResponse.json();
  const blocked = await workState(page, fixture.key);
  expect(blocked.state.next_node).toBe('blocked');
  expect(blocked.state.approvals.release).toBe('pending');
  expect(blocked.approval_reviews.some((review) => review.is_actionable)).toBe(false);
  expect(fs.readFileSync(oldQa.path, 'utf8')).toBe(oldQa.content);
  expect(fs.readFileSync(edited.path, 'utf8')).toBe(editedContent);
  await page.goto(`/projects/${fixture.key}/work`);
  await expect(page.getByText('Move the required changes into the upstream RFC and save a new revision. Review RFC and security again to regenerate delivery. The edited artifact remains in history.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Retry failed stage', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Confirm manual continuation', exact: true })).toHaveCount(0);
  const oldRelease = release.approval_reviews.find((review) => review.approval === 'release');
  const stale = await page.request.post('/api/approve', { data: {
    project_key: fixture.key, approval: 'release', decision: 'approved',
    expected_run_id: release.state.run_id, expected_target_type: 'review', expected_target_id: 'release',
    expected_artifact_key: oldRelease.artifact, expected_artifact_version: oldRelease.artifact_version,
  } });
  expect(stale.ok()).toBe(false);
  expect((await stale.json()).message).toContain('not currently actionable');

  await page.getByRole('link', { name: 'Revise upstream RFC', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${fixture.key}/review/rfc$`));
  await page.getByRole('button', { name: 'Edit Source', exact: true }).click();
  const editor = page.getByRole('textbox', { name: 'Artifact content', exact: true });
  const rfc = await artifact(page, fixture.key, 'rfc');
  await editor.fill(`${rfc.content}\nAcceptance criterion: QA must explicitly verify the empty state.\n`);
  await clickMutation(page, page.getByRole('button', { name: 'Save artifact', exact: true }), '/api/artifact');
  await expectGate(page, fixture.key, 'rfc');
  expect((await workState(page, fixture.key)).state.artifacts.qa_report).toBeUndefined();
  await approveGate(page, fixture, 'rfc');
  await expectGate(page, fixture.key, 'security');
  await approveGate(page, fixture, 'security');
  const regenerated = await expectGate(page, fixture.key, 'release');
  expect(regenerated.state.artifacts.qa_report).not.toBe(edited.path);
  expect(fs.readFileSync(edited.path, 'utf8')).toBe(editedContent);
  expect(fs.readFileSync(oldQa.path, 'utf8')).toBe(oldQa.content);
});
