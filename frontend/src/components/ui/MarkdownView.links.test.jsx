import { describe, expect, it } from 'vitest';
import { renderInlineMarkdown } from './MarkdownView';

describe('markdown link destinations', () => {
  it.each(['/Users/operator/task/output/spec.js', '/tmp/task/file.patch', '//host/path', 'file:///tmp/spec.js', 'C:\\task\\spec.js', '/projects/P/../work', '/projects/%2e%2e/work', '/projects/P%2f..%2fUsers/work', '/projects/P%5c../work', '/projects/%252e%252e/work', '/projects/P/work/extra', '/projects/P%zz/work'])('does not turn local or ambiguous source %s into a web request', (path) => {
    const [node] = renderInlineMarkdown(`[Source](${path})`).filter(Boolean);
    expect(node.type).not.toBe('a');
    expect(node.props.children).toContain(path);
  });

  it.each(['https://github.com/example/repo/pull/1', '#evidence', '/api/delivery-artifact?project=P&run=R&key=qa_report&version=sha256:abc', '/api/artifact?key=prd', '/artifact?key=prd', '/projects', '/projects/P/work/runs/R', '/projects/P/review', '/projects/P/review/Q?tab=detail#evidence', '/projects/P/knowledge', '/projects/P/team', '/projects/P/settings/general', '/settings', '/legacy?project=P', '/?project=P', '/projects/%E9%A1%B9%E7%9B%AE/work'])('preserves an explicit supported web destination %s', (href) => {
    const [node] = renderInlineMarkdown(`[Evidence](${href})`).filter(Boolean);
    expect(node.type).toBe('a');
    expect(node.props.href).toBe(href);
  });
});
