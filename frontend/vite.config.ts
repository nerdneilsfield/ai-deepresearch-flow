import { fileURLToPath, URL } from 'node:url'
import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const packageJson = JSON.parse(readFileSync('./package.json', 'utf-8'))

function modernFontCss() {
  return {
    name: 'modern-font-css',
    enforce: 'pre' as const,
    transform(code: string, id: string) {
      const pathname = id.split('?', 1)[0]
      const isFontProviderCss =
        pathname.includes('/node_modules/@fontsource/') ||
        pathname.includes('/node_modules/katex/dist/katex.min.css')

      if (!isFontProviderCss || !pathname.endsWith('.css')) return null

      // Vite's browser baseline supports WOFF2. Omit legacy fallbacks so
      // deploy artifacts do not carry duplicate copies of every webfont.
      const optimized = code
        .replace(/,\s*url\([^)]*\.woff\)\s*format\((?:'|\")woff(?:'|\")\)/g, '')
        .replace(/,\s*url\([^)]*\.ttf\)\s*format\((?:'|\")truetype(?:'|\")\)/g, '')

      return optimized === code ? null : { code: optimized, map: null }
    },
  }
}

export default defineConfig({
  plugins: [modernFontCss(), vue()],
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
    // The largest chunks are lazy-loaded renderer/PDF vendor bundles. Their raw
    // size is expected and not useful as a per-build warning signal.
    chunkSizeWarningLimit: 8_000,
    rollupOptions: {
      onwarn(warning, warn) {
        if (
          warning.code === 'INVALID_ANNOTATION' &&
          typeof warning.id === 'string' &&
          warning.id.includes('/node_modules/@vueuse/core/')
        ) {
          return
        }
        warn(warning)
      },
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
          if (id.includes('mermaid')) return 'mermaid'
          if (id.includes('markmap')) return 'markmap'
          return undefined
        },
      },
    },
  },
})
