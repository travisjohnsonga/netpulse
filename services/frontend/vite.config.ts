import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8001', ws: true },
    },
  },
  build: {
    rollupOptions: {
      output: {
        // Split the heavy, independently-cacheable vendor libs into their own
        // chunks so a change to app code (or one lib) doesn't invalidate/reparse
        // the others, and the browser caches them across deploys. echarts pulls
        // in zrender; cytoscape and swagger-ui-react are the other large deps.
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('echarts') || id.includes('zrender')) return 'echarts'
          if (id.includes('cytoscape')) return 'cytoscape'
          if (id.includes('swagger-ui')) return 'swagger-ui'
          return 'vendor'
        },
      },
    },
  },
})
