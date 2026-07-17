import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import Button from './Button';
import InlineNotice from './InlineNotice';
import PageState from './PageState';
import StatusBadge from './StatusBadge';

test.each(['primary', 'secondary', 'danger'])('button exposes the %s variant', (variant) => {
  render(<Button variant={variant}>{variant}</Button>);

  expect(screen.getByRole('button', { name: variant })).toHaveAttribute('data-variant', variant);
});

test('button defaults to type button and respects explicit disabled state', () => {
  render(<Button disabled>Do not submit</Button>);

  expect(screen.getByRole('button', { name: 'Do not submit' })).toHaveAttribute('type', 'button');
  expect(screen.getByRole('button', { name: 'Do not submit' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Do not submit' })).not.toHaveAttribute('aria-busy');
});

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

test.each([
  ['loading', 'status'],
  ['empty', 'region'],
  ['error', 'alert'],
  ['needs_input', 'region'],
  ['complete', 'region'],
])('%s page state is named by its heading', (kind, role) => {
  const title = `${kind} project state`;
  render(<PageState kind={kind} title={title} />);

  expect(screen.getByRole(role, { name: title })).toHaveAttribute('data-kind', kind);
});

test('inline danger notice is announced as an alert', () => {
  render(<InlineNotice tone="danger" title="Cannot read project">Check directory permissions.</InlineNotice>);

  expect(screen.getByRole('alert')).toHaveTextContent('Cannot read project');
  expect(screen.getByRole('alert')).toHaveTextContent('Check directory permissions.');
});

test('inline info notice is announced as non-urgent status', () => {
  render(<InlineNotice title="Project saved">You can continue.</InlineNotice>);

  expect(screen.getByRole('status')).toHaveTextContent('Project saved');
});

test('status badge keeps its semantic label visible', () => {
  render(<StatusBadge tone="complete">Ready</StatusBadge>);

  expect(screen.getByText('Ready')).toHaveAttribute('data-tone', 'complete');
});

test('status badge requires visible text or an accessible label', () => {
  expect(() => render(<StatusBadge />)).toThrow(/accessible label/i);

  render(<StatusBadge aria-label="Ready" tone="complete" />);
  expect(screen.getByLabelText('Ready')).toHaveAttribute('data-tone', 'complete');
});

test.each([
  ['false', false],
  ['an empty array', []],
  ['an array containing only null and false', [null, false]],
  ['an empty Fragment', <></>],
  ['nested empty Fragments', <><></></>],
  ['nested whitespace', <><span>   </span></>],
])('status badge rejects %s without an accessible label', (_case, children) => {
  expect(() => render(<StatusBadge>{children}</StatusBadge>)).toThrow(/accessible label/i);
});

test('status badge recursively accepts nested visible text and number zero', () => {
  const { rerender } = render(
    <StatusBadge><><span><>Ready</></span></></StatusBadge>,
  );
  expect(screen.getByText('Ready')).toBeVisible();

  rerender(<StatusBadge>{0}</StatusBadge>);
  expect(screen.getByText('0')).toBeVisible();
});

test('icon-only status badge requires and accepts an accessible label', () => {
  const icon = <span aria-hidden="true" />;
  expect(() => render(<StatusBadge>{icon}</StatusBadge>)).toThrow(/accessible label/i);

  render(<StatusBadge aria-label="Ready">{icon}</StatusBadge>);
  expect(screen.getByLabelText('Ready')).toBeVisible();
});
