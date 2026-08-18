import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import MarkdownView, { isTableSeparatorRow, parseMarkdownBlocks, renderInlineMarkdown } from './MarkdownView';

describe('isTableSeparatorRow', () => {
  test('correctly identifies valid and invalid GFM table separators', () => {
    expect(isTableSeparatorRow('|---|---|')).toBe(true);
    expect(isTableSeparatorRow('|:---|:---:|---:|')).toBe(true);
    expect(isTableSeparatorRow('| --- | --- |')).toBe(true);
    expect(isTableSeparatorRow('| not-a-separator | row |')).toBe(false);
    expect(isTableSeparatorRow('regular text')).toBe(false);
    expect(isTableSeparatorRow('')).toBe(false);
  });
});

describe('renderInlineMarkdown', () => {
  test('handles bold, italic, code, and links correctly', () => {
    const text = 'This is **bold** and *italic* with `inline_code` and [link](https://goodteam.dev)';
    const { container } = render(<div>{renderInlineMarkdown(text)}</div>);

    expect(container.querySelector('strong')).toHaveTextContent('bold');
    expect(container.querySelector('em')).toHaveTextContent('italic');
    expect(container.querySelector('code')).toHaveTextContent('inline_code');
    const link = container.querySelector('a');
    expect(link).toHaveTextContent('link');
    expect(link).toHaveAttribute('href', 'https://goodteam.dev');
  });
});

describe('parseMarkdownBlocks', () => {
  test('parses headings, paragraphs, lists, tables, and code blocks', () => {
    const md = `
# Title H1
## Section H2

This is a paragraph with **strong** points.

- Item 1
- Item 2
- [x] Done task
- [ ] Todo task

1. First
2. Second

\`\`\`python
def hello():
    return "world"
\`\`\`

> A wisdom blockquote

| Col 1 | Col 2 |
|---|---|
| A | B |
| C | D |

---
`;

    const blocks = parseMarkdownBlocks(md);
    expect(blocks.length).toBeGreaterThanOrEqual(8);
    expect(blocks[0]).toEqual({ type: 'heading', level: 1, content: 'Title H1' });
    expect(blocks[1]).toEqual({ type: 'heading', level: 2, content: 'Section H2' });
  });

  test('does not skip lines on standalone single pipe rows', () => {
    const md = `
| standalone pipe text |
Next regular paragraph line.
`;
    const blocks = parseMarkdownBlocks(md);
    expect(blocks.length).toBe(1);
    expect(blocks[0].type).toBe('paragraph');
    expect(blocks[0].content).toContain('| standalone pipe text |');
    expect(blocks[0].content).toContain('Next regular paragraph line.');
  });

  test('does not drop rows when second line is not a separator row', () => {
    const md = `
| header row |
| data row without separator |
`;
    const blocks = parseMarkdownBlocks(md);
    expect(blocks.length).toBe(1);
    expect(blocks[0].type).toBe('paragraph');
    expect(blocks[0].content).toContain('| header row |');
    expect(blocks[0].content).toContain('| data row without separator |');
  });
});

describe('MarkdownView Component', () => {
  test('renders full structured markdown content accessibly', () => {
    const content = `
# PRD Header

Introduction text.

## Features
- First feature
- Second feature

\`\`\`json
{"status": "ok"}
\`\`\`

| Key | Value |
|---|---|
| test | 123 |
`;

    render(<MarkdownView content={content} />);

    expect(screen.getByRole('heading', { level: 1, name: 'PRD Header' })).toBeVisible();
    expect(screen.getByRole('heading', { level: 2, name: 'Features' })).toBeVisible();
    expect(screen.getByText('Introduction text.')).toBeVisible();
    expect(screen.getByText('First feature')).toBeVisible();
    expect(screen.getByText('{"status": "ok"}')).toBeVisible();
    expect(screen.getByText('Key')).toBeVisible();
    expect(screen.getByText('123')).toBeVisible();
  });
});
