import { useEffect, useSyncExternalStore } from 'react';
import { normalizeMotionPreference, shouldReduceMotion } from '../lib/motion/preferences';

const STORAGE_KEY = 'gt-reduced-motion';
const CHANGE_EVENT = 'gt-motion-preference-change';
const QUERY = '(prefers-reduced-motion: reduce)';

function preference() {
  return normalizeMotionPreference(window.localStorage?.getItem?.(STORAGE_KEY));
}

function subscribe(callback) {
  const media = window.matchMedia?.(QUERY);
  media?.addEventListener?.('change', callback);
  window.addEventListener('storage', callback);
  window.addEventListener(CHANGE_EVENT, callback);
  return () => {
    media?.removeEventListener?.('change', callback);
    window.removeEventListener('storage', callback);
    window.removeEventListener(CHANGE_EVENT, callback);
  };
}

export function useReducedMotion() {
  const motionPreference = useSyncExternalStore(subscribe, preference, () => 'system');
  const reducedMotion = useSyncExternalStore(subscribe,
    () => shouldReduceMotion(preference(), window.matchMedia?.(QUERY)?.matches), () => false);
  useEffect(() => {
    document.documentElement.classList.toggle('reduce-motion', reducedMotion);
  }, [reducedMotion]);
  const setMotionPreference = (value) => {
    window.localStorage?.setItem?.(STORAGE_KEY, normalizeMotionPreference(value));
    window.dispatchEvent(new Event(CHANGE_EVENT));
  };
  return { motionPreference, reducedMotion, setMotionPreference };
}
