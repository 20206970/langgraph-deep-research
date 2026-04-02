import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 3000,
    proxy: {
      '/research': {
        target: 'http://localhost:8000',
        changeOrigin: true
      }
    }
  }
})