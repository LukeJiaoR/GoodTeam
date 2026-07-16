import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

if (globalThis.process?.env?.VITEST) {
  afterEach(() => {
    cleanup();
  });
}
