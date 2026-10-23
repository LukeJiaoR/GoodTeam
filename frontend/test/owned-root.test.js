import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { openOwnedE2ERoot } from '../e2e/owned-root.js';

const frontend = fileURLToPath(new URL('..', import.meta.url));
const posix = { skip: process.platform === 'win32' && 'Requires POSIX mode bits' };

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'goodteam-e2e-'));
  const support = fs.mkdtempSync(path.join(os.tmpdir(), 'e2e-cleanup-test-'));
  const owner = crypto.randomUUID();
  const inputs = path.join(root, 'operator-state/skills/codex-review/job/review-context/inputs');
  const runtime = path.join(inputs, '.runtime');
  const guard = path.join(runtime, 'guard.py');
  const outside = path.join(support, 'outside');
  fs.writeFileSync(path.join(root, '.owner'), owner);
  // Restore only the exact directories this fixture may seal, even on a red run.
  t.after(() => {
    for (const directory of [root, inputs, runtime, outside]) {
      if (fs.existsSync(directory) && !fs.lstatSync(directory).isSymbolicLink()) {
        fs.chmodSync(directory, 0o700);
      }
    }
    fs.rmSync(root, { recursive: true, force: true });
    fs.rmSync(support, { recursive: true, force: true });
  });
  return { root, support, owner, inputs, runtime, guard, outside };
}

function sealInputs(item) {
  fs.mkdirSync(item.runtime, { recursive: true });
  fs.writeFileSync(item.guard, '# fixture guard\n');
  fs.chmodSync(item.guard, 0o444);
  fs.chmodSync(item.runtime, 0o555);
  fs.chmodSync(item.inputs, 0o555);
}

function startServer(item, childSource = 'process.exit(0);', overrides = {}) {
  const executable = path.join(item.support, 'fixture-python.cjs');
  fs.writeFileSync(executable, `#!${process.execPath}\n${childSource}\n`, { mode: 0o700 });
  return spawnSync(process.execPath, ['e2e/start-server.js'], {
    cwd: frontend,
    encoding: 'utf8',
    timeout: 10_000,
    env: {
      ...process.env,
      GOODTEAM_E2E_ROOT: item.root,
      GOODTEAM_E2E_OWNER: item.owner,
      GOODTEAM_E2E_PYTHON: executable,
      GOODTEAM_E2E_PORT: '0',
      ...overrides,
    },
  });
}

test('server exit removes sealed reviewer inputs without an EACCES teardown', posix, (t) => {
  const item = fixture(t);
  sealInputs(item);
  assert.equal(fs.statSync(item.inputs).mode & 0o777, 0o555);
  assert.equal(fs.statSync(item.runtime).mode & 0o777, 0o555);
  assert.equal(fs.statSync(item.guard).mode & 0o777, 0o444);
  const result = startServer(item);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.existsSync(item.root), false);
});

test('sealed input cleanup preserves a failing child exit code', posix, (t) => {
  const item = fixture(t);
  sealInputs(item);
  const result = startServer(item, 'process.exit(7);');
  assert.equal(result.status, 7, result.stderr);
  assert.equal(fs.existsSync(item.root), false);
});

test('expected child termination still cleans sealed inputs successfully', posix, (t) => {
  const item = fixture(t);
  sealInputs(item);
  const result = startServer(item, "process.kill(process.pid, 'SIGTERM');");
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.existsSync(item.root), false);
});

test('startup rejects an owner-token mismatch before changing the tree', posix, (t) => {
  const item = fixture(t);
  sealInputs(item);
  const result = startServer(item, 'process.exit(0);', { GOODTEAM_E2E_OWNER: 'different-owner' });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /E2E requires its own temporary directory/);
  assert.equal(fs.existsSync(path.join(item.root, 'home')), false);
  assert.equal(fs.statSync(item.inputs).mode & 0o777, 0o555);
});

test('shutdown revalidates ownership before changing or removing anything', posix, (t) => {
  const item = fixture(t);
  sealInputs(item);
  const result = startServer(item, `
    require('node:fs').writeFileSync(require('node:path').join(process.env.GOODTEAM_E2E_ROOT, '.owner'), 'new-owner');
  `);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /E2E requires its own temporary directory/);
  assert.equal(fs.readFileSync(path.join(item.root, '.owner'), 'utf8'), 'new-owner');
  assert.equal(fs.statSync(item.runtime).mode & 0o777, 0o555);
  assert.equal(fs.statSync(item.inputs).mode & 0o777, 0o555);
});

test('cleanup never follows outside symlinks or changes hard-linked file modes', posix, (t) => {
  const item = fixture(t);
  fs.mkdirSync(item.outside);
  const externalFile = path.join(item.outside, 'keep.txt');
  fs.writeFileSync(externalFile, 'keep outside data\n', { mode: 0o444 });
  fs.mkdirSync(item.runtime, { recursive: true });
  fs.symlinkSync(item.outside, path.join(item.runtime, 'outside-directory'));
  fs.symlinkSync(externalFile, path.join(item.runtime, 'outside-file'));
  fs.symlinkSync(path.join(item.outside, 'missing'), path.join(item.runtime, 'dangling'));
  fs.linkSync(externalFile, path.join(item.runtime, 'hard-linked-file'));
  fs.chmodSync(item.outside, 0o555);
  sealInputs(item);
  const result = startServer(item);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.existsSync(item.root), false);
  assert.equal(fs.readFileSync(externalFile, 'utf8'), 'keep outside data\n');
  assert.equal(fs.statSync(item.outside).mode & 0o777, 0o555);
  assert.equal(fs.statSync(externalFile).mode & 0o777, 0o444);
});

test('owned cleanup is repeatable after the tree is removed', posix, (t) => {
  const item = fixture(t);
  sealInputs(item);
  const { cleanup } = openOwnedE2ERoot(item.root, item.owner);
  cleanup();
  assert.equal(fs.existsSync(item.root), false);
  assert.doesNotThrow(cleanup);
});

test('a real filesystem failure makes otherwise successful server teardown fail', {
  skip: process.platform === 'win32' || process.getuid?.() === 0,
}, (t) => {
  const item = fixture(t);
  sealInputs(item);
  fs.chmodSync(item.runtime, 0o000);
  const result = startServer(item);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /EACCES/);
  assert.equal(fs.readFileSync(path.join(item.root, '.owner'), 'utf8'), item.owner);
});

test('cleanup errors propagate instead of turning teardown into a success', posix, (t) => {
  const item = fixture(t);
  sealInputs(item);
  const { cleanup } = openOwnedE2ERoot(item.root, item.owner);
  const error = Object.assign(new Error('fixture removal failure'), { code: 'EIO' });
  const remove = t.mock.method(fs, 'rmSync', () => { throw error; });
  assert.throws(cleanup, (actual) => actual === error);
  remove.mock.restore();
  assert.equal(fs.existsSync(item.guard), true);
  cleanup();
});

test('permission repair errors propagate without attempting removal', posix, (t) => {
  const item = fixture(t);
  sealInputs(item);
  const { cleanup } = openOwnedE2ERoot(item.root, item.owner);
  const error = Object.assign(new Error('fixture chmod failure'), { code: 'EPERM' });
  const chmod = t.mock.method(fs, 'fchmodSync', () => { throw error; });
  assert.throws(cleanup, (actual) => actual === error);
  chmod.mock.restore();
  assert.equal(fs.existsSync(path.join(item.root, '.owner')), true);
  assert.equal(fs.statSync(item.inputs).mode & 0o777, 0o555);
});

test('startup rejects roots outside the direct temporary-parent and prefix boundary', posix, (t) => {
  const item = fixture(t);
  assert.throws(() => openOwnedE2ERoot(item.support, item.owner), /E2E requires its own temporary directory/);
  const nested = path.join(item.support, 'goodteam-e2e-nested');
  fs.mkdirSync(nested);
  fs.writeFileSync(path.join(nested, '.owner'), item.owner);
  assert.throws(() => openOwnedE2ERoot(nested, item.owner), /E2E requires its own temporary directory/);
  assert.equal(fs.existsSync(nested), true);
});

test('a symlink cannot stand in for the owned root or owner marker', posix, (t) => {
  const item = fixture(t);
  const alias = path.join(item.support, 'root-alias');
  fs.symlinkSync(item.root, alias);
  assert.throws(() => openOwnedE2ERoot(alias, item.owner), /E2E requires its own temporary directory/);
  const marker = path.join(item.root, '.owner');
  const outsideMarker = path.join(item.support, 'owner');
  fs.renameSync(marker, outsideMarker);
  fs.symlinkSync(outsideMarker, marker);
  assert.throws(() => openOwnedE2ERoot(item.root, item.owner), /E2E requires its own temporary directory/);
  assert.equal(fs.readFileSync(outsideMarker, 'utf8'), item.owner);
});

test('cleanup rejects a replacement root even when its token matches', posix, (t) => {
  const item = fixture(t);
  const { cleanup } = openOwnedE2ERoot(item.root, item.owner);
  fs.renameSync(item.root, path.join(item.support, 'original'));
  fs.mkdirSync(item.root);
  fs.writeFileSync(path.join(item.root, '.owner'), item.owner);
  sealInputs(item);
  assert.throws(cleanup, /E2E requires its own temporary directory/);
  assert.equal(fs.statSync(item.inputs).mode & 0o777, 0o555);
  assert.equal(fs.readFileSync(path.join(item.root, '.owner'), 'utf8'), item.owner);
});

test('cleanup rejects a root replaced by an outside symlink', posix, (t) => {
  const item = fixture(t);
  const { cleanup } = openOwnedE2ERoot(item.root, item.owner);
  fs.renameSync(item.root, path.join(item.support, 'original'));
  fs.mkdirSync(item.outside);
  fs.writeFileSync(path.join(item.outside, '.owner'), item.owner, { mode: 0o444 });
  fs.chmodSync(item.outside, 0o555);
  fs.symlinkSync(item.outside, item.root);
  assert.throws(cleanup, /E2E requires its own temporary directory/);
  assert.equal(fs.statSync(item.outside).mode & 0o777, 0o555);
  assert.equal(fs.readFileSync(path.join(item.outside, '.owner'), 'utf8'), item.owner);
});
