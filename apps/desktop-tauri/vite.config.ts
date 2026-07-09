import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  clearScreen: false,
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          const normalized = id.replace(/\\/g, '/');
          if (
            normalized.includes('/node_modules/@codemirror/view/') ||
            normalized.includes('/node_modules/@codemirror/state/')
          ) {
            return 'vendor-cm-core';
          }
          if (normalized.includes('/node_modules/@codemirror/') || normalized.includes('/node_modules/@lezer/')) {
            return 'vendor-cm-extra';
          }
          if (
            normalized.includes('/node_modules/markdown-it/') ||
            normalized.includes('/node_modules/dompurify/') ||
            normalized.includes('/node_modules/entities/') ||
            normalized.includes('/node_modules/linkify-it/') ||
            normalized.includes('/node_modules/mdurl/') ||
            normalized.includes('/node_modules/uc.micro/')
          ) {
            return 'vendor-markdown';
          }
        }
      }
    }
  },
  server: {
    host: '127.0.0.1',
    port: 1420,
    strictPort: true,
    watch: {
      ignored: ['**/src-tauri/**']
    }
  }
});
