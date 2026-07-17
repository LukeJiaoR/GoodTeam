import fs from 'node:fs';
import path from 'node:path';
import { expect, test } from 'vitest';
import { en } from './locales/en';
import { zhCN } from './locales/zh-CN';

const backendSource = fs.readFileSync(
  path.resolve(globalThis.process.cwd(), '../src/goodteam_os/web.py'),
  'utf8',
);

function quotedValues(source) {
  return [...source.matchAll(/(["'])([a-z][a-z0-9_]*)\1/g)].map((match) => match[2]);
}

function mappingBlock(source, name) {
  const match = source.match(new RegExp(`${name} = \\{([\\s\\S]*?)\\n\\}`));
  if (!match) throw new Error(`Missing backend mapping: ${name}`);
  return match[1];
}

function mappingListValues(source, name) {
  return [...mappingBlock(source, name).matchAll(/:\s*\[([^\]]*)\]/g)]
    .flatMap((match) => quotedValues(match[1]));
}

function callBodies(source, name) {
  const marker = `${name}(`;
  const bodies = [];
  let cursor = 0;
  while (cursor < source.length) {
    const callStart = source.indexOf(marker, cursor);
    if (callStart < 0) break;
    const bodyStart = callStart + marker.length;
    let depth = 1;
    let quote = '';
    let escaped = false;
    let index = bodyStart;
    for (; index < source.length; index += 1) {
      const character = source[index];
      if (quote) {
        if (escaped) escaped = false;
        else if (character === '\\') escaped = true;
        else if (character === quote) quote = '';
      } else if (character === '"' || character === "'") {
        quote = character;
      } else if (character === '(') {
        depth += 1;
      } else if (character === ')') {
        depth -= 1;
        if (depth === 0) break;
      }
    }
    if (depth !== 0) throw new Error(`Unclosed backend call: ${name}`);
    bodies.push(source.slice(bodyStart, index));
    cursor = index + 1;
  }
  return bodies;
}

function functionBody(source, name) {
  const start = source.indexOf(`def ${name}(`);
  if (start < 0) throw new Error(`Missing backend function: ${name}`);
  const end = source.indexOf('\ndef ', start + 1);
  return source.slice(start, end < 0 ? source.length : end);
}

function extractProjectApiContract(source) {
  const constructorBodies = callBodies(source, 'ProjectApiError');
  const constructorCodes = constructorBodies
    .map((body) => body.match(/^\s*(["'])([a-z0-9_]+)\1/)?.[2])
    .filter(Boolean);
  const serializerBody = functionBody(source, '_project_api_error_response');
  const serializerCodes = [...serializerBody
    .matchAll(/code\s*=\s*(["'])([a-z0-9_]+)\1/g)]
    .map((match) => match[2]);
  const codes = [...new Set([
    ...constructorCodes,
    ...quotedValues(mappingBlock(source, 'IMPORT_ERROR_STATUS')),
    ...serializerCodes,
  ])].sort();
  const constructorLists = constructorBodies
    .flatMap((body) => [...body.matchAll(/recovery\s*=\s*\[([^\]]*)\]/g)])
    .flatMap((match) => quotedValues(match[1]));
  const serializerLists = [...serializerBody.matchAll(/recovery\s*=\s*\[([^\]]*)\]/g)]
    .flatMap((match) => quotedValues(match[1]));
  const recoveries = [...new Set([
    ...constructorLists,
    ...serializerLists,
    ...mappingListValues(source, 'IMPORT_ERROR_RECOVERY'),
  ])].sort();
  return { codes, recoveries };
}

const backendContract = extractProjectApiContract(backendSource);

test.each([
  ['en', en],
  ['zh-CN', zhCN],
])('%s maps every stable project API error and recovery identifier', (_language, catalog) => {
  for (const code of backendContract.codes) {
    expect(catalog[`projects.errors.${code}`], `error code ${code}`).toEqual(expect.any(String));
    expect(catalog[`projects.errors.${code}`].trim(), `error code ${code}`).not.toBe('');
  }
  for (const recovery of backendContract.recoveries) {
    expect(
      catalog[`projects.recovery.${recovery}`],
      `recovery identifier ${recovery}`,
    ).toEqual(expect.any(String));
    expect(
      catalog[`projects.recovery.${recovery}`].trim(),
      `recovery identifier ${recovery}`,
    ).not.toBe('');
  }
});

test('backend contract parser supports single and double quoted Python literals', () => {
  const fixture = `
IMPORT_ERROR_STATUS = {
    'single_import_error': 400,
    "double_import_error": 409,
}
IMPORT_ERROR_RECOVERY = {
    'single_import_error': ['single_recovery', "double_recovery"],
}

def raise_errors():
    raise ProjectApiError(
        'single_error',
        'message',
        400,
        recovery=['single_action', "double_action"],
    )

def _project_api_error_response(error):
    code = 'internal_error'
    recovery = ['retry', "report_request_id"]

def next_function():
    pass
`;

  expect(extractProjectApiContract(fixture)).toEqual({
    codes: [
      'double_import_error',
      'internal_error',
      'single_error',
      'single_import_error',
    ],
    recoveries: [
      'double_action',
      'double_recovery',
      'report_request_id',
      'retry',
      'single_action',
      'single_recovery',
    ],
  });
});
