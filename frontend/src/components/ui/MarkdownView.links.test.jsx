import { describe, expect, it } from 'vitest';
import { renderInlineMarkdown } from './MarkdownView';

describe('markdown link destinations', () => {
  it('keeps a bare path as text and an explicit relative link clickable', () => {
    const path = '/projects/P/work';
    expect(renderInlineMarkdown(path)).toEqual([path]);
    const [node] = renderInlineMarkdown(`[Workbench](${path})`).filter(Boolean);
    expect(node.type).toBe('a');
    expect(node.props.href).toBe(path);
  });

  it('renders a local file URL as text', () => {
    const path = 'file:///tmp/spec.js';
    const [node] = renderInlineMarkdown(`[Source](${path})`).filter(Boolean);
    expect(node.type).toBe('span');
    expect(node.props.children).toContain(path);
  });

  it('renders a script URL as text', () => {
    const [node] = renderInlineMarkdown('[Source](javascript:0)').filter(Boolean);
    expect(node.type).toBe('span');
  });

  it.each(['https://github.com/example/repo/pull/1', '#evidence', 'mailto:team@example.com'])('keeps %s clickable', (href) => {
    const [node] = renderInlineMarkdown(`[Evidence](${href})`).filter(Boolean);
    expect(node.type).toBe('a');
    expect(node.props.href).toBe(href);
  });
});
