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
  return [...source.matchAll(/"([a-z][a-z0-9_]*)"/g)].map((match) => match[1]);
}

function mappingBlock(name) {
  const match = backendSource.match(new RegExp(`${name} = \\{([\\s\\S]*?)\\n\\}`));
  if (!match) throw new Error(`Missing backend mapping: ${name}`);
  return match[1];
}

function mappingListValues(name) {
  return [...mappingBlock(name).matchAll(/:\s*\[([^\]]*)\]/g)]
    .flatMap((match) => quotedValues(match[1]));
}

function callBodies(name) {
  const marker = `${name}(`;
  const bodies = [];
  let cursor = 0;
  while (cursor < backendSource.length) {
    const callStart = backendSource.indexOf(marker, cursor);
    if (callStart < 0) break;
    const bodyStart = callStart + marker.length;
    let depth = 1;
    let quote = '';
    let escaped = false;
    let index = bodyStart;
    for (; index < backendSource.length; index += 1) {
      const character = backendSource[index];
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
    bodies.push(backendSource.slice(bodyStart, index));
    cursor = index + 1;
  }
  return bodies;
}

function functionBody(name) {
  const start = backendSource.indexOf(`def ${name}(`);
  if (start < 0) throw new Error(`Missing backend function: ${name}`);
  const end = backendSource.indexOf('\ndef ', start + 1);
  return backendSource.slice(start, end < 0 ? backendSource.length : end);
}

function projectApiErrorCodes() {
  const constructorCodes = callBodies('ProjectApiError')
    .map((body) => body.match(/^\s*"([a-z0-9_]+)"/)?.[1])
    .filter(Boolean);
  const serializerCodes = [...functionBody('_project_api_error_response')
    .matchAll(/code\s*=\s*"([a-z0-9_]+)"/g)]
    .map((match) => match[1]);
  return [...new Set([
    ...constructorCodes,
    ...quotedValues(mappingBlock('IMPORT_ERROR_STATUS')),
    ...serializerCodes,
  ])].sort();
}

function projectApiRecoveryIds() {
  const constructorLists = callBodies('ProjectApiError')
    .flatMap((body) => [...body.matchAll(/recovery\s*=\s*\[([^\]]*)\]/g)])
    .flatMap((match) => quotedValues(match[1]));
  const serializerLists = [...functionBody('_project_api_error_response')
    .matchAll(/recovery\s*=\s*\[([^\]]*)\]/g)]
    .flatMap((match) => quotedValues(match[1]));
  return [...new Set([
    ...constructorLists,
    ...serializerLists,
    ...mappingListValues('IMPORT_ERROR_RECOVERY'),
  ])].sort();
}

test.each([
  ['en', en],
  ['zh-CN', zhCN],
])('%s maps every stable project API error and recovery identifier', (_language, catalog) => {
  for (const code of projectApiErrorCodes()) {
    expect(catalog[`projects.errors.${code}`], `error code ${code}`).toEqual(expect.any(String));
    expect(catalog[`projects.errors.${code}`].trim(), `error code ${code}`).not.toBe('');
  }
  for (const recovery of projectApiRecoveryIds()) {
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
