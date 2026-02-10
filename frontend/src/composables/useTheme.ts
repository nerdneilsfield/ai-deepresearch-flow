import { ref, watch, onMounted } from 'vue'

export type ThemeMode = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'paperdb_theme'

const themeMode = ref<ThemeMode>((localStorage.getItem(STORAGE_KEY) as ThemeMode) || 'system')

function applyTheme(mode: ThemeMode) {
  const isDark =
    mode === 'dark' || (mode === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.classList.toggle('dark', isDark)
}

// Listen for system preference changes
const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
mediaQuery.addEventListener('change', () => {
  if (themeMode.value === 'system') {
    applyTheme('system')
  }
})

watch(themeMode, (mode) => {
  localStorage.setItem(STORAGE_KEY, mode)
  applyTheme(mode)
})

export function useTheme() {
  onMounted(() => {
    applyTheme(themeMode.value)
  })

  function setTheme(mode: ThemeMode) {
    themeMode.value = mode
  }

  return { themeMode, setTheme }
}
