import { en } from './locales/en';
import { zhCN } from './locales/zh-CN';
import { runtimeEn, runtimeZhCN } from './locales/runtimeCatalog';

export const LANGUAGE_STORAGE_KEY = 'gt-language';
export const LANGUAGE_PREFERENCE_AUTO = 'auto';
export const LANGUAGE_EN = 'en';
export const LANGUAGE_ZH_CN = 'zh-CN';
export const SUPPORTED_LANGUAGE_PREFERENCES = ['auto', 'en', 'zh-CN'];
export const PRODUCT_I18N_PREFIXES = [
  'product.', 'projects.', 'readiness.', 'work.', 'review.', 'knowledge.',
  'team.',
];

const LOCALES = {
  [LANGUAGE_EN]: { ...en, ...runtimeEn },
  [LANGUAGE_ZH_CN]: { ...zhCN, ...runtimeZhCN },
};

function isCatalogBranch(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function flattenTranslationCatalog(catalog, prefix = '') {
  const flattened = {};
  for (const [key, value] of Object.entries(catalog || {})) {
    const leafKey = prefix ? `${prefix}.${key}` : key;
    if (isCatalogBranch(value)) {
      Object.assign(flattened, flattenTranslationCatalog(value, leafKey));
    } else {
      flattened[leafKey] = value;
    }
  }
  return flattened;
}

function isProductKey(key, prefixes) {
  return prefixes.some((prefix) => key.startsWith(prefix));
}

export function catalogIntegrityIssues(
  requiredCatalog,
  localeCatalog,
  prefixes = PRODUCT_I18N_PREFIXES,
) {
  const required = flattenTranslationCatalog(requiredCatalog);
  const locale = flattenTranslationCatalog(localeCatalog);
  const issues = [];

  for (const key of Object.keys(required).filter((item) => isProductKey(item, prefixes))) {
    if (!Object.prototype.hasOwnProperty.call(locale, key)) {
      issues.push({ type: 'missing', key });
    } else if (typeof locale[key] !== 'string' || locale[key].trim() === '') {
      issues.push({ type: 'empty', key });
    }
  }
  for (const key of Object.keys(locale).filter((item) => isProductKey(item, prefixes))) {
    if (!Object.prototype.hasOwnProperty.call(required, key)) {
      issues.push({ type: 'extra', key });
    }
  }

  const typeOrder = { missing: 0, empty: 1, extra: 2 };
  return issues.sort((first, second) => (
    typeOrder[first.type] - typeOrder[second.type] || first.key.localeCompare(second.key)
  ));
}

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

export function missingTranslationKeys(language, locales = LOCALES, requiredCatalog = LOCALES[LANGUAGE_EN]) {
  return catalogIntegrityIssues(requiredCatalog, locales[language] || {})
    .map(({ type, key }) => `${type}:${key}`);
}
