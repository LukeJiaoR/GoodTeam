import { afterEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import MarkdownView from './MarkdownView';
import { InteractiveTable } from './ChatRichContent';
import { parseChatRichContent } from './chatRichContentParser';

const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
afterEach(() => {
  if (originalClipboard) Object.defineProperty(navigator, 'clipboard', originalClipboard);
  else delete navigator.clipboard;
});
const fenced = value => '```goodteam-ui\n' + JSON.stringify(value) + '\n```';
const chart = { type: 'chart', title: 'Task comparison', columns: ['Stage', 'Planned', 'Actual'], rows: [['Design', 5, 3], ['Build', 8, 4]] };

it('sorts numbers accurately, restores source order, and copies the filtered view', async () => {
  const writeText = vi.fn().mockResolvedValue();
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  render(<InteractiveTable headers={['Name', 'Hours']} rows={[
    ['Design B', '10'], ['Design A', '2.5'], ['Build', '2.05'],
  ]} />);
  const names = () => screen.getAllByRole('row').slice(1).map(row => within(row).getAllByRole('cell')[0].textContent);
  fireEvent.click(screen.getByRole('button', { name: 'Hours' }));
  expect(names()).toEqual(['Build', 'Design A', 'Design B']);
  expect(screen.getByRole('columnheader', { name: 'Hours' })).toHaveAttribute('aria-sort', 'ascending');
  fireEvent.click(screen.getByRole('button', { name: 'Hours' }));
  expect(names()).toEqual(['Design B', 'Design A', 'Build']);
  fireEvent.click(screen.getByRole('button', { name: 'Hours' }));
  expect(names()).toEqual(['Design B', 'Design A', 'Build']);
  fireEvent.change(screen.getByRole('searchbox', { name: 'Filter table' }), { target: { value: 'design' } });
  expect(names()).toEqual(['Design B', 'Design A']);
  fireEvent.click(screen.getByRole('button', { name: 'Copy data' }));
  await screen.findByText('Copied');
  expect(writeText).toHaveBeenCalledWith('Name\tHours\nDesign B\t10\nDesign A\t2.5');
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'missing' } });
  expect(screen.getByText('No matching rows')).toBeVisible();
});

it('preserves extra row cells and quotes tabs when copying spreadsheet data', async () => {
  const writeText = vi.fn().mockResolvedValue();
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  render(<InteractiveTable headers={['Name']} rows={[["A\tB", 'extra']]} />);
  expect(screen.getByRole('columnheader', { name: 'Column 2' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Copy data' }));
  await screen.findByText('Copied');
  expect(writeText).toHaveBeenCalledWith('Name\tColumn 2\n"A\tB"\textra');
});

it('reports clipboard failure without claiming success', async () => {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } });
  render(<InteractiveTable headers={['Name']} rows={[['Design']]} />);
  fireEvent.click(screen.getByRole('button', { name: 'Copy data' }));
  expect(await screen.findByText('Copy failed; try again')).toBeVisible();
  expect(screen.queryByText('Copied')).not.toBeInTheDocument();
});

it('changes the inspected chart metric without changing source data', () => {
  render(<MarkdownView content={fenced(chart)} interactive />);
  fireEvent.click(screen.getByRole('button', { name: 'Build 8' }));
  expect(screen.getByText('Build · Planned: 8')).toBeVisible();
  fireEvent.change(screen.getByRole('combobox', { name: 'Metric' }), { target: { value: '2' } });
  expect(screen.getByText('Build · Actual: 4')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Design 3' }));
  expect(screen.getByText('Design · Actual: 3')).toBeVisible();
  expect(chart.rows).toEqual([['Design', 5, 3], ['Build', 8, 4]]);
});

it('falls back to literal code for incomplete or unsupported rich output', () => {
  for (const source of ['{', JSON.stringify({ ...chart, rows: [['Bad', 'not numeric', 4]] }), JSON.stringify({ ...chart, type: 'html' })]) {
    expect(parseChatRichContent(source)).toBeNull();
  }
  render(<MarkdownView content={'```goodteam-ui\n{"type":"html","body":"<script>bad()</script>"}\n```'} interactive />);
  expect(screen.getByText(/<script>bad/)).toBeVisible();
  expect(document.querySelector('script')).toBeNull();
  expect(document.querySelector('iframe')).toBeNull();
});

it('renders model-provided HTML and command fields as inert data', () => {
  render(<MarkdownView content={fenced({ type: 'cards', title: 'Preview', items: [{ title: 'Text', body: '<img src=x onerror="bad()">' }], action: 'approve' })} interactive />);
  fireEvent.click(screen.getByText('Preview', { selector: 'summary' }));
  expect(screen.getByText('<img src=x onerror="bad()">')).toBeVisible();
  expect(document.querySelector('img')).toBeNull();
  expect(screen.queryByRole('button', { name: /approve/i })).not.toBeInTheDocument();
});
