import { describe, expect, it } from 'vitest';
import { executionEvidenceHref } from './executionEvidence';

const href = '/review-workbench/exec-123?project_root=%2Fworkspace%2Fdemo';
const mission = { source: 'operator_queue', execution_id: 'exec-123', execution_evidence_href: href };

describe('bound execution evidence navigation', () => {
  it('uses the backend-provided relative URL without synthesizing an identity', () => {
    expect(executionEvidenceHref(mission)).toBe(href);
    expect(executionEvidenceHref({ ...mission, status: 'failed' })).toBe(href);
    expect(executionEvidenceHref({ ...mission, execution_evidence_href: '' })).toBe('');
  });
  it.each([
    null, {}, { source: 'workflow', run_id: 'exec-123', execution_evidence_href: href },
    { ...mission, execution_id: '' }, { ...mission, execution_id: 'other' },
    { ...mission, read_only: true },
  ])('does not substitute workflow, missing or historical identity: %j', (value) => {
    expect(executionEvidenceHref(value)).toBe('');
  });
  it.each([
    'https://evil.example/review-workbench/exec-123?project_root=%2Fworkspace',
    '//evil.example/review-workbench/exec-123?project_root=%2Fworkspace',
    'javascript:alert(1)', '/review-workbench/exec-123/extra?project_root=%2Fworkspace',
    '/review-workbench/../exec-123?project_root=%2Fworkspace',
    '/review-workbench/exec-123?project_root=%2Fworkspace#other',
    '/review-workbench/exec-123?project_root=%2Fworkspace&redirect=https://evil.example',
    '/review-workbench/exec-123?project_root=%2Fworkspace&project_root=%2Fother',
    '/review-workbench/exec-123', '/review-workbench/exec-123?project_root=',
    '/review-workbench/exec-123?project_root=relative',
    '/review-workbench/exec-123?project_root=%2F%2Fother',
    '/review-workbench/exec-123?project_root=%2Fworkspace%00',
    '/review-workbench/exec-123?project_root=%2Fworkspace%5Cother',
    '/review-workbench/%E0%A4%A?project_root=%2Fworkspace',
    '/review-workbench/exec-123\\?project_root=%2Fworkspace',
    '/review-workbench/exec-123\n?project_root=%2Fworkspace',
  ])('rejects unsafe or ambiguous destination %s', (url) => {
    expect(executionEvidenceHref({ ...mission, execution_evidence_href: url })).toBe('');
  });
});
