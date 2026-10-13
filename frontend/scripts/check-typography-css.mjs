// Run after npm run build. This checks emitted selectors/declarations, not pixels.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import postcss from 'postcss';
import { JSDOM } from 'jsdom';

const assets = path.resolve(process.argv[2] || 'dist/assets');
const cssFiles = fs.readdirSync(assets).filter(file => file.endsWith('.css'));
assert.ok(cssFiles.length, 'Build the frontend before checking typography CSS');
const rules = [];
for (const file of cssFiles) {
  postcss.parse(fs.readFileSync(path.join(assets, file), 'utf8')).walkRules(rule => rules.push(rule));
}
const document = new JSDOM(`<div class="product-shell product-shell--workspace product-shell--studio product-shell--work">
  <label class="review-editor"><textarea id="review"></textarea></label>
  <div class="assistant-draft-editor"><textarea id="draft"></textarea></div>
  <div class="work-workspace-shell"><div class="project-queued-task"><textarea id="queued"></textarea></div></div>
  <textarea id="plain"></textarea><input id="input"><select id="select"></select>
</div>`).window.document;
function declarations(element, property) {
  return rules.flatMap(rule => {
    if (!rule.nodes.some(node => node.type === 'decl' && node.prop === property)) return [];
    // The selected fixtures have no pseudo-state. Exclude pseudo-elements too.
    const matching = postcss.list.comma(rule.selector).some(selector =>
      !selector.includes('::') && !/:(?:-moz|-webkit)-/.test(selector) && element.matches(selector));
    return matching ? rule.nodes.filter(node => node.type === 'decl' && node.prop === property)
      .map(node => ({ node, selector: rule.selector })) : [];
  });
}
const retainedMinimums = { review: '28rem', draft: '120px', queued: '5rem' };
for (const id of ['review', 'draft', 'queued', 'plain']) {
  const element = document.getElementById(id);
  const minimums = declarations(element, 'min-height');
  assert.equal(minimums.some(({ node }) => node.important), false,
    `${id}: a forced touch minimum would override the feature's larger editor height`);
  assert.equal(minimums.some(({ selector }) => selector.includes('product-shell--studio')), false,
    `${id}: shared Studio touch rules must leave textarea dimensions to feature styles`);
  if (retainedMinimums[id]) {
    assert.ok(minimums.some(({ node }) => node.value === retainedMinimums[id]),
      `${id}: feature minimum ${retainedMinimums[id]} must survive compilation`);
  }
  assert.ok(declarations(element, 'font-size').some(({ node, selector }) =>
    selector.includes('product-shell--studio') && node.important && /max\(1rem,\s*16px\)/.test(node.value)),
  `${id}: mobile text must retain the 16px font protection`);
}
for (const id of ['input', 'select']) {
  assert.ok(declarations(document.getElementById(id), 'min-height').some(({ node, selector }) =>
    selector.includes('product-shell--studio') && node.important && node.value === '44px'),
  `${id}: compact single-line controls must retain the 44px touch minimum`);
}
console.log('Compiled CSS selector regression passed: larger Review/draft/queued editors retained; textarea font and input/select touch guards present. Not browser visual acceptance.');
