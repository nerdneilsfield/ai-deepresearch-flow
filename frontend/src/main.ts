import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'vue-virtual-scroller/dist/vue-virtual-scroller.css'
import './style.css'
import App from './App.vue'
import router from './router'
import i18n from './i18n'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { queryClient } from './lib/query-client'
import { useRuntimeConfigStore } from './stores/runtime-config'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(router)
app.use(i18n)
app.use(VueQueryPlugin, { queryClient })
const runtimeConfig = useRuntimeConfigStore(pinia)
runtimeConfig.load().catch(() => {})
app.mount('#app')

// Performance monitoring in development
if (import.meta.env.DEV) {
  setInterval(() => {
    const nodeCount = document.querySelectorAll('*').length
    const memory = (performance as any).memory?.usedJSHeapSize
    const memoryMB = memory ? (memory / 1024 / 1024).toFixed(1) : 'N/A'
    console.log(
      `%c[Perf]%c Nodes: ${nodeCount}, Memory: ${memoryMB}MB`,
      'color: #3b82f6; font-weight: bold',
      'color: inherit'
    )
    
    // Warn if metrics are high
    if (nodeCount > 10000) {
      console.warn(`%c[Perf]%c High DOM node count: ${nodeCount}`, 'color: #f59e0b', 'color: inherit')
    }
    if (memory && memory > 200 * 1024 * 1024) {
      console.warn(`%c[Perf]%c High memory usage: ${memoryMB}MB`, 'color: #f59e0b', 'color: inherit')
    }
  }, 10000) // Log every 10 seconds
}
