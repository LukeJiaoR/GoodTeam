import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import MarkdownView from './MarkdownView';

it('links an explicit Markdown destination and leaves a bare path as text', () => {
  const path = '/projects/P/work';
  render(<MarkdownView content={`${path}\n\n[Workbench](${path})`} />);

  expect(screen.getByText(path)).toBeInTheDocument();
  expect(screen.getAllByRole('link')).toHaveLength(1);
  expect(screen.getByRole('link', { name: 'Workbench' })).toHaveAttribute('href', path);
});
