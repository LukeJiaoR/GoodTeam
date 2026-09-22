import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { test, expect, importProject } from './fixtures.js';
import { approveGate, clickMutation, prepareFirstTask, sendTask, snapshot, workState } from './business-loop-helpers.js';

// This is a real HTTP/browser capture and blocked-dispatch test, not a fake
// Skill loading success. The fixture's first legacy task only prepares the
// project; the second task must carry a nonempty authoritative method lock.
test('confirmed custom-method task persists its lock and cannot execute through the legacy fake path', async ({ page, fixture }, testInfo) => {
  const evidence = [];
  try {
    await importProject(page, fixture);
    await sendTask(page, fixture, 'Prepare the project for a separate method-bound task.');
    await prepareFirstTask(page, fixture, evidence);
    for (const gate of ['takeover', 'prd', 'rfc', 'security', 'release']) {
      await approveGate(page, fixture, gate);
    }
    expect((await workState(page, fixture.key)).state.current_node).toBe('complete');

    const skillName = `locked-method-${fixture.name.replace(/[^a-z0-9]/gi, '').slice(-10).toLowerCase()}`;
    const source = path.join(process.env.GOODTEAM_E2E_ROOT, 'skills', skillName);
    fs.mkdirSync(source, { recursive: true });
    fs.writeFileSync(path.join(source, 'SKILL.md'), [
      '---', `name: ${skillName}`, 'description: Task-bound review evidence.', '---',
      'Review the exact task and preserve its original evidence.', '',
    ].join('\n'));
    await page.goto(`/projects/${fixture.key}/team`);
    await page.getByRole('button', { name: 'Skills', exact: true }).click();
    let dialog = page.getByRole('dialog', { name: 'Skills', exact: true });
    await dialog.getByLabel('Server directory').fill(source);
    await clickMutation(page, dialog.getByRole('button', { name: 'Preview', exact: true }), '/api/skills/local/preview');
    const installed = await clickMutation(page, dialog.getByRole('button', { name: 'Install Skill', exact: true }), '/api/skills/local/install');
    await dialog.locator('footer').getByRole('button', { name: 'Close Skills', exact: true }).click();
    await page.getByRole('button', { name: 'Show roles in Quality & delivery', exact: true }).click();
    let qa = page.getByRole('article', { name: 'QA', exact: true });
    await qa.getByRole('button', { name: 'Methods', exact: true }).click();
    dialog = page.getByRole('dialog', { name: 'QA methods', exact: true });
    await dialog.getByRole('radio', { name: /Custom Skills/ }).check();
    await dialog.getByRole('checkbox', { name: new RegExp(skillName) }).check();
    await clickMutation(page, dialog.getByRole('button', { name: 'Save methods', exact: true }),
      `/api/projects/${fixture.key}/team/commands`);
    await expect(qa.getByText('Saved only · execution not connected', { exact: true })).toBeVisible();

    const proposal = await sendTask(page, fixture, 'Review using the explicitly selected task methods.');
    const started = await clickMutation(page, page.getByRole('button', { name: 'Start this task', exact: true }),
      `/api/projects/${fixture.key}/task-assistant/actions`);
    expect(started.workflow_started).toBe(true);
    const bound = await snapshot(page, fixture, evidence);
    const context = bound.state.task_skill_context;
    expect(context.schema_version).toBe('task_skill_context.v1');
    expect(context.task).toEqual({ kind: 'workflow_run', project_key: fixture.key, logical_id: `task-${proposal.id}` });
    expect(bound.task_assistant_session.proposed_task.task_skill_context).toEqual(context);
    expect(bound.state.blockers.length).toBeGreaterThan(0);
    expect(bound.state.current_node).not.toBe('complete');
    const jobs = bound.worker_jobs.filter((job) => job.run_id === context.task.logical_id);
    expect(jobs.length).toBeGreaterThan(0);
    for (const job of jobs) {
      expect(job.id).toMatch(/^job-skill-/);
      expect(job.status).toBe('failed');
      expect(job.task_skill_context).toEqual(context);
      expect(job.skill_runtime_status).toBe('not_connected');
      expect(job.invocation_evidence.external_invocation).toBe(false);
    }
    // Inspect immutable evidence only; do not seed or repair application state.
    const lockRoot = path.join(process.env.GOODTEAM_E2E_ROOT, 'operator-state', 'skills', 'task-locks');
    const namespaces = fs.readdirSync(lockRoot).filter((name) => fs.statSync(path.join(lockRoot, name)).isDirectory());
    const objects = namespaces.map((name) => path.join(lockRoot, name, 'objects', `${context.task_lock_ref.digest.slice(7)}.json`));
    const objectPath = objects.find((candidate) => fs.existsSync(candidate));
    expect(objectPath).toBeTruthy();
    const lock = JSON.parse(fs.readFileSync(objectPath, 'utf8'));
    expect(lock.subjects['role:qa'].items[0].version_id).toBe(installed.installed.version_id);

    await page.goto(`/projects/${fixture.key}/team`);
    await page.getByRole('button', { name: 'Show roles in Quality & delivery', exact: true }).click();
    qa = page.getByRole('article', { name: 'QA', exact: true });
    await qa.getByRole('button', { name: 'Methods', exact: true }).click();
    dialog = page.getByRole('dialog', { name: 'QA methods', exact: true });
    await dialog.getByRole('radio', { name: /GoodTeam default methods/ }).check();
    await clickMutation(page, dialog.getByRole('button', { name: 'Restore default methods', exact: true }),
      `/api/projects/${fixture.key}/team/commands`);
    await page.goto(`/projects/${fixture.key}/work`);
    await page.reload();
    const restored = await snapshot(page, fixture, evidence);
    expect(restored.state.task_skill_context).toEqual(context);
    expect(restored.team.roles.find((role) => role.role === 'qa').skill_binding.mode).toBe('default');
    expect(JSON.parse(fs.readFileSync(objectPath, 'utf8'))).toEqual(lock);
    await testInfo.attach('method-bound-task-blocked', { body: await page.screenshot(), contentType: 'image/png' });
    expect(fixture.errors).toEqual([]);
  } finally {
    await testInfo.attach('task-skill-capture-evidence', {
      body: JSON.stringify({ project: fixture.key, snapshots: evidence }, null, 2), contentType: 'application/json',
    });
  }
});
