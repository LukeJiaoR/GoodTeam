import fs from 'node:fs';
import path from 'node:path';
import { expect, test } from 'vitest';

const readCss = (relativePath) => fs.readFileSync(
  path.resolve(globalThis.process.cwd(), relativePath),
  'utf8',
);

const globalCss = readCss('src/styles/product.css');
const workspaceCss = readCss('src/features/runs/workspace/workspace.css');
const presentationCss = readCss('src/features/runs/workspace/workspacePresentation.css');
const responsiveCss = readCss('src/features/runs/workspace/workspaceResponsive.css');
const taskDetailCss = readCss('src/features/runs/workspace/workspaceTaskDetail.css');
const localCss = `${workspaceCss}\n${presentationCss}\n${responsiveCss}\n${taskDetailCss}`;

const globalWorkLeaks = [
  '.product-shell--work',
  '--product-neu-',
  '.team-workspace',
  '.team-approval-brief',
  '.team-status-led',
  '.team-inspector',
  '.team-mobile-nav',
  '.team-pane--manager',
  '.team-pane--execution',
  '.team-pane--outcomes',
  '.task-detail-dialog',
  '.task-detail-backdrop',
];

test('global product CSS contains no Manager workspace presentation rules', () => {
  for (const marker of globalWorkLeaks) {
    expect(globalCss, `global CSS leaked ${marker}`).not.toContain(marker);
  }
  expect(globalCss).not.toContain('.work-workspace-shell');
});

test('the Work visual system is rooted under the feature-owned workspace shell', () => {
  expect(workspaceCss).toContain('.work-workspace-shell {');
  expect(workspaceCss).toContain('.dark .work-workspace-shell {');
  expect(workspaceCss).toContain('--work-material: #edf2f8;');
  expect(workspaceCss).toContain('--work-accent: #315efb;');
  expect(localCss).toContain('.work-workspace-shell .team-workspace');
  expect(localCss).toContain('.work-workspace-shell .team-approval-brief');
  expect(localCss).toContain('.work-workspace-shell .team-mobile-nav');
  expect(localCss).toContain('.work-workspace-shell .team-inspector');
  expect(taskDetailCss).toContain('.work-workspace-shell .task-detail-dialog');
  expect(taskDetailCss).toContain('.work-workspace-shell .task-detail-backdrop');
  expect(responsiveCss).toContain('bottom: max(0.65rem, env(safe-area-inset-bottom));');
  expect(localCss).not.toContain('.product-shell--work');
  expect(localCss).not.toContain('--product-neu-');
});

test('Work-owned selectors cannot escape the workspace root', () => {
  const presentationMarkers = ['.team-', '.task-detail-', '.product-work-page', '.work-run-', '.md-renderer', '.md-code-', '.md-table'];

  for (const css of [workspaceCss, presentationCss, responsiveCss, taskDetailCss]) {
    for (const chunk of css.split('{').slice(0, -1)) {
      const selector = chunk.slice(chunk.lastIndexOf('}') + 1).trim();
      if (!presentationMarkers.some((marker) => selector.includes(marker))) continue;
      for (const part of selector.split(',')) {
        if (!presentationMarkers.some((marker) => part.includes(marker))) continue;
        expect(part.trim(), `unscoped Work selector: ${part.trim()}`).toContain('.work-workspace-shell');
      }
    }
  }
});
