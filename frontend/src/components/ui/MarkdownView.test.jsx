import { describe, expect, it } from 'vitest';
import { parseMarkdownBlocks } from './MarkdownView';

describe('structured Markdown list parser', () => {
  it('keeps adjacent marker kinds separate and preserves ordered starts', () => {
    const blocks = parseMarkdownBlocks('3. Implement\n4. Verify\n- Caveat\n+ Another caveat\n7. Continue');
    expect(blocks.map((block) => block.ordered)).toEqual([true, false, true]);
    expect(blocks.map((block) => block.items.length)).toEqual([2, 2, 1]);
    expect(blocks.map((block) => block.start)).toEqual([3, undefined, 7]);
  });

  it('keeps child criteria bound to their original plan step', () => {
    const [root] = parseMarkdownBlocks('1. Build\n   - Preserve input\n     - Include evidence\n   - Verify output\n2. Review');
    expect(root.items.map((item) => item.content)).toEqual(['Build', 'Review']);
    const children = root.items[0].children[0].items;
    expect(children.map((item) => item.content)).toEqual(['Preserve input', 'Verify output']);
    expect(children[0].children[0].items[0].content).toBe('Include evidence');
    expect(root.items[1].children).toEqual([]);
  });

  it('preserves nested checklist states, mixed children and continuation order', () => {
    const [root] = parseMarkdownBlocks('- [x] Parent\n  - [ ] Pending\n  5. Child sequence\n  continuation\n- Next');
    expect(root.items).toHaveLength(2);
    const parent = root.items[0];
    expect(parent.checked).toBe(true);
    expect(parent.children.map((child) => child.type)).toEqual(['list', 'list', 'continuation']);
    expect(parent.children[0].items[0].checked).toBe(false);
    expect(parent.children[1].start).toBe(5);
    expect(parent.children[2].content).toBe('continuation');
  });

  it('ends at blank lines and leaves following blocks intact', () => {
    const blocks = parseMarkdownBlocks('- First\n  continuation\n\n## Result\nDone');
    expect(blocks.map((block) => block.type)).toEqual(['list', 'heading', 'paragraph']);
    expect(blocks[0].items[0].content).toBe('First\ncontinuation');
  });

  it('keeps fenced list-like text literal', () => {
    const blocks = parseMarkdownBlocks('```text\n- literal\n  2. still code\n```\n\n- Actual');
    expect(blocks.map((block) => block.type)).toEqual(['code', 'list']);
    expect(blocks[0].content).toBe('- literal\n  2. still code');
  });

  it('handles tabs, dedenting and multiple child lists without losing text', () => {
    const [root] = parseMarkdownBlocks('- Parent\n\t- Child\n\t\t- Grandchild\n\t3. Sequence\n- Sibling');
    expect(root.items.map((item) => item.content)).toEqual(['Parent', 'Sibling']);
    expect(root.items[0].children.map((child) => child.ordered)).toEqual([false, true]);
    expect(root.items[0].children[0].items[0].children[0].items[0].content).toBe('Grandchild');
  });
});
