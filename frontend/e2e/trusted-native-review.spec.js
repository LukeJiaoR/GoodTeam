import path from 'node:path';
import process from 'node:process';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { test, expect } from './fixtures.js';

test('default page starts a bounded native-shaped review and opens its durable report', async ({ page, fixture }, testInfo) => {
  const child = spawn(process.env.GOODTEAM_E2E_PYTHON || '../.venv/bin/python', [
    '../tests/fixtures/trusted_native_browser_server.py', path.resolve('dist'),
  ], { env: { ...process.env, PYTHONPATH: path.resolve('../src'), PYTHONDONTWRITEBYTECODE: '1' } });
  let output = '', errors = '';
  child.stdout.on('data', data => { output += data; });
  child.stderr.on('data', data => { errors += data; });
  try {
    await expect.poll(() => output.includes('\n') ? output : errors, { timeout: 15000 }).toContain('"url"');
    const start = JSON.parse(output.split('\n')[0]);
    expect(start.real_model_calls).toBe(0);
    expect(start.native_codex).toBe(false);
    const endpoint = `${start.url}/api/projects/DEMO/runs/${start.run_id}/skill-reviews`;
    await page.goto(`${start.url}/projects/DEMO/work`);
    await page.getByRole('button', { name: 'Review execution details', exact: true }).click();
    const panel = page.getByRole('region', { name: 'Review execution details', exact: true });
    const response = page.waitForResponse(item => new URL(item.url()).pathname === '/api/reviewer-jobs/run');
    await panel.getByRole('button', { name: 'Start review', exact: true }).click();
    const invoked = await response;
    expect(invoked.status(), await invoked.text()).toBe(200);
    const result = await invoked.json();
    expect(result.run_id).toBe(start.run_id);
    expect(result.ran_count).toBe(1);
    expect(result.results[0].status).toBe('review_received');
    await expect(panel.getByText('Contract fixture · not live Runtime acceptance')).toBeVisible();
    await expect(panel.getByText('Authorized sessions: 1. Maximum per session: 5s. No verified cost ceiling.')).toBeVisible();
    await panel.getByRole('button', { name: 'Open review report', exact: true }).click();
    await expect(panel.getByText('No findings reported')).toBeVisible();
    const details = await (await page.request.get(endpoint)).json();
    expect(details.reviews).toHaveLength(1);
    expect(details.reviews[0].task_skill_context.task.kind).toBe('direct_task');
    expect(details.reviews[0].runtime_boundaries.provider_binding_verified).toBe(false);
    await testInfo.attach('native-shaped-page-review-report', { body: await page.screenshot(), contentType: 'image/png' });
    await page.reload();
    await page.getByRole('button', { name: 'Review execution details', exact: true }).click();
    await panel.getByRole('button', { name: 'Open review report', exact: true }).click();
    await expect(panel.getByText('No findings reported')).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Start review', exact: true })).toHaveCount(0);
    expect(fixture.errors).toEqual([]);
    await testInfo.attach('native-shaped-flow-evidence', { body: JSON.stringify({ ...start, result,
      report_persisted: true, reopened: true, scope: details.reviews[0].task_skill_context.task,
      boundaries: details.reviews[0].runtime_boundaries }, null, 2), contentType: 'application/json' });
  } finally {
    if (child.exitCode === null && child.signalCode === null) {
      const end = once(child, 'exit');
      child.kill('SIGTERM');
      const fallback = setTimeout(() => child.kill('SIGKILL'), 5000);
      await end;
      clearTimeout(fallback);
    }
  }
});

test('pending native review exposes progress and cancellation before Start returns', async ({ page, fixture }, testInfo) => {
  const child = spawn(process.env.GOODTEAM_E2E_PYTHON || '../.venv/bin/python', [
    '../tests/fixtures/trusted_native_browser_server.py', path.resolve('dist'), 'cancel',
  ], { env: { ...process.env, PYTHONPATH: path.resolve('../src'), PYTHONDONTWRITEBYTECODE: '1' } });
  let output = '', errors = '';
  child.stdout.on('data', data => { output += data; });
  child.stderr.on('data', data => { errors += data; });
  try {
    await expect.poll(() => output.includes('\n') ? output : errors, { timeout: 15000 }).toContain('"url"');
    const start = JSON.parse(output.split('\n')[0]);
    const endpoint = `${start.url}/api/projects/DEMO/runs/${start.run_id}/skill-reviews`;
    let returned = false;
    await page.goto(`${start.url}/projects/DEMO/work`);
    await page.getByRole('button', { name: 'Review execution details', exact: true }).click();
    const panel = page.getByRole('region', { name: 'Review execution details', exact: true });
    const response = page.waitForResponse(item => new URL(item.url()).pathname === '/api/reviewer-jobs/run')
      .then(value => { returned = true; return value; });
    await panel.getByRole('button', { name: 'Start review', exact: true }).click();
    await expect(panel.getByText('Parent-observed phase: native_turn_started')).toBeVisible();
    await expect(panel.getByText('Authorized sessions: 1. Maximum per session: 900s. No verified cost ceiling.')).toBeVisible();
    expect(returned).toBe(false);
    const running = await (await page.request.get(endpoint)).json();
    expect(running.reviews).toHaveLength(1);
    expect(running.reviews[0].process_progress.thread_id).toBe('01234567-89ab-cdef-0123-456789abcdef');
    expect(running.reviews[0].report_available).toBe(false);
    await testInfo.attach('pending-review-progress-and-cancel', { body: await page.screenshot(), contentType: 'image/png' });
    const stopped = page.waitForResponse(item => item.url().endsWith('/cancel'));
    await panel.getByRole('button', { name: 'Cancel review', exact: true }).click();
    expect((await stopped).status()).toBe(202);
    const invoked = await response;
    expect(invoked.status()).toBe(200);
    const result = await invoked.json();
    expect(result.ran_count).toBe(1);
    expect(result.results[0].status).toBe('failed');
    await expect(panel.getByText('Cancelled', { exact: true })).toBeVisible();
    await expect(panel.getByText('Local process stopped; upstream request status unconfirmed.')).toBeVisible();
    const saved = await (await page.request.get(endpoint)).json();
    expect(saved.reviews[0].status).toBe('cancelled');
    expect(saved.reviews[0].process_progress.termination_confirmed).toBe(true);
    expect(saved.reviews[0].process_progress.usage).toBe(null);
    expect(saved.reviews[0].report_available).toBe(false);
    expect(start.real_model_calls).toBe(0);
    expect(fixture.errors).toEqual([]);
    await testInfo.attach('pending-cancel-model-free-evidence', { body: JSON.stringify({ ...start, result,
      progress: saved.reviews[0].process_progress, boundaries: saved.reviews[0].runtime_boundaries,
      cancelled_before_start_response: true }, null, 2), contentType: 'application/json' });
  } finally {
    if (child.exitCode === null && child.signalCode === null) {
      const end = once(child, 'exit');
      child.kill('SIGTERM');
      const fallback = setTimeout(() => child.kill('SIGKILL'), 5000);
      await end;
      clearTimeout(fallback);
    }
  }
});
