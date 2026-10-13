import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { useReducedMotion } from './useReducedMotion';

let media;
beforeEach(() => {
  localStorage.clear();
  media = new EventTarget();
  media.matches = false;
  vi.stubGlobal('matchMedia', () => media);
});
afterEach(() => vi.unstubAllGlobals());

test('explicit reduction updates mounted consumers, persists, and follows the system when reset', () => {
  const first = renderHook(useReducedMotion);
  const second = renderHook(useReducedMotion);
  act(() => first.result.current.setMotionPreference('reduce'));
  expect(second.result.current.reducedMotion).toBe(true);
  expect(document.documentElement).toHaveClass('reduce-motion');
  first.unmount();
  const reopened = renderHook(useReducedMotion);
  expect(reopened.result.current.motionPreference).toBe('reduce');
  act(() => reopened.result.current.setMotionPreference('system'));
  expect(second.result.current.reducedMotion).toBe(false);
  act(() => { media.matches = true; media.dispatchEvent(new Event('change')); });
  expect(second.result.current.reducedMotion).toBe(true);
  expect(document.documentElement).toHaveClass('reduce-motion');
  expect(reopened.result.current.motionPreference).toBe('system');
});

test('cross-tab storage changes update reduction and invalid preferences fall back to the system', () => {
  const hook = renderHook(useReducedMotion);
  act(() => { localStorage.setItem('gt-reduced-motion', 'reduce'); window.dispatchEvent(new Event('storage')); });
  expect(hook.result.current.reducedMotion).toBe(true);
  act(() => { localStorage.setItem('gt-reduced-motion', 'invalid'); window.dispatchEvent(new Event('storage')); });
  expect(hook.result.current.motionPreference).toBe('system');
  expect(hook.result.current.reducedMotion).toBe(false);
});
