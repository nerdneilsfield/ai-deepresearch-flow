import { defineComponent, h, onBeforeUnmount, onMounted, ref, shallowRef, type Component } from 'vue'

type AsyncLoader<T extends Component> = () => Promise<T | { default: T }>

function asyncErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message
  if (typeof error === 'string' && error) return error
  return 'Please reload this page or try again later.'
}

export function defineSafeAsyncComponent<T extends Component>(label: string, loader: AsyncLoader<T>): T {
  return defineComponent({
    name: 'SafeAsyncComponent',
    inheritAttrs: false,
    setup(_props, { attrs, slots }) {
      const resolved = shallowRef<T | null>(null)
      const error = ref<unknown>(null)
      const loading = ref(true)
      let mounted = true

      onMounted(async () => {
        try {
          const loaded = await loader()
          if (!mounted) return
          resolved.value = 'default' in Object(loaded) ? (loaded as { default: T }).default : (loaded as T)
        } catch (caught) {
          if (!mounted) return
          error.value = caught
        } finally {
          if (mounted) loading.value = false
        }
      })

      onBeforeUnmount(() => {
        mounted = false
      })

      return () => {
        if (error.value) {
          return h(
            'section',
            {
              role: 'alert',
              class:
                'rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-950 dark:border-red-900 dark:bg-red-950/30 dark:text-red-100',
            },
            [
              h('p', { class: 'font-medium' }, `${label} is unavailable.`),
              h('p', { class: 'mt-2 break-words opacity-80' }, asyncErrorMessage(error.value)),
            ],
          )
        }
        if (resolved.value) return h(resolved.value, attrs, slots)
        if (loading.value) {
          return h('div', { class: 'rounded-lg border border-ink-100 p-4 text-sm text-ink-500' }, `${label} is loading…`)
        }
        return null
      }
    },
  }) as unknown as T
}
