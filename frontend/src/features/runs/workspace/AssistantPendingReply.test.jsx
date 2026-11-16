import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import AssistantPendingReply from './AssistantPendingReply';
import { en } from '../../../i18n/locales/en';
import { zhCN } from '../../../i18n/locales/zh-CN';

const t = key => en[key] || key;

test('native activity renders without crashing and Stop remains usable', async () => {
  const stop = vi.fn().mockResolvedValue();
  const { rerender } = render(<AssistantPendingReply t={t} onStop={stop}
    request={{ activity: { kind: 'reasoning', status: 'started' } }} />);
  expect(screen.getByText('reasoning: started')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: t('assistant.project.stopResponse') }));
  await waitFor(() => expect(stop).toHaveBeenCalledOnce());
  rerender(<AssistantPendingReply t={t} request={{ activity: { kind: 'reasoning', status: 'completed' } }} />);
  expect(screen.getByText('reasoning: completed')).toBeVisible();
  rerender(<AssistantPendingReply t={t} request={{ activity: 'Inspecting the task' }} />);
  expect(screen.getByText('Inspecting the task')).toBeVisible();
});

test('both languages explain authentication and Manager interruption errors', () => {
  for (const locale of [en, zhCN]) {
    for (const code of ['authentication_failed', 'manager_interrupted', 'manager_stopped', 'manager_runtime_changed', 'operation_conflict']) {
      expect(locale[`assistant.error.${code}`]).toBeTruthy();
    }
  }
});
