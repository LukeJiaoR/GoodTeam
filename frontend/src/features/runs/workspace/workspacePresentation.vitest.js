import fs from 'node:fs';
import path from 'node:path';
import postcss from 'postcss';
import { expect, test } from 'vitest';

const readCss = (relativePath) => fs.readFileSync(
  path.resolve(globalThis.process.cwd(), relativePath),
  'utf8',
);

const workspaceRoot = postcss.parse(
  readCss('src/features/runs/workspace/workspace.css'),
  { from: 'workspace.css' },
);
const responsiveRoot = postcss.parse(
  readCss('src/features/runs/workspace/workspaceResponsive.css'),
  { from: 'workspaceResponsive.css' },
);
const globalRoot = postcss.parse(readCss('src/styles/product.css'), { from: 'product.css' });

function declarationsFor(root, selector) {
  const declarations = {};
  root.walkRules((rule) => {
    if (!(rule.selectors || []).includes(selector)) return;
    rule.walkDecls((declaration) => {
      declarations[declaration.prop] = declaration.value;
    });
  });
  return declarations;
}

function tokensFor(root, selector, prefix) {
  return Object.fromEntries(
    Object.entries(declarationsFor(root, selector))
      .filter(([property, value]) => property.startsWith(prefix) && /^#[\da-f]{6}$/i.test(value))
      .map(([property, value]) => [property.slice(prefix.length), value]),
  );
}

function relativeLuminance(hex) {
  const channels = hex.slice(1).match(/.{2}/g).map((value) => parseInt(value, 16) / 255);
  const [red, green, blue] = channels.map((value) => (
    value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  ));
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

function contrastRatio(first, second) {
  const values = [relativeLuminance(first), relativeLuminance(second)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

test.each([
  ['light', '.work-workspace-shell'],
  ['dark', '.dark .work-workspace-shell'],
])('%s Work palette is feature-owned and readable on its material surface', (_mode, selector) => {
  const tokens = tokensFor(workspaceRoot, selector, '--work-');
  for (const token of ['text', 'text-muted', 'accent-strong', 'success', 'warning', 'danger']) {
    expect(contrastRatio(tokens[token], tokens.material), `${token} contrast`).toBeGreaterThanOrEqual(4.5);
  }
});

test('global shell palette remains unchanged and contains no Work tokens', () => {
  expect(tokensFor(globalRoot, '.product-shell', '--product-')).toMatchObject({
    bg: '#f3f0e9',
    surface: '#fffdf8',
    text: '#292723',
    accent: '#9d5d42',
  });
  expect(declarationsFor(globalRoot, '.product-shell')).not.toHaveProperty('--work-material');
});

test('Execution LEDs use semantic Work tokens and stop for reduced motion', () => {
  const light = tokensFor(workspaceRoot, '.work-workspace-shell', '--work-');
  const dark = tokensFor(workspaceRoot, '.dark .work-workspace-shell', '--work-');

  for (const tokens of [light, dark]) {
    expect(tokens['led-working']).toBe(tokens.accent);
    expect(tokens['led-reviewing']).toBe(tokens['accent-strong']);
    expect(tokens['led-fixing']).toBe(tokens.accent);
    expect(tokens['led-blocked']).toBe(tokens.danger);
    expect(tokens['led-completed']).toBe(tokens.success);
  }

  expect(declarationsFor(
    workspaceRoot,
    '.work-workspace-shell .team-status-led-trigger[data-motion="ambient"] .team-status-led',
  ).animation).toContain('work-status-led-breathe');

  let reducedMotionRule = null;
  responsiveRoot.walkAtRules('media', (atRule) => {
    if (atRule.params !== '(prefers-reduced-motion: reduce)') return;
    atRule.walkRules((rule) => {
      if ((rule.selectors || []).includes('.work-workspace-shell .team-status-led')) {
        reducedMotionRule = declarationsFor(atRule, '.work-workspace-shell .team-status-led');
      }
    });
  });
  expect(reducedMotionRule).toMatchObject({ animation: 'none' });
});

test('Manager workspace keeps one raised and inset material system', () => {
  expect(declarationsFor(workspaceRoot, '.work-workspace-shell .team-workspace')).toMatchObject({
    'grid-template-areas': '"execution manager outcomes"',
    background: 'var(--work-material)',
    border: '0',
    'border-radius': '28px',
  });
  expect(declarationsFor(workspaceRoot, '.work-workspace-shell .team-pane')).toMatchObject({
    background: 'var(--work-material)',
    border: '0',
    'border-radius': '22px',
    'box-shadow': 'var(--work-shadow-soft)',
  });
  expect(declarationsFor(workspaceRoot, '.work-workspace-shell .team-pane--manager')['box-shadow'])
    .toBe('var(--work-shadow-raised)');
  expect(declarationsFor(workspaceRoot, '.work-workspace-shell .team-attention')['box-shadow'])
    .toContain('var(--work-shadow-raised)');
  expect(declarationsFor(workspaceRoot, '.work-workspace-shell .team-inspector__tabs')).toMatchObject({
    background: 'var(--work-material-deep)',
    'box-shadow': 'var(--work-shadow-inset)',
  });
  expect(declarationsFor(workspaceRoot, '.work-workspace-shell .work-run-context')).toMatchObject({
    background: 'var(--work-material)',
    'box-shadow': 'var(--work-shadow-soft)',
  });
  expect(declarationsFor(workspaceRoot, '.work-workspace-shell .work-run-title')['white-space'])
    .toBe('normal');
});

test('desktop Work header keeps mission context and actions on one compact row', () => {
  expect(declarationsFor(workspaceRoot, '.work-workspace-shell .product-page-header')).toMatchObject({
    'margin-bottom': '1rem',
    'border-bottom': '1px solid var(--work-border)',
  });
  expect(declarationsFor(workspaceRoot, '.work-workspace-shell .product-page-header__meta')).toMatchObject({
    display: 'grid',
    'grid-template-columns': 'minmax(24rem, 1fr) auto',
    'align-items': 'center',
  });
  const selectableContext = declarationsFor(
    workspaceRoot,
    '.work-workspace-shell .work-run-context--selectable',
  );
  expect(selectableContext['grid-template-columns'])
    .toBe('minmax(0, 1fr) minmax(14rem, 22rem)');
  expect(selectableContext['grid-template-areas']).toContain('"mission selector"');
  expect(selectableContext['grid-template-areas']).toContain('"facts selector"');
});

test('Work context behaves as a top drawer without reserving closed space', () => {
  expect(declarationsFor(
    workspaceRoot,
    '.work-workspace-shell .product-work-page > .product-page-header.work-context-drawer',
  )).toMatchObject({
    'max-height': '15rem',
    overflow: 'clip',
    'transform-origin': 'top',
  });
  expect(declarationsFor(
    workspaceRoot,
    '.work-workspace-shell .product-work-page > .product-page-header.work-context-drawer[data-open="false"]',
  )).toMatchObject({
    'max-height': '0',
    'margin-bottom': '0',
    visibility: 'hidden',
    opacity: '0',
    'pointer-events': 'none',
  });
});

test('Work context aligns mission, status, progress, and update metadata', () => {
  expect(declarationsFor(
    workspaceRoot,
    '.work-workspace-shell .work-run-context__mission',
  )).toMatchObject({
    display: 'grid',
    'grid-template-columns': '7.5rem minmax(0, 1fr)',
    'align-items': 'start',
  });
  expect(declarationsFor(
    workspaceRoot,
    '.work-workspace-shell .work-run-context__facts',
  )).toMatchObject({
    display: 'grid',
    'grid-template-columns': '7.5rem repeat(2, max-content) minmax(0, 1fr)',
    'align-items': 'center',
  });
  expect(declarationsFor(
    workspaceRoot,
    '.work-workspace-shell .work-run-context__updated',
  )).toMatchObject({
    'grid-column': '4',
    'justify-self': 'end',
  });
});

test('Outcomes cards keep their natural height inside the scrolling pane', () => {
  expect(declarationsFor(workspaceRoot, '.work-workspace-shell .team-outcome-list')).toMatchObject({
    display: 'flex',
    'flex-direction': 'column',
    'overflow-y': 'auto',
  });
  expect(declarationsFor(workspaceRoot, '.work-workspace-shell .team-outcome-list > li').flex)
    .toBe('0 0 auto');
});

test('Outcomes cards progressively disclose owner and full details', () => {
  expect(declarationsFor(
    workspaceRoot,
    '.work-workspace-shell .team-outcome-card__owner',
  )).toMatchObject({
    'max-height': '0',
    opacity: '0',
  });
  expect(declarationsFor(
    workspaceRoot,
    '.work-workspace-shell .team-outcome-list > li:hover .team-outcome-card__owner',
  )).toMatchObject({
    'max-height': '3rem',
    opacity: '1',
  });
  expect(declarationsFor(
    workspaceRoot,
    '.work-workspace-shell .team-outcome-card__details[hidden]',
  )).toMatchObject({ display: 'none' });
});

test('historical runs retain the full-width three-pane workspace', () => {
  expect(declarationsFor(
    workspaceRoot,
    '.work-workspace-shell .work-historical-view',
  )).toMatchObject({ width: '100%' });
  expect(declarationsFor(
    workspaceRoot,
    '.work-workspace-shell .work-historical-view',
  )).not.toHaveProperty('max-width');
});
