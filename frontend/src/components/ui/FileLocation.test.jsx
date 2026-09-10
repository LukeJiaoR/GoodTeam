import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { translate } from '../../i18n';
import { fileName, isAbsoluteFilePath } from '../../lib/presentation/filePresentation';
import FileLocation from './FileLocation';

const t = (key, values) => translate('en', key, values);
afterEach(() => vi.restoreAllMocks());

test('keeps the path available behind a disclosure and copies the exact original value', async () => {
  const user = userEvent.setup();
  const path = 'C:\\Users\\Luke\\My Project\\设计稿\\prd.md';
  const copy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue();
  render(<FileLocation path={path} t={t} />);
  expect(screen.getByText(path)).not.toBeVisible();
  await user.click(screen.getByText('File location'));
  expect(screen.getByText(path)).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Copy path for prd.md' }));
  expect(copy).toHaveBeenCalledWith(path);
  expect(screen.getByRole('status')).toHaveTextContent('Path copied');
});

test('shows a recoverable copy failure and Escape closes only the path disclosure', async () => {
  const user = userEvent.setup();
  vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('Permission denied'));
  const parentKeyDown = vi.fn();
  render(<div onKeyDown={parentKeyDown}><FileLocation path="/private/demo/prd.md" t={t} /></div>);
  await user.click(screen.getByText('File location'));
  await user.click(screen.getByRole('button', { name: 'Copy path for prd.md' }));
  expect(screen.getByRole('alert')).toHaveTextContent('Select the path above');
  await user.keyboard('{Escape}');
  expect(screen.getByText('/private/demo/prd.md')).not.toBeVisible();
  expect(screen.getByText('File location').closest('summary')).toHaveFocus();
  expect(parentKeyDown).not.toHaveBeenCalled();
  await user.keyboard('{Escape}');
  expect(parentKeyDown).toHaveBeenCalledOnce();
});

test('recognizes local paths and preserves readable names across operating systems', () => {
  expect(fileName('/Users/luke/项目/prd.md')).toBe('prd.md');
  expect(fileName('C:\\Work\\Demo\\')).toBe('Demo');
  expect(fileName('\\\\server\\share\\report.pdf')).toBe('report.pdf');
  expect(isAbsoluteFilePath('/private/repo/file.md')).toBe(true);
  expect(isAbsoluteFilePath('C:\\Work\\file.md')).toBe(true);
  expect(isAbsoluteFilePath('https://example.com/file.md')).toBe(false);
  expect(isAbsoluteFilePath('sha256:abcd')).toBe(false);
});
