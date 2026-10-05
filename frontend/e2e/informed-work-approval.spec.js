import { test, expect, importProject } from './fixtures.js';
import { clickMutation, decisions, expectGate, prepareFirstTask, sendTask, snapshot, workState } from './business-loop-helpers.js';

// Real HTTP and ledger; fake Workers only. This is not a #411 real-task PASS.
test('fake Work approval requires a reason and records it against the exact review', async ({ page, fixture }, testInfo) => {
  const evidence = [];
  try {
    await importProject(page, fixture);
    await sendTask(page, fixture, 'Implement a searchable project list.');
    await prepareFirstTask(page, fixture, evidence);
    const before = await expectGate(page, fixture.key, 'takeover');
    const brief = before.team_workspace.attention_request.approval_brief;
    expect(brief.recommendation.verdict).not.toBe('approve');
    await page.goto(`/projects/${fixture.key}/work`);
    const button = page.getByRole('button', { name: 'Confirm manual continuation', exact: true });
    const reason = page.getByRole('textbox', { name: 'Why are you continuing despite these evidence gaps?' });
    await expect(button).toBeDisabled();
    await reason.fill('   ');
    await reason.press('Control+Enter');
    expect(decisions(await workState(page, fixture.key))).toEqual(decisions(before));
    await page.reload();
    await expect(reason).toHaveValue('');
    await expect(button).toBeDisabled();

    const note = 'Fake-only acceptance: I understand the reviewer is simulated and continue to test the approval contract.';
    await reason.fill(note);
    const requestPromise = page.waitForRequest(request => request.method() === 'POST' && new URL(request.url()).pathname === '/api/approve');
    await clickMutation(page, button, '/api/approve');
    const request = await requestPromise;
    expect(request.postDataJSON()).toMatchObject({
      project_key: fixture.key, approval: 'takeover', decision: 'approved', note,
      expected_run_id: brief.approval_target.run_id,
      expected_target_type: brief.approval_target.target_type,
      expected_target_id: brief.approval_target.target_id,
      expected_artifact_key: brief.approval_target.artifact_key,
      expected_artifact_version: brief.approval_target.artifact_version,
    });
    await expectGate(page, fixture.key, 'prd');
    const after = await snapshot(page, fixture, evidence);
    expect(decisions(after)).toHaveLength(decisions(before).length + 1);
    expect(decisions(after).at(-1)).toMatchObject({ gate: 'takeover', decision: 'approved', note });
  } finally {
    await testInfo.attach('informed-work-approval-fake-evidence', {
      body: JSON.stringify({ source: 'fake-only', project: fixture.key, snapshots: evidence }, null, 2),
      contentType: 'application/json',
    });
  }
});
