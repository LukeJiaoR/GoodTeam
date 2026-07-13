import assert from 'node:assert/strict';
import test from 'node:test';

import { chooseLocalPath } from './localPathPicker.js';

function response(payload, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    json: async () => payload,
  };
}

test('selected directory posts the exact mode and invokes only the selected callback', async () => {
  const requests = [];
  const selected = [];
  let webPickerCalls = 0;
  let errorCalls = 0;

  const result = await chooseLocalPath({
    mode: 'directory',
    initialPath: '/workspace/current',
    onSelected: (path) => selected.push(path),
    openWebPicker: () => { webPickerCalls += 1; },
    onError: () => { errorCalls += 1; },
    fetchImpl: async (...args) => {
      requests.push(args);
      return response({ status: 'selected', path: '/workspace/chosen' });
    },
  });

  assert.deepEqual(result, { status: 'selected', path: '/workspace/chosen' });
  assert.deepEqual(selected, ['/workspace/chosen']);
  assert.equal(webPickerCalls, 0);
  assert.equal(errorCalls, 0);
  assert.equal(requests.length, 1);
  assert.equal(requests[0][0], '/api/fs/choose');
  assert.deepEqual(requests[0][1], {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode: 'directory', initial_path: '/workspace/current' }),
  });
});

test('selected file preserves file mode and updates only its target callback', async () => {
  const rootSelections = [];
  const fileSelections = [];

  const result = await chooseLocalPath({
    mode: 'file',
    initialPath: '/workspace/context',
    onSelected: (path) => fileSelections.push(path),
    openWebPicker: () => rootSelections.push('fallback'),
    onError: () => rootSelections.push('error'),
    fetchImpl: async (_url, options) => {
      assert.deepEqual(JSON.parse(options.body), {
        mode: 'file',
        initial_path: '/workspace/context',
      });
      return response({ status: 'selected', path: '/workspace/context/brief.md' });
    },
  });

  assert.deepEqual(result, { status: 'selected', path: '/workspace/context/brief.md' });
  assert.deepEqual(fileSelections, ['/workspace/context/brief.md']);
  assert.deepEqual(rootSelections, []);
});

test('cancelled selection is a no-op', async () => {
  const calls = [];

  const result = await chooseLocalPath({
    mode: 'directory',
    initialPath: '',
    onSelected: () => calls.push('selected'),
    openWebPicker: () => calls.push('fallback'),
    onError: () => calls.push('error'),
    fetchImpl: async () => response({ status: 'cancelled' }),
  });

  assert.deepEqual(result, { status: 'cancelled' });
  assert.deepEqual(calls, []);
});

test('unsupported native chooser opens the Web picker without reporting an error', async () => {
  const calls = [];

  const result = await chooseLocalPath({
    mode: 'directory',
    initialPath: '/workspace',
    onSelected: () => calls.push('selected'),
    openWebPicker: () => calls.push('fallback'),
    onError: () => calls.push('error'),
    fetchImpl: async () => response({ status: 'unsupported' }),
  });

  assert.deepEqual(result, { status: 'unsupported' });
  assert.deepEqual(calls, ['fallback']);
});

test('native chooser error reports the error and opens the Web picker', async () => {
  const calls = [];

  const result = await chooseLocalPath({
    mode: 'file',
    initialPath: '/workspace',
    onSelected: () => calls.push('selected'),
    openWebPicker: () => calls.push('fallback'),
    onError: (message) => calls.push(`error:${message}`),
    fetchImpl: async () => response({ status: 'error', error: 'Native dialog failed' }),
  });

  assert.deepEqual(result, { status: 'error', error: 'Native dialog failed' });
  assert.deepEqual(calls, ['error:Native dialog failed', 'fallback']);
});

test('HTTP failure reports the server error and opens the Web picker', async () => {
  const calls = [];

  const result = await chooseLocalPath({
    mode: 'directory',
    initialPath: '/workspace',
    onSelected: () => calls.push('selected'),
    openWebPicker: () => calls.push('fallback'),
    onError: (message) => calls.push(`error:${message}`),
    fetchImpl: async () => response({ error: 'Choose endpoint failed' }, { ok: false, status: 500 }),
  });

  assert.deepEqual(result, { status: 'error', error: 'Choose endpoint failed' });
  assert.deepEqual(calls, ['error:Choose endpoint failed', 'fallback']);
});
