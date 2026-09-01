import { defineConfig } from 'vitest/config';

/**
 * §13.5 — the pricing service is where money leaks. 100% on branches is an
 * acceptance criterion, so it is enforced here rather than aspired to in a doc.
 */
export default defineConfig({
  test: {
    include: ['src/**/*.test.ts'],
    coverage: {
      provider: 'v8',
      include: ['src/**/*.ts'],
      exclude: ['src/**/*.test.ts', 'src/index.ts', 'src/testing.ts'],
      reporter: ['text', 'json-summary'],
      thresholds: { branches: 100, functions: 100, lines: 100, statements: 100 },
    },
  },
});
