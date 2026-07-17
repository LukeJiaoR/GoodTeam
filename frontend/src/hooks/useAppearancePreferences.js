import { useEffect, useState } from 'react';
import {
  LANGUAGE_STORAGE_KEY,
  normalizeLanguagePreference,
  resolveLanguage,
  translate,
} from '../i18n';

export function useAppearancePreferences() {
  const [darkMode, setDarkMode] = useState(() => {
    const saved = window.localStorage?.getItem?.('color-scheme');
    if (saved) return saved === 'dark';
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches || false;
  });

  const [theme, setTheme] = useState(() => {
    return window.localStorage?.getItem?.('gt-theme') || 'vivid';
  });

  const [languagePreference, setLanguagePreferenceState] = useState(() => {
    return normalizeLanguagePreference(window.localStorage?.getItem?.(LANGUAGE_STORAGE_KEY));
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
      window.localStorage?.setItem?.('color-scheme', 'dark');
    } else {
      document.documentElement.classList.remove('dark');
      if (meta) meta.content = 'light';
      window.localStorage?.setItem?.('color-scheme', 'light');
    }
  }, [darkMode]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage?.setItem?.('gt-theme', theme);
  }, [theme]);

  useEffect(() => {
    if (typeof window.localStorage?.setItem === 'function') {
      window.localStorage.setItem(LANGUAGE_STORAGE_KEY, languagePreference);
    }
  }, [languagePreference]);

  return {
    darkMode,
    setDarkMode,
    theme,
    setTheme,
    language,
    languagePreference,
    setLanguagePreference,
    t,
  };
}
