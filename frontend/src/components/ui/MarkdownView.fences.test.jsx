import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import MarkdownView, { parseMarkdownBlocks } from './MarkdownView';

describe('retained source fences', () => {
  it('keeps shorter fences, headings and links as literal source', () => {
    const source = '```\n# not a heading\n[not a link](https://example.com)\n````suffix\n';
    const markdown = '`````text\n' + source + '`````\n\n## Evidence';
    const blocks = parseMarkdownBlocks(markdown);
    expect(blocks.map((block) => block.type)).toEqual(['code', 'heading']);
    expect(blocks[0].content).toBe(source.slice(0, -1));
    render(<MarkdownView content={markdown} />);
    expect(screen.queryByRole('link')).toBeNull();
    expect(screen.queryByRole('heading', { name: 'not a heading' })).toBeNull();
  });
  it('accepts longer closing fences and leaves unclosed source literal', () => {
    expect(parseMarkdownBlocks('````py\nx = 1\n`````\nend')[0].content).toBe('x = 1');
    expect(parseMarkdownBlocks('````py\nx = 1\n```')[0].content).toBe('x = 1\n```');
  });
});
