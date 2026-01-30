import { defineStore } from 'pinia'
import { toast } from '@/components/ui/toast'

export const useUiStore = defineStore('ui', {
  state: () => ({
    loading: false,
    error: '',
    detailTitle: '',
    detailSubtitle: '',
  }),
  actions: {
    setLoading(value: boolean) {
      this.loading = value
    },
    setError(message: string) {
      this.error = message
    },
    pushToast(message: string, tone: 'info' | 'error' | 'success' = 'info') {
      toast({
        title: tone === 'error' ? 'Error' : tone === 'success' ? 'Success' : 'Info',
        description: message,
        variant: tone === 'error' ? 'destructive' : 'default',
      })
    },
    setDetailHeader(title: string, subtitle = '') {
      this.detailTitle = title
      this.detailSubtitle = subtitle
    },
  },
})
