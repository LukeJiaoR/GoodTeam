import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import Button from './Button';
import InlineNotice from './InlineNotice';
import PageState from './PageState';
import StatusBadge from './StatusBadge';

test('button exposes disabled and busy state accessibly', () => {
  render(<Button busy>Load project</Button>);

  expect(screen.getByRole('button', { name: 'Load project' })).toBeDisabled();
  expect(screen.getByRole('button')).toHaveAttribute('aria-busy', 'true');
});

test('button preserves native attributes while busy', () => {
  render(<Button busy name="load-project" value="selected">Load project</Button>);

  expect(screen.getByRole('button')).toHaveAttribute('name', 'load-project');
  expect(screen.getByRole('button')).toHaveAttribute('value', 'selected');
});

test('page error state keeps the recovery action inline', () => {
  render(
    <PageState
      kind="error"
      title="Cannot read project"
      action={<button>Choose another directory</button>}
    />,
  );

  expect(screen.getByRole('heading', { name: 'Cannot read project' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Choose another directory' })).toBeVisible();
});

test('loading page state announces that its region is busy', () => {
  render(<PageState kind="loading" title="Reading project" />);

  expect(screen.getByRole('status')).toHaveAttribute('aria-busy', 'true');
  expect(screen.getByRole('heading', { name: 'Reading project' })).toBeVisible();
});

test('inline danger notice is announced as an alert', () => {
  render(<InlineNotice tone="danger" title="Cannot read project">Check directory permissions.</InlineNotice>);

  expect(screen.getByRole('alert')).toHaveTextContent('Cannot read project');
  expect(screen.getByRole('alert')).toHaveTextContent('Check directory permissions.');
});

test('status badge keeps its semantic label visible', () => {
  render(<StatusBadge tone="complete">Ready</StatusBadge>);

  expect(screen.getByText('Ready')).toHaveAttribute('data-tone', 'complete');
});
