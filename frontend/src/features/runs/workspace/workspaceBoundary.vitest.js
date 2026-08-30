import fs from 'node:fs';
import path from 'node:path';
import { expect, test } from 'vitest';

const globalCss = fs.readFileSync(
  path.resolve(globalThis.process.cwd(), 'src/styles/product.css'),
  'utf8',
);
const workspaceCss = fs.readFileSync(
  path.resolve(globalThis.process.cwd(), 'src/features/runs/workspace/workspace.css'),
  'utf8',
);

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
  expect(workspaceCss).toContain('.work-workspace-shell .team-workspace');
  expect(workspaceCss).toContain('.work-workspace-shell .team-approval-brief');
  expect(workspaceCss).toContain('.work-workspace-shell .team-mobile-nav');
  expect(workspaceCss).toContain('.work-workspace-shell .team-inspector');
  expect(workspaceCss).toContain('.work-workspace-shell .task-detail-dialog');
  expect(workspaceCss).not.toContain('.product-shell--work');
  expect(workspaceCss).not.toContain('--product-neu-');
});

test('Work-owned selectors cannot escape the workspace root', () => {
  const selectorBlocks = workspaceCss.match(/(?:^|\n)([^@\n][^{]+)\{/g) || [];
  const presentationMarkers = ['.team-', '.task-detail-', '.product-work-page', '.work-run-'];

  for (const rawSelector of selectorBlocks) {
    const selector = rawSelector.replace(/\{$/, '').trim();
    if (!presentationMarkers.some((marker) => selector.includes(marker))) continue;
    for (const part of selector.split(',')) {
      expect(part.trim(), `unscoped Work selector: ${part.trim()}`).toContain('.work-workspace-shell');
    }
  }
});
