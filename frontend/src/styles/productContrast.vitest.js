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
