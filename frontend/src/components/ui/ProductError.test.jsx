import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { translate } from '../../i18n';
import ProductError from './ProductError';

it('keeps request diagnostics out of the conversation without manufacturing a source-evidence entry', () => {
  const error = Object.assign(new Error('Native check failed at /private/worktree/check.py'), {
    code: 'new_internal_failure', requestId: 'request-original',
    details: { receipt: 'sha256:original', diagnostic: 'Traceback: original failure', request: { prompt: 'Internal request body' } },
    recovery: ['new_internal_command'],
  });
  const retry = vi.fn();
  const t = (key, params) => translate('zh-CN', key, params);
  const { container } = render(<ProductError error={error} onRetry={retry} t={t} />);
  expect(screen.getByRole('alert')).toHaveTextContent(t('product.errors.generic'));
  expect(container).not.toHaveTextContent(error.message);
  expect(container).not.toHaveTextContent(error.code);
  expect(container).not.toHaveTextContent(error.requestId);
  expect(container).not.toHaveTextContent(error.recovery[0]);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: t('product.evidence.open') })).not.toBeInTheDocument();
  expect(container).not.toHaveTextContent(error.details.request.prompt);
  fireEvent.click(screen.getByRole('button', { name: t('product.actions.retry') }));
  expect(retry).toHaveBeenCalledOnce();
});
