import fs from 'node:fs';
import path from 'node:path';
import postcss from 'postcss';
import { expect, test } from 'vitest';

const readSource = (relativePath) => fs.readFileSync(
  path.resolve(globalThis.process.cwd(), relativePath),
  'utf8',
);

const sources = {
  workspace: readSource('src/features/runs/workspace/workspace.css'),
  conversation: readSource('src/features/runs/workspace/projectConversation.css'),
  responsive: readSource('src/features/runs/workspace/workspaceResponsive.css'),
  taskDetail: readSource('src/features/runs/workspace/workspaceTaskDetail.css'),
};
const roots = Object.fromEntries(
  Object.entries(sources).map(([name, css]) => [name, postcss.parse(css, { from: name })]),
);
const globalCss = readSource('src/styles/product.css');
const globalRoot = postcss.parse(globalCss, { from: 'product.css' });
const shellSource = readSource('src/features/runs/workspace/WorkWorkspaceShell.jsx');
const workPageSource = readSource('src/features/runs/WorkPage.jsx');
const teamWorkspaceSource = readSource('src/features/runs/workspace/TeamWorkspace.jsx');
const workSelector = /\.(?:work-|team-|task-detail-)|\.product-work-page/;
const rootedWorkSelector = /^(?:\.dark\s+)?\.work-workspace-shell(?=$|\s|[.:#>+~]|\[)/;

function insideKeyframes(rule) {
  let parent = rule.parent;
  while (parent) {
    if (parent.type === 'atrule' && /keyframes$/i.test(parent.name)) return true;
    parent = parent.parent;
  }
  return false;
}

function ruleContext(rule) {
  const context = [];
  let parent = rule.parent;
  while (parent) {
    if (parent.type === 'atrule') context.unshift(`@${parent.name} ${parent.params}`);
    parent = parent.parent;
  }
  return context.join(' > ');
}

test('global product CSS has no Work-owned selectors or animation names', () => {
  globalRoot.walkRules((rule) => {
    if (insideKeyframes(rule)) return;
    for (const selector of rule.selectors || []) {
      expect(workSelector.test(selector), `global Work selector: ${selector}`).toBe(false);
    }
  });
  globalRoot.walkAtRules(/keyframes$/i, (atRule) => {
    expect(atRule.params, 'global Work animation').not.toMatch(/^(?:work-|taskDetail)/);
  });
  expect(globalCss).not.toContain('.work-workspace-shell');
});

test('every workspace presentation rule is rooted and has one file owner', () => {
  const owners = new Map();

  for (const [file, root] of Object.entries(roots)) {
    root.walkRules((rule) => {
      if (insideKeyframes(rule)) return;
      for (const selector of rule.selectors || []) {
        expect(selector.trim(), `unscoped selector in ${file}`).toMatch(rootedWorkSelector);
        const key = `${ruleContext(rule)} || ${selector.trim()}`;
        const previousOwner = owners.get(key);
        if (previousOwner && previousOwner !== file) {
          throw new Error(`selector context owned by both ${previousOwner} and ${file}: ${key}`);
        }
        owners.set(key, file);
      }
    });
  }
});

test('workspace stylesheets keep their declared responsibilities', () => {
  roots.workspace.walkAtRules('media', (atRule) => {
    throw new Error(`workspace.css leaked responsive rule: @media ${atRule.params}`);
  });
  roots.workspace.walkRules((rule) => {
    expect(rule.selector || '').not.toContain('.task-detail-');
  });

  for (const node of roots.responsive.nodes) {
    if (node.type === 'comment') continue;
    expect(node.type, 'workspaceResponsive.css may only contain at-rules').toBe('atrule');
    expect(node.name).toBe('media');
  }

  roots.taskDetail.walkRules((rule) => {
    if (insideKeyframes(rule)) return;
    const localSelector = (rule.selector || '').replaceAll('.work-workspace-shell', '');
    expect(localSelector).not.toMatch(/\.(?:work-|team-)|\.product-work-page/);
  });

  expect(sources.workspace).toContain('--work-material: #edf2f8;');
  expect(sources.workspace).toContain('.dark .work-workspace-shell {');
  expect(sources.responsive).toContain('bottom: max(0.65rem, env(safe-area-inset-bottom));');
  expect(sources.taskDetail).toContain('.work-workspace-shell .task-detail-dialog');
  expect(sources.taskDetail).toContain('.work-workspace-shell .task-detail-processing-banner');
  expect(shellSource).not.toContain('workspacePresentation.css');
  expect(shellSource).toContain("import './workspaceResponsive.css';");
  expect(shellSource).toContain("import './workspaceTaskDetail.css';");
});

test('Manager presentation components are owned by the workspace feature', () => {
  expect(workPageSource).toContain("from './workspace/TeamWorkspace'");
  expect(workPageSource).toContain("from './workspace/WorkTaskDetailDialog'");
  expect(workPageSource).not.toContain('function TeamWorkspace(');
  expect(workPageSource).not.toContain('function ManagerApprovalBrief(');
  expect(teamWorkspaceSource).toContain("import ProjectConversation from './ProjectConversation'");
  expect(teamWorkspaceSource).toContain('function AdvancedInspector(');
  expect(fs.existsSync(path.resolve(
    globalThis.process.cwd(),
    'src/components/TaskDetailModal.jsx',
  ))).toBe(false);
});
