import { fileURLToPath, URL } from 'node:url'
import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const packageJson = JSON.parse(readFileSync('./package.json', 'utf-8'))

export default defineConfig({
  plugins: [vue()],
  define: {
    'import.meta.env.VITE_APP_VERSION': JSON.stringify(packageJson.version),
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  // @ts-ignore
  test: {
    environment: 'jsdom',
    globals: true,
    fileParallelism: false,
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('echarts') || id.includes('vue-echarts')) return 'charts'
          if (id.includes('pdfjs-dist') || id.includes('@tuttarealstep/vue-pdf.js')) return 'pdf'
          if (
            id.includes('md-editor-v3') ||
            id.includes('markdown-it') ||
            id.includes('katex') ||
            id.includes('dompurify')
          ) return 'markdown'
          if (id.includes('markmap') || id.includes('mermaid')) return 'diagrams'
          return undefined
        },
      },
    },
  },
})
