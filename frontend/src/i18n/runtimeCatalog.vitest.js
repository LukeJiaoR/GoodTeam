import { describe, expect, it } from 'vitest';
import { catalogIntegrityIssues, missingTranslationKeys, translate } from './index';
import { runtimeEn, runtimeZhCN } from './locales/runtimeCatalog';

describe('runtime catalog integration', () => {
  it('has matching, nonempty English and Chinese catalogs', () => {
    expect(catalogIntegrityIssues(runtimeEn, runtimeZhCN)).toEqual([]);
    expect(catalogIntegrityIssues(runtimeZhCN, runtimeEn)).toEqual([]);
  });

  it('resolves all runtime strings through the actual product translator', () => {
    for (const [language, catalog] of [['en', runtimeEn], ['zh-CN', runtimeZhCN]]) {
      for (const [key, value] of Object.entries(catalog)) expect(translate(language, key)).toBe(value);
      expect(missingTranslationKeys(language)).toEqual([]);
    }
  });
});
