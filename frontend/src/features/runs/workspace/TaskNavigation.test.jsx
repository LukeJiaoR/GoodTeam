import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import TaskNavigation from './TaskNavigation';
import { en } from '../../../i18n/locales/en';

test('project conversation, current iteration and historical iterations keep their own identities and status', async () => {
  const user = userEvent.setup();
  const select = vi.fn();
  const props = { currentRunId: 'current', currentTitle: 'Current work', currentStatus: 'running', selectedRunId: 'past',
    runs: [{ run_id: 'current', title: 'Current work', status: 'active' },
      ...Array.from({ length: 4 }, (_, i) => ({ run_id: i ? `past-${i}` : 'past', title: `Past work ${i}`, status: 'archived' }))],
    onSelect: select, t: key => en[key] || key };
  const { rerender } = render(<TaskNavigation {...props} />);
  const history = screen.getByRole('region', { name: 'Past iterations' });
  expect(within(history).getAllByRole('listitem')).toHaveLength(3);
  expect(within(history).getByRole('button', { name: /Past work 0/ })).toHaveAttribute('aria-current', 'page');
  expect(screen.getByRole('region', { name: 'Current iteration' })).toHaveTextContent('Current work');
  await user.click(screen.getByRole('button', { name: 'Show more…' }));
  expect(within(history).getAllByRole('listitem')).toHaveLength(4);
  await user.click(within(history).getByRole('button', { name: /Past work 3/ }));
  expect(select).toHaveBeenLastCalledWith('past-3');
  rerender(<TaskNavigation {...props} query="Past work 0" />);
  expect(screen.queryByRole('region', { name: 'Current iteration' })).not.toBeInTheDocument();
  expect(within(history).getAllByRole('listitem')).toHaveLength(1);
  await user.click(screen.getByRole('button', { name: 'Project conversation', exact: true }));
  expect(select).toHaveBeenLastCalledWith('');
});
