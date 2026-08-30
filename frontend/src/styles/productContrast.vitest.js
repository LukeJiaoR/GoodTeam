import fs from 'node:fs';
import path from 'node:path';
import { expect, test } from 'vitest';

const css = fs.readFileSync(
  path.resolve(globalThis.process.cwd(), 'src/styles/product.css'),
  'utf8',
);

function blockFor(selector) {
  const marker = `${selector} {`;
  const start = css.indexOf(marker);
  if (start < 0) throw new Error(`Missing CSS block: ${selector}`);
  const bodyStart = start + marker.length;
  const end = css.indexOf('\n}', bodyStart);
  if (end < 0) throw new Error(`Unclosed CSS block: ${selector}`);
  return css.slice(bodyStart, end);
}

function tokensFor(selector) {
  return Object.fromEntries(
    [...blockFor(selector).matchAll(/--(product-[\w-]+):\s*(#[\da-f]{6});/gi)]
      .map((match) => [match[1], match[2]]),
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

const toneTokens = {
  neutral: 'product-text-muted',
  active: 'product-accent-strong',
  accent: 'product-accent-strong',
  complete: 'product-success',
  success: 'product-success',
  pending: 'product-warning',
  needs_input: 'product-warning',
  warning: 'product-warning',
  error: 'product-danger',
  danger: 'product-danger',
};

test.each([
  ['light', tokensFor('.product-shell')],
  ['dark', { ...tokensFor('.product-shell'), ...tokensFor('.dark .product-shell') }],
])('%s status badge tones meet 4.5:1 against the actual badge surface', (_mode, tokens) => {
  const badge = blockFor('.product-shell .product-status-badge');
  expect(badge).toMatch(/background:\s*var\(--product-surface\);/);

  for (const [tone, token] of Object.entries(toneTokens)) {
    expect(
      contrastRatio(tokens[token], tokens['product-surface']),
      `${tone} badge contrast`,
    ).toBeGreaterThanOrEqual(4.5);
  }
});

test('button feedback uses deliberate timing and includes a danger hover state', () => {
  const button = blockFor('.product-shell .product-button');
  const durations = [...button.matchAll(/(\d+)ms/g)].map((match) => Number(match[1]));

  expect(durations.length).toBeGreaterThan(0);
  expect(durations.every((duration) => duration >= 160 && duration <= 240)).toBe(true);
  expect(blockFor('.product-shell .product-button--danger:hover:not(:disabled)')).toMatch(
    /background:/,
  );
});

test('the neumorphic palette is scoped to Work and leaves the app shell tokens intact', () => {
  const shell = tokensFor('.product-shell');
  const darkShell = tokensFor('.dark .product-shell');
  const work = tokensFor('.product-shell--work');
  const darkWork = tokensFor('.dark .product-shell--work');

  expect(shell).toMatchObject({
    'product-bg': '#f3f0e9',
    'product-surface': '#fffdf8',
    'product-text': '#292723',
    'product-accent': '#9d5d42',
  });
  expect(blockFor('.product-shell')).not.toContain('--product-neu-');
  expect(darkShell).toMatchObject({
    'product-bg': '#191816',
    'product-surface': '#23211e',
    'product-text': '#eee9df',
    'product-accent': '#c07859',
  });
  expect(blockFor('.dark .product-shell')).not.toContain('--product-neu-');
  expect(work).toMatchObject({
    'product-bg': '#f4f7fc',
    'product-text': '#102044',
    'product-accent': '#315efb',
    'product-neu-material': '#edf2f8',
  });
  expect(darkWork).toMatchObject({
    'product-bg': '#0e1420',
    'product-text': '#f2f6ff',
    'product-accent': '#86a1ff',
    'product-neu-material': '#182231',
  });
});

test('Execution LEDs use semantic tokens and become static for reduced motion', () => {
  const light = tokensFor('.product-shell--work');
  const dark = {
    ...light,
    ...tokensFor('.dark .product-shell--work'),
  };
  for (const state of [
    'working', 'reviewing', 'fixing', 'waiting', 'up-next', 'blocked', 'completed', 'stopped',
  ]) {
    expect(light[`product-led-${state}`], `light ${state} LED token`).toMatch(/^#[\da-f]{6}$/i);
    expect(dark[`product-led-${state}`], `dark ${state} LED token`).toMatch(/^#[\da-f]{6}$/i);
  }
  for (const tokens of [light, dark]) {
    expect(tokens['product-led-working']).toBe(tokens['product-accent']);
    expect(tokens['product-led-reviewing']).toBe(tokens['product-accent-strong']);
    expect(tokens['product-led-fixing']).toBe(tokens['product-accent']);
    expect(tokens['product-led-blocked']).toBe(tokens['product-danger']);
    expect(tokens['product-led-completed']).toBe(tokens['product-success']);
  }
  expect(blockFor('.product-shell .team-status-led-trigger[data-motion="ambient"] .team-status-led'))
    .toMatch(/animation:\s*team-status-led-breathe/);
  expect(css).toMatch(
    /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.product-shell \.team-status-led \{[\s\S]*?animation:\s*none !important;/,
  );
});

test('Work uses one material system with authentic raised and inset neumorphic depth', () => {
  const chrome = blockFor('.product-shell--work .product-work-chrome');
  const activeNavigation = blockFor('.product-shell--work .product-work-navigation__link.is-active');
  const studio = blockFor('.product-shell .team-workspace');
  const pane = blockFor('.product-shell .team-pane');
  const manager = blockFor('.product-shell .team-pane--manager');
  const attention = blockFor('.product-shell .team-attention');
  const attentionLabel = blockFor('.product-shell .team-attention__label');
  const attentionIcon = blockFor('.product-shell .team-attention__label svg');
  const outcomeAttention = blockFor('.product-shell .team-outcome-list li[data-attention="true"]');
  const outcomeAttentionLabel = blockFor('.product-shell .team-outcome-list__attention');
  const mobileAttention = blockFor('.product-shell .team-mobile-nav__attention');
  const actorCard = blockFor('.product-shell .team-actor-list li');
  const inspectorTabs = blockFor('.product-shell .team-inspector__tabs');
  const runContext = blockFor('.product-shell .product-work-page .work-run-context');
  const runTitle = blockFor('.product-shell .product-work-page .work-run-title');

  expect(chrome).toMatch(/background:\s*var\(--product-neu-material\);/);
  expect(chrome).toMatch(/var\(--product-neu-shadow\)/);
  expect(activeNavigation).toMatch(/var\(--product-neu-shadow-soft\)/);
  expect(studio).toMatch(/grid-template-areas:\s*"execution manager outcomes";/);
  expect(studio).toMatch(/background-color:\s*var\(--product-neu-material\);/);
  expect(studio).toMatch(/border:\s*0;/);
  expect(studio).toMatch(/border-radius:\s*28px;/);
  expect(studio).toMatch(/var\(--product-neu-shadow-raised\)/);
  expect(pane).toMatch(/border:\s*0;/);
  expect(pane).toMatch(/background:\s*var\(--product-neu-material\);/);
  expect(pane).toMatch(/border-radius:\s*22px;/);
  expect(pane).toMatch(/var\(--product-neu-shadow-soft\)/);
  expect(manager).toMatch(/background:\s*var\(--product-neu-material\);/);
  expect(manager).toMatch(/var\(--product-neu-shadow-raised\)/);
  expect(attention).toMatch(/background:\s*var\(--product-neu-material\);/);
  expect(attention).toMatch(/var\(--product-neu-shadow-raised\)/);
  expect(attentionLabel).toMatch(/color:\s*var\(--product-text-muted\);/);
  expect(attentionIcon).toMatch(/color:\s*var\(--product-accent\);/);
  expect(outcomeAttention).toMatch(/var\(--product-neu-material\)/);
  expect(outcomeAttention).toMatch(/var\(--product-neu-shadow-raised\)/);
  expect(outcomeAttentionLabel).toMatch(/color:\s*var\(--product-accent-strong\);/);
  expect(mobileAttention).toMatch(/background:\s*var\(--product-accent\);/);
  expect(actorCard).toMatch(/background:\s*var\(--product-neu-material\);/);
  expect(actorCard).toMatch(/border-radius:\s*17px;/);
  expect(actorCard).toMatch(/var\(--product-neu-shadow-soft\)/);
  expect(css).not.toContain('.team-task-progress');
  expect(inspectorTabs).toMatch(/background:\s*var\(--product-neu-material-deep\);/);
  expect(inspectorTabs).toMatch(/var\(--product-neu-shadow-inset\)/);
  expect(runContext).toMatch(/background:\s*var\(--product-neu-material\);/);
  expect(runContext).toMatch(/var\(--product-neu-shadow-soft\)/);
  expect(runTitle).toMatch(/white-space:\s*normal;/);
});
