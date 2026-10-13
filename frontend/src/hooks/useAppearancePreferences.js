import { useEffect, useState } from 'react';
import { useReducedMotion } from './useReducedMotion';
import {
  LANGUAGE_STORAGE_KEY,
  normalizeLanguagePreference,
  resolveLanguage,
  translate,
} from '../i18n';

export function useAppearancePreferences() {
  const motion = useReducedMotion();
  const [darkMode, setDarkMode] = useState(() => {
    const saved = typeof window.localStorage?.getItem === 'function'
      ? window.localStorage.getItem('color-scheme')
      : null;
    if (saved) return saved === 'dark';
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches || false;
  });

  const [languagePreference, setLanguagePreferenceState] = useState(() => {
    const saved = typeof window.localStorage?.getItem === 'function'
      ? window.localStorage.getItem(LANGUAGE_STORAGE_KEY)
      : null;
    return normalizeLanguagePreference(saved);
  });
  const language = resolveLanguage(languagePreference);
  const t = (key, params) => translate(language, key, params);
  const setLanguagePreference = (preference) => {
    setLanguagePreferenceState(normalizeLanguagePreference(preference));
  };

  useEffect(() => {
    const meta = document.querySelector('meta[name="color-scheme"]');
    if (darkMode) {
      document.documentElement.classList.add('dark');
      if (meta) meta.content = 'dark';
      if (typeof window.localStorage?.setItem === 'function') {
        window.localStorage.setItem('color-scheme', 'dark');
      }
    } else {
      document.documentElement.classList.remove('dark');
      if (meta) meta.content = 'light';
      if (typeof window.localStorage?.setItem === 'function') {
        window.localStorage.setItem('color-scheme', 'light');
      }
    }
  }, [darkMode]);

  useEffect(() => {
    if (typeof window.localStorage?.setItem === 'function') {
      window.localStorage.setItem(LANGUAGE_STORAGE_KEY, languagePreference);
    }
  }, [languagePreference]);

  return {
    ...motion,
    darkMode,
    setDarkMode,
    language,
    languagePreference,
    setLanguagePreference,
    t,
  };
}
