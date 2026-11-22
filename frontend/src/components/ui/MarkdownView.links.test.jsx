import { describe, expect, it } from 'vitest';
import { renderInlineMarkdown } from './MarkdownView';

describe('markdown link destinations', () => {
  it.each([
    '/projects/P/work', 'file:///tmp/spec.js', 'C:\\task\\spec.js', '//host/path',
  ])('renders unsupported destination %s as text', (path) => {
    const [node] = renderInlineMarkdown(`[Source](${path})`).filter(Boolean);
    expect(node.type).toBe('span');
    expect(node.props.children).toContain(path);
  });

  it.each(['https://github.com/example/repo/pull/1', '#evidence'])('keeps %s clickable', (href) => {
    const [node] = renderInlineMarkdown(`[Evidence](${href})`).filter(Boolean);
    expect(node.type).toBe('a');
    expect(node.props.href).toBe(href);
  });
});
