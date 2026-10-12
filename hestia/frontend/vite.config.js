import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// desenv Built wird nach hestia/frontend/dist committed; Container bauen nichts.
export default defineConfig({
  plugins: [svelte()],
  base: '/',
  build: { outDir: 'dist', emptyOutDir: true },
  server: { port: 5175, proxy: { '/api': 'http://localhost:8123' } },
})
