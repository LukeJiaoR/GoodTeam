import { expect, test } from 'vitest';
import { missingTranslationKeys } from './index';

test.each(['en', 'zh-CN'])('%s has every redesigned product key', (language) => {
  expect(missingTranslationKeys(language)).toEqual([]);
});
