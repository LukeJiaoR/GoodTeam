import { expect, test } from 'vitest';
import {
  catalogIntegrityIssues,
  flattenTranslationCatalog,
  missingTranslationKeys,
} from './index';

test.each(['en', 'zh-CN'])('%s has every redesigned product key', (language) => {
  expect(missingTranslationKeys(language)).toEqual([]);
});

test('catalog leaves are flattened recursively', () => {
  expect(flattenTranslationCatalog({
    product: {
      actions: { load: 'Load project' },
      title: 'Projects',
    },
  })).toEqual({
    'product.actions.load': 'Load project',
    'product.title': 'Projects',
  });
});

test('catalog integrity reports nested missing, empty, and locale-only product leaves', () => {
  const required = {
    product: {
      actions: {
        load: 'Load project',
        retry: 'Try again',
      },
    },
    legacy: { title: 'Ignored legacy key' },
  };
  const locale = {
    product: {
      actions: {
        load: '加载项目',
        retry: '   ',
        localeOnly: '仅本地语言存在',
      },
    },
  };

  expect(catalogIntegrityIssues(required, locale)).toEqual([
    { type: 'empty', key: 'product.actions.retry' },
    { type: 'extra', key: 'product.actions.localeOnly' },
  ]);
  expect(missingTranslationKeys('fixture', { fixture: locale }, required)).toEqual([
    'empty:product.actions.retry',
    'extra:product.actions.localeOnly',
  ]);

  delete locale.product.actions.load;
  expect(catalogIntegrityIssues(required, locale)).toContainEqual({
    type: 'missing',
    key: 'product.actions.load',
  });
});
