import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: [
      'src/**/*.spec.ts',
      'electron/**/*.spec.ts',
      'tests/**/*.spec.ts'
    ],
    exclude: [
      '**/node_modules/**',
      '**/dist/**',
      '**/dist-electron/**',
      '**/release-electron/**',
      '**/runtime/**'
    ]
  }
});
