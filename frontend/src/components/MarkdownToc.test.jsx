import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import MarkdownToc, { extractHeadings } from './MarkdownToc';

describe('MarkdownToc', () => {
  it('extracts headings properly from markdown string', () => {
    const md = `
# Title
Introduction
## Architecture
Details
### Security
More details
    `;
    const headings = extractHeadings(md);
    expect(headings.length).toBe(3);
    expect(headings[0].text).toBe('Title');
    expect(headings[1].text).toBe('Architecture');
    expect(headings[2].text).toBe('Security');
  });

  it('renders TOC toggle button and expands on click', () => {
    const md = `
# Title
## Section 1
## Section 2
    `;
    render(<MarkdownToc content={md} t={(k) => k} />);
    const toggle = screen.getByRole('button');
    expect(toggle).toBeTruthy();

    expect(screen.queryByText('Section 1')).toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByText('Section 1')).toBeTruthy();
    expect(screen.getByText('Section 2')).toBeTruthy();
  });
});
