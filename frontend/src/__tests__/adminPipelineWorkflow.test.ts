import { reactive, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  notifyPipelineTransition,
  requestPipelineNotifications,
} from '@/lib/admin-pipeline'

const routerPush = vi.fn()
const routerReplace = vi.fn()
const routeState = reactive<{ params: Record<string, string> }>({ params: {} })

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPush, replace: routerReplace }),
  useRoute: () => routeState,
}))

const originalFetch = globalThis.fetch
const originalCreateObjectURL = URL.createObjectURL
const originalRevokeObjectURL = URL.revokeObjectURL

function responseJson(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
}

function responseArtifact(value: string, contentType = 'text/plain'): Response {
  return new Response(value, { status: 200, headers: { 'Content-Type': contentType } })
}

function configPayload(worker: 'online' | 'offline' | 'degraded' = 'online') {
  return {
    enabled: true,
    models: {
      ocr: { allowlist: ['ocr-a', 'ocr-b'], default: 'ocr-a' },
      extract: { allowlist: ['extract-a'], default: 'extract-a' },
      translate: { allowlist: ['translate-a'], default: 'translate-a' },
    },
    limits: { pdfs_per_batch: 20, max_pdf_bytes: 100, max_batch_bytes: 500, bibtex_max_bytes: 50 },
    worker: { status: worker, active_jobs: 0 },
  }
}

function jobPayload(overrides: Record<string, unknown> = {}) {
  return {
    id: 'job-1',
    batch_id: 'batch-1',
    status: 'review_ready',
    revision: 3,
    filename: 'paper.pdf',
    size: 32,
    selected_models: { ocr: 'ocr-a', extract: 'extract-a', translate: 'translate-a' },
    progress: { completed_steps: 10, total_steps: 10 },
    failed_step: null,
    error: null,
    bibtex: { status: 'not_provided', entry_key: null, candidates: [], diagnostics: {} },
    artifacts: [],
    ...overrides,
  }
}

function batchPayload(jobs: unknown[], id = 'batch-1') {
  return {
    id,
    revision: 1,
    job_count: jobs.length,
    status_counts: {},
    jobs,
  }
}

function installResponses(responses: Response[]): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(responses.shift() ?? responseJson({})))
  globalThis.fetch = fetchMock as unknown as typeof fetch
  return fetchMock
}

function previewResponses(): Response[] {
  return [
    responseArtifact('%PDF-1.7 preview', 'application/pdf'),
    responseArtifact('# source'),
    responseArtifact('{"summary":"ok"}', 'application/json'),
    responseArtifact('# translation'),
  ]
}

describe('admin pipeline batch and review workflow', () => {
  beforeEach(() => {
    sessionStorage.clear()
    sessionStorage.setItem('paper-db-admin-pipeline-token', 'session-secret')
    setActivePinia(createPinia())
    routeState.params = {}
    routerPush.mockReset()
    routerReplace.mockReset()
    URL.createObjectURL = vi.fn().mockReturnValue('blob:job-preview')
    URL.revokeObjectURL = vi.fn()
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    URL.createObjectURL = originalCreateObjectURL
    URL.revokeObjectURL = originalRevokeObjectURL
    vi.useRealTimers()
  })

  it('shows offline worker state and stops polling after jobs become terminal', async () => {
    vi.useFakeTimers()
    routeState.params = { batchId: 'batch-1' }
    const queued = jobPayload({ status: 'queued', progress: { completed_steps: 0, total_steps: 10 } })
    const ready = jobPayload({ status: 'review_ready' })
    const fetchMock = installResponses([
      responseJson(configPayload('offline')),
      responseJson({ batch: batchPayload([queued]) }),
      responseJson(configPayload('offline')),
    ])
    const { default: AdminPipelineBatchView } = await import('@/views/AdminPipelineBatchView.vue')
    const wrapper = mount(AdminPipelineBatchView)
    await flushPromises()
    expect(wrapper.find('[data-testid="worker-offline"]').exists()).toBe(true)

    fetchMock.mockImplementationOnce(() => Promise.resolve(responseJson({ batch: batchPayload([ready]) })))
    fetchMock.mockImplementationOnce(() => Promise.resolve(responseJson(configPayload('online'))))
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()
    expect(wrapper.text()).toContain('review ready')
    const callsAfterTerminal = fetchMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(6000)
    await flushPromises()
    expect(fetchMock.mock.calls.length).toBe(callsAfterTerminal)
    wrapper.unmount()
  })

  it('notifies once when a job first reaches review_ready after permission', async () => {
    vi.useFakeTimers()
    routeState.params = { batchId: 'batch-1' }
    const queued = jobPayload({ status: 'queued', progress: { completed_steps: 9, total_steps: 10 } })
    const ready = jobPayload({ status: 'review_ready' })
    class TestNotification {
      static permission: NotificationPermission = 'default'
      static calls = 0
      static requestPermission = vi.fn().mockImplementation(() => {
        TestNotification.permission = 'granted'
        return Promise.resolve('granted')
      })

      constructor() {
        TestNotification.calls += 1
      }
    }
    Object.defineProperty(window, 'Notification', { configurable: true, value: TestNotification })
    const fetchMock = installResponses([
      responseJson(configPayload()),
      responseJson({ batch: batchPayload([queued]) }),
      responseJson(configPayload()),
    ])
    const { default: AdminPipelineBatchView } = await import('@/views/AdminPipelineBatchView.vue')
    const wrapper = mount(AdminPipelineBatchView)
    await flushPromises()
    await wrapper.get('[data-testid="enable-notifications"]').trigger('click')
    await flushPromises()
    fetchMock.mockImplementationOnce(() => Promise.resolve(responseJson({ batch: batchPayload([ready]) })))
    fetchMock.mockImplementationOnce(() => Promise.resolve(responseJson(configPayload())))
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()
    expect(TestNotification.permission).toBe('granted')
    expect(TestNotification.calls).toBe(1)
    const callsAfterTransition = fetchMock.mock.calls.length
    fetchMock.mockImplementationOnce(() => Promise.resolve(responseJson({ batch: batchPayload([ready]) })))
    fetchMock.mockImplementationOnce(() => Promise.resolve(responseJson(configPayload())))
    const refreshButton = wrapper.findAll('button').find((button) => button.text() === 'Refresh')
    expect(refreshButton).toBeDefined()
    await refreshButton?.trigger('click')
    await flushPromises()
    expect(fetchMock.mock.calls.length).toBeGreaterThan(callsAfterTransition)
    expect(TestNotification.calls).toBe(1)
    wrapper.unmount()
  })

  it('submits displayed revisions and renders partial batch publish outcomes', async () => {
    routeState.params = { batchId: 'batch-1' }
    const ready = jobPayload({ revision: 7 })
    const failed = jobPayload({ id: 'job-2', status: 'failed', revision: 4, filename: 'failed.pdf' })
    const fetchMock = installResponses([
      responseJson(configPayload()),
      responseJson({ batch: batchPayload([ready, failed]) }),
      responseJson(configPayload()),
      responseJson({
        batch_id: 'batch-1',
        outcomes: [
          { job_id: 'job-1', status: 'queued', result: { status: 'publish_queued' } },
          { job_id: 'job-2', status: 'conflict', error: { code: 'conflict', message: 'not ready' } },
        ],
      }),
      responseJson({ batch: batchPayload([jobPayload({ status: 'publish_queued', revision: 8 }), failed]) }),
      responseJson(configPayload()),
    ])
    const { default: AdminPipelineBatchView } = await import('@/views/AdminPipelineBatchView.vue')
    const wrapper = mount(AdminPipelineBatchView)
    await flushPromises()
    await wrapper.get('[data-testid="batch-publish-ready"]').trigger('click')
    await flushPromises()

    const publishCall = fetchMock.mock.calls.find(([url]) => String(url).includes('/publish-ready'))
    expect(publishCall).toBeTruthy()
    expect(JSON.parse(String((publishCall?.[1] as RequestInit).body))).toEqual({
      items: [{ job_id: 'job-1', expected_revision: 7 }],
    })
    expect(wrapper.find('[data-testid="batch-outcomes"]').text()).toContain('conflict')
    wrapper.unmount()
  })

  it('reloads and resets state when reused route changes to another batch', async () => {
    routeState.params = { batchId: 'batch-1' }
    const first = jobPayload({ filename: 'first.pdf' })
    const second = jobPayload({ id: 'job-2', batch_id: 'batch-2', filename: 'second.pdf', status: 'queued', progress: { completed_steps: 0, total_steps: 10 } })
    const fetchMock = installResponses([
      responseJson(configPayload()),
      responseJson({ batch: batchPayload([first], 'batch-1') }),
      responseJson(configPayload()),
      responseJson({ batch: batchPayload([second], 'batch-2') }),
      responseJson(configPayload()),
    ])
    const { default: AdminPipelineBatchView } = await import('@/views/AdminPipelineBatchView.vue')
    const wrapper = mount(AdminPipelineBatchView)
    await flushPromises()
    expect(wrapper.text()).toContain('first.pdf')
    routeState.params = { batchId: 'batch-2' }
    await flushPromises()
    await nextTick()
    expect(wrapper.text()).toContain('second.pdf')
    expect(wrapper.text()).not.toContain('first.pdf')
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/batches/batch-2'))).toBe(true)
    wrapper.unmount()
  })

  it('stops polling and redirects to login when heartbeat config loses auth', async () => {
    vi.useFakeTimers()
    routeState.params = { batchId: 'batch-1' }
    const queued = jobPayload({ status: 'queued', progress: { completed_steps: 0, total_steps: 10 } })
    const fetchMock = installResponses([
      responseJson(configPayload()),
      responseJson({ batch: batchPayload([queued]) }),
      responseJson(configPayload()),
    ])
    const { default: AdminPipelineBatchView } = await import('@/views/AdminPipelineBatchView.vue')
    const wrapper = mount(AdminPipelineBatchView)
    await flushPromises()
    fetchMock.mockImplementationOnce(() => Promise.resolve(responseJson({ batch: batchPayload([queued]) })))
    fetchMock.mockImplementationOnce(() => Promise.resolve(responseJson({ error: { code: 'unauthorized', message: 'authentication required' } }, 401)))
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()
    expect(routerReplace).toHaveBeenCalledWith('/admin/pipeline')
    const callsAfterLogout = fetchMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(6000)
    await flushPromises()
    expect(fetchMock.mock.calls.length).toBe(callsAfterLogout)
    wrapper.unmount()
  })

  it('allows candidate correction and explicit no-BibTeX binding', async () => {
    routeState.params = { jobId: 'job-1' }
    const needsAttention = jobPayload({
      status: 'needs_attention',
      bibtex: {
        status: 'needs_attention',
        entry_key: null,
        candidates: [{ key: 'smith2024', title: 'Candidate paper' }],
        diagnostics: { reason: 'ambiguous', candidate_keys: ['smith2024'] },
      },
    })
    const bound = jobPayload({ status: 'review_ready', revision: 4, bibtex: { ...needsAttention.bibtex, status: 'matched', entry_key: 'smith2024' } })
    const fetchMock = installResponses([
      responseJson(configPayload()),
      responseJson({ job: needsAttention, worker: configPayload().worker }),
      ...previewResponses(),
      responseJson({ job: bound, binding: { entry_key: 'smith2024' } }),
      ...previewResponses(),
      responseJson({ job: jobPayload({ status: 'review_ready', revision: 5, bibtex: { ...bound.bibtex, status: 'not_provided', entry_key: null } }), binding: { entry_key: null } }),
      ...previewResponses(),
    ])
    const { default: AdminPipelineJobView } = await import('@/views/AdminPipelineJobView.vue')
    const wrapper = mount(AdminPipelineJobView)
    await flushPromises()
    await wrapper.get('[data-testid="bibtex-match"]').setValue('smith2024')
    await wrapper.get('[data-testid="bibtex-bind"]').trigger('click')
    await flushPromises()
    const bindingCall = fetchMock.mock.calls.find(([url]) => String(url).includes('/bibtex-match'))
    expect(JSON.parse(String((bindingCall?.[1] as RequestInit).body))).toEqual({ entry_key: 'smith2024' })
    expect(wrapper.text()).toContain('Read-only previews')
    await wrapper.get('[data-testid="bibtex-match"]').setValue('__none__')
    await wrapper.get('[data-testid="bibtex-bind"]').trigger('click')
    await flushPromises()
    const bindingCalls = fetchMock.mock.calls.filter(([url]) => String(url).includes('/bibtex-match'))
    expect(JSON.parse(String((bindingCalls[1]?.[1] as RequestInit).body))).toEqual({ no_bibtex: true })
    wrapper.unmount()
  })

  it('publishes with current revision and exposes stale 409 response', async () => {
    routeState.params = { jobId: 'job-1' }
    const current = jobPayload({ revision: 9 })
    const stale = jobPayload({ revision: 10, status: 'published', filename: 'updated.pdf' })
    const fetchMock = installResponses([
      responseJson(configPayload()),
      responseJson({ job: current, worker: configPayload().worker }),
      ...previewResponses(),
      responseJson({ error: { code: 'conflict', message: 'publication revision is stale' } }, 409),
      responseJson({ job: stale, worker: configPayload().worker }),
      ...previewResponses(),
    ])
    const { default: AdminPipelineJobView } = await import('@/views/AdminPipelineJobView.vue')
    const wrapper = mount(AdminPipelineJobView)
    await flushPromises()
    await wrapper.get('[data-testid="job-publish"]').trigger('click')
    await flushPromises()
    const publishCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/publish'))
    expect(JSON.parse(String((publishCall?.[1] as RequestInit).body))).toEqual({ expected_revision: 9 })
    expect(wrapper.find('[data-testid="stale-revision"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('Stale revision')
    expect(wrapper.text()).toContain('updated.pdf')
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/artifacts/')).length).toBe(8)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:job-preview')
    wrapper.unmount()
  })

  it('refreshes indexing retry conflicts before enabling actions again', async () => {
    routeState.params = { jobId: 'job-1' }
    const current = jobPayload({ revision: 12, status: 'published_with_warning' })
    const fresh = jobPayload({ revision: 13, status: 'published_with_warning', filename: 'fresh-index.pdf' })
    const fetchMock = installResponses([
      responseJson(configPayload()),
      responseJson({ job: current, worker: configPayload().worker }),
      ...previewResponses(),
      responseJson({ error: { code: 'conflict', message: 'indexing revision is stale' } }, 409),
      responseJson({ job: fresh, worker: configPayload().worker }),
      ...previewResponses(),
    ])
    const { default: AdminPipelineJobView } = await import('@/views/AdminPipelineJobView.vue')
    const wrapper = mount(AdminPipelineJobView)
    await flushPromises()
    await wrapper.get('[data-testid="job-retry"]').trigger('click')

    expect(wrapper.get('[data-testid="job-retry"]').attributes('disabled')).toBeDefined()
    await flushPromises()
    expect(wrapper.text()).toContain('fresh-index.pdf')
    expect(wrapper.find('[data-testid="stale-revision"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="job-retry"]').attributes('disabled')).toBeUndefined()
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/artifacts/')).length).toBe(8)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:job-preview')
    wrapper.unmount()
  })

  it('retries failed jobs with selected models and supports rejection', async () => {
    routeState.params = { jobId: 'job-1' }
    const failed = jobPayload({ status: 'failed', failed_step: 'translate', error: 'provider failed' })
    const queued = jobPayload({ status: 'queued', progress: { completed_steps: 2, total_steps: 10 } })
    const rejected = jobPayload({ status: 'rejected' })
    const fetchMock = installResponses([
      responseJson(configPayload()),
      responseJson({ job: failed, worker: configPayload().worker }),
      responseJson({}, 404), responseJson({}, 404), responseJson({}, 404), responseJson({}, 404),
      responseJson({ job: queued, result: { status: 'queued' } }),
      responseJson({ job: rejected }),
    ])
    const { default: AdminPipelineJobView } = await import('@/views/AdminPipelineJobView.vue')
    const wrapper = mount(AdminPipelineJobView)
    await flushPromises()
    await wrapper.get('#retry-ocr').setValue('ocr-b')
    await wrapper.get('[data-testid="job-retry"]').trigger('click')
    await flushPromises()
    const retryCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/retry'))
    expect(JSON.parse(String((retryCall?.[1] as RequestInit).body))).toMatchObject({ models: { ocr: 'ocr-b' } })
    expect(wrapper.text()).toContain('queued')

    await wrapper.get('[data-testid="job-reject"]').trigger('click')
    await flushPromises()
    const rejectCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/reject'))
    expect(rejectCall).toBeTruthy()
    expect(wrapper.text()).toContain('rejected')
    wrapper.unmount()
  })

  it('requests notifications only from explicit permission action', async () => {
    const requestPermission = vi.fn()
    class TestNotification {
      static permission: NotificationPermission = 'default'
      static requestPermission = requestPermission
      readonly title: string
      readonly options?: NotificationOptions

      constructor(title: string, options?: NotificationOptions) {
        this.title = title
        this.options = options
      }
    }
    requestPermission.mockImplementation(() => {
      TestNotification.permission = 'granted'
      return Promise.resolve('granted')
    })
    Object.defineProperty(window, 'Notification', { configurable: true, value: TestNotification })

    expect(await requestPipelineNotifications()).toBe(true)
    expect(requestPermission).toHaveBeenCalledTimes(1)
    expect(notifyPipelineTransition('Pipeline job completed', 'paper.pdf: published')).toBe(true)
  })
})
