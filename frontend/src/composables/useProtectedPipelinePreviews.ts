import { onBeforeUnmount, ref, type Ref } from 'vue'
import { fetchAdminArtifact } from '@/lib/admin-pipeline'

export type ProtectedPipelinePreviews = {
  pdfUrl: Ref<string | null>
  sourceMarkdown: Ref<string>
  summaryJson: Ref<string>
  translatedMarkdown: Ref<string>
  loading: Ref<boolean>
  error: Ref<string>
  load: (jobId: string, token: string) => Promise<void>
  dispose: () => void
}

export function useProtectedPipelinePreviews(): ProtectedPipelinePreviews {
  const pdfUrl = ref<string | null>(null)
  const sourceMarkdown = ref('')
  const summaryJson = ref('')
  const translatedMarkdown = ref('')
  const loading = ref(false)
  const error = ref('')
  let disposed = false

  function revokePdf(): void {
    if (pdfUrl.value) {
      URL.revokeObjectURL(pdfUrl.value)
      pdfUrl.value = null
    }
  }

  function dispose(): void {
    disposed = true
    revokePdf()
  }

  async function load(jobId: string, token: string): Promise<void> {
    disposed = false
    loading.value = true
    error.value = ''
    revokePdf()
    sourceMarkdown.value = ''
    summaryJson.value = ''
    translatedMarkdown.value = ''
    try {
      const [pdf, source, summary, translated] = await Promise.all([
        fetchAdminArtifact(token, jobId, 'pdf'),
        fetchAdminArtifact(token, jobId, 'source_markdown'),
        fetchAdminArtifact(token, jobId, 'summary_json'),
        fetchAdminArtifact(token, jobId, 'translated_markdown'),
      ])
      const nextPdfUrl = URL.createObjectURL(await pdf.blob())
      const [nextSource, nextSummary, nextTranslated] = await Promise.all([
        source.text(),
        summary.text(),
        translated.text(),
      ])
      if (disposed) {
        URL.revokeObjectURL(nextPdfUrl)
        return
      }
      pdfUrl.value = nextPdfUrl
      sourceMarkdown.value = nextSource
      summaryJson.value = nextSummary
      translatedMarkdown.value = nextTranslated
    } catch (cause) {
      if (!disposed) {
        error.value = cause instanceof Error ? cause.message : 'Preview could not be loaded.'
      }
    } finally {
      if (!disposed) loading.value = false
    }
  }

  onBeforeUnmount(dispose)

  return {
    pdfUrl,
    sourceMarkdown,
    summaryJson,
    translatedMarkdown,
    loading,
    error,
    load,
    dispose,
  }
}
