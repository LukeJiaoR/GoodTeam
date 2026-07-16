import { en } from './locales/en';
import { zhCN } from './locales/zh-CN';

export const LANGUAGE_STORAGE_KEY = 'gt-language';
export const LANGUAGE_PREFERENCE_AUTO = 'auto';
export const LANGUAGE_EN = 'en';
export const LANGUAGE_ZH_CN = 'zh-CN';
export const SUPPORTED_LANGUAGE_PREFERENCES = ['auto', 'en', 'zh-CN'];
export const PRODUCT_I18N_PREFIXES = ['product.', 'projects.', 'readiness.'];

const LOCALES = {
  [LANGUAGE_EN]: en,
  [LANGUAGE_ZH_CN]: zhCN,
};

export function normalizeLanguagePreference(value) {
  return SUPPORTED_LANGUAGE_PREFERENCES.includes(value) ? value : LANGUAGE_PREFERENCE_AUTO;
}

export function browserLanguagesFromNavigator(navigatorObject = globalThis.navigator) {
  if (!navigatorObject) return [];
  if (Array.isArray(navigatorObject.languages) && navigatorObject.languages.length > 0) {
    return navigatorObject.languages;
  }
  return navigatorObject.language ? [navigatorObject.language] : [];
}

export function resolveLanguage(preference, browserLanguages = browserLanguagesFromNavigator()) {
  const normalizedPreference = normalizeLanguagePreference(preference);
  if (normalizedPreference === LANGUAGE_EN || normalizedPreference === LANGUAGE_ZH_CN) {
    return normalizedPreference;
  }

  const firstLanguage = String(browserLanguages.find(Boolean) || '').toLowerCase();
  if (firstLanguage.startsWith('zh')) {
    return 'zh-CN';
  }
  return 'en';
}

function interpolate(template, params = {}) {
  return String(template).replace(/\{\{(\w+)\}\}/g, (_, key) => (
    Object.prototype.hasOwnProperty.call(params, key) ? String(params[key]) : ''
  ));
}

export function translate(language, key, params = {}) {
  const resolvedLanguage = language === LANGUAGE_ZH_CN ? LANGUAGE_ZH_CN : LANGUAGE_EN;
  const template = LOCALES[resolvedLanguage]?.[key] ?? LOCALES[LANGUAGE_EN]?.[key] ?? key;
  return interpolate(template, params);
}

export function missingTranslationKeys(language) {
  const locale = LOCALES[language] || {};
  return Object.keys(en)
    .filter((key) => PRODUCT_I18N_PREFIXES.some((prefix) => key.startsWith(prefix)))
    .filter((key) => !Object.prototype.hasOwnProperty.call(locale, key))
    .sort();
}
