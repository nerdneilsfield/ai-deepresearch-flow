import { API_BASE } from '@/lib/config'
import { buildUrl, fetchResponse } from '@/lib/http'

export const ADMIN_TOKEN_STORAGE_KEY = 'paper-db-admin-pipeline-token'
export const ADMIN_PIPELINE_BASE = '/admin/pipeline'

export type PipelineModelGroup = {
  allowlist: string[]
  default: string
}

export type PipelineWorkerStatus = {
  status: 'online' | 'degraded' | 'offline' | string
  last_heartbeat_at?: string | null
  age_seconds?: number | null
  active_jobs?: number
  diagnostics?: Record<string, unknown>
}

export type PipelineConfig = {
  enabled: boolean
  models: {
    ocr: PipelineModelGroup
    extract: PipelineModelGroup
    translate: PipelineModelGroup
  }
  limits: {
    pdfs_per_batch: number
    max_pdf_bytes: number
    max_batch_bytes: number
    bibtex_max_bytes: number
  }
  translation_language?: string
  worker: PipelineWorkerStatus
}

export type BibtexCandidate = {
  key: string
  type?: string
  title?: string
  author?: string
  doi?: string
  year?: string | number
  [key: string]: unknown
}

export type BibtexInfo = {
  status: 'not_provided' | 'matched' | 'unmatched' | 'needs_attention' | string
  entry_key: string | null
  candidates: BibtexCandidate[]
  diagnostics: {
    reason?: string
    candidate_keys?: string[]
    [key: string]: unknown
  }
}

export type PipelineProgress = {
  completed_steps: number
  total_steps: number
}

export type PipelineStep = {
  name: string
  status: string
  attempt?: number
  model_key?: string | null
  duration_ms?: number | null
}

export type PipelineAttempt = {
  step: string
  attempt?: number
  status: string
  error?: string | null
  error_type?: string | null
  retryable?: boolean | null
  duration_ms?: number | null
  started_at?: string | null
  finished_at?: string | null
}

export type PipelineArtifact = {
  kind: string
  size: number
  digest?: string | null
}

export type PipelineJob = {
  id: string
  batch_id?: string | null
  status: string
  revision: number
  created_at?: string | null
  updated_at?: string | null
  terminal_at?: string | null
  filename?: string | null
  size?: number | null
  selected_models: Record<string, string>
  progress: PipelineProgress
  failed_step?: string | null
  error?: string | null
  error_type?: string | null
  retryable?: boolean | null
  bibtex: BibtexInfo
  preview_digest?: string | null
  bundle_digest?: string | null
  preview_error?: string | null
  cancel_requested?: boolean
  steps?: PipelineStep[]
  attempts?: PipelineAttempt[]
  artifacts?: PipelineArtifact[]
  [key: string]: unknown
}

export type PipelineBatch = {
  id: string
  created_at?: string | null
  revision: number
  job_count: number
  status_counts: Record<string, number>
  jobs: PipelineJob[]
  [key: string]: unknown
}

export type PipelineBatchList = {
  page: number
  page_size: number
  total: number
  has_more: boolean
  items: PipelineBatch[]
}

export type PipelineJobResponse = { job: PipelineJob; worker?: PipelineWorkerStatus }
export type PipelineBatchResponse = { batch: PipelineBatch }

export type PipelineModels = {
  ocr?: string
  extract?: string
  translate?: string
}

export class AdminPipelineError extends Error {
  readonly status: number
  readonly code: string
  readonly payload: unknown

  constructor(status: number, code: string, message: string, payload?: unknown) {
    super(message)
    this.name = 'AdminPipelineError'
    this.status = status
    this.code = code
    this.payload = payload
  }
}

function storage(): Storage | null {
  if (typeof window === 'undefined') return null
  return window.sessionStorage
}

export function getAdminToken(): string | null {
  return storage()?.getItem(ADMIN_TOKEN_STORAGE_KEY) ?? null
}

export function setAdminToken(token: string): void {
  const value = token.trim()
  const target = storage()
  if (!target) return
  if (value) target.setItem(ADMIN_TOKEN_STORAGE_KEY, value)
  else target.removeItem(ADMIN_TOKEN_STORAGE_KEY)
}

export function clearAdminToken(): void {
  storage()?.removeItem(ADMIN_TOKEN_STORAGE_KEY)
}

function adminUrl(path: string, params?: Record<string, string | number | undefined | null>): string {
  return buildUrl(`${ADMIN_PIPELINE_BASE}${path}`, params)
}

function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}` }
}

async function readError(response: Response): Promise<AdminPipelineError> {
  let payload: unknown
  try {
    payload = await response.clone().json()
  } catch {
    payload = undefined
  }
  const error = payload && typeof payload === 'object' && 'error' in payload
    ? (payload as { error?: { code?: unknown; message?: unknown } }).error
    : undefined
  const code = typeof error?.code === 'string' ? error.code : `http_${response.status}`
  const message = typeof error?.message === 'string' ? error.message : response.statusText || 'pipeline request failed'
  return new AdminPipelineError(response.status, code, message, payload)
}

async function requestJson<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  const response = await fetchResponse(adminUrl(path), {
    ...init,
    headers: { ...authHeaders(token), ...(init.headers ?? {}) },
    retry: 0,
    timeoutMs: 120_000,
  })
  if (!response.ok) throw await readError(response)
  return (await response.json()) as T
}

export async function fetchAdminConfig(token: string): Promise<PipelineConfig> {
  return requestJson<PipelineConfig>('/config', token)
}

export type CreatePipelineBatchInput = {
  pdfs: File[]
  bibtex?: File | null
  models?: PipelineModels
}

export async function createPipelineBatch(
  input: CreatePipelineBatchInput,
  token: string,
): Promise<{
  batch: PipelineBatch
  batch_id: string
  job_ids: string[]
  bibtex: { status: string }
}> {
  const body = new FormData()
  for (const pdf of input.pdfs) body.append('pdfs[]', pdf, pdf.name)
  if (input.bibtex) body.append('bibtex', input.bibtex, input.bibtex.name)
  for (const name of ['ocr', 'extract', 'translate'] as const) {
    const model = input.models?.[name]
    if (model) body.append(`${name}_model`, model)
  }
  return requestJson('/batches', token, { method: 'POST', body })
}

export async function listPipelineBatches(
  token: string,
  page = 1,
  pageSize = 20,
): Promise<PipelineBatchList> {
  return requestJson<PipelineBatchList>(
    `/batches?page=${encodeURIComponent(String(page))}&page_size=${encodeURIComponent(String(pageSize))}`,
    token,
  )
}

export async function getPipelineBatch(token: string, batchId: string): Promise<PipelineBatchResponse> {
  return requestJson<PipelineBatchResponse>(`/batches/${encodeURIComponent(batchId)}`, token)
}

export async function getPipelineJob(token: string, jobId: string): Promise<PipelineJobResponse> {
  return requestJson<PipelineJobResponse>(`/jobs/${encodeURIComponent(jobId)}`, token)
}

export async function retryPipelineJob(
  token: string,
  jobId: string,
  models?: PipelineModels,
  expectedRevision?: number,
): Promise<PipelineJobResponse & { result?: unknown }> {
  const body: Record<string, unknown> = {}
  if (models && Object.keys(models).length > 0) body.models = models
  if (expectedRevision !== undefined) body.expected_revision = expectedRevision
  return requestJson(`/jobs/${encodeURIComponent(jobId)}/retry`, token, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function cancelPipelineJob(token: string, jobId: string): Promise<PipelineJobResponse> {
  return requestJson(`/jobs/${encodeURIComponent(jobId)}/cancel`, token, { method: 'POST' })
}

export async function rejectPipelineJob(token: string, jobId: string): Promise<PipelineJobResponse> {
  return requestJson(`/jobs/${encodeURIComponent(jobId)}/reject`, token, { method: 'POST' })
}

export async function publishPipelineJob(
  token: string,
  jobId: string,
  expectedRevision: number,
): Promise<PipelineJobResponse & { result?: unknown }> {
  return requestJson(`/jobs/${encodeURIComponent(jobId)}/publish`, token, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_revision: expectedRevision }),
  })
}

export async function bindPipelineBibtex(
  token: string,
  jobId: string,
  entryKey: string | null,
): Promise<PipelineJobResponse & { binding?: unknown }> {
  const body = entryKey === null ? { no_bibtex: true } : { entry_key: entryKey }
  return requestJson(`/jobs/${encodeURIComponent(jobId)}/bibtex-match`, token, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export type BatchPublishOutcome = {
  job_id?: string
  status: string
  result?: unknown
  error?: { code?: string; message?: string }
  [key: string]: unknown
}

export async function publishReadyPipelineBatch(
  token: string,
  batchId: string,
  items: Array<{ job_id: string; expected_revision: number }>,
): Promise<{ batch_id: string; outcomes: BatchPublishOutcome[] }> {
  return requestJson(`/batches/${encodeURIComponent(batchId)}/publish-ready`, token, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items }),
  })
}

export async function cancelPipelineBatch(
  token: string,
  batchId: string,
): Promise<{ batch_id: string; outcomes: BatchPublishOutcome[] }> {
  return requestJson(`/batches/${encodeURIComponent(batchId)}/cancel`, token, { method: 'POST' })
}

export async function fetchAdminArtifact(
  token: string,
  jobId: string,
  kind: string,
): Promise<Response> {
  const response = await fetchResponse(
    adminUrl(`/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(kind)}`),
    { headers: authHeaders(token), retry: 0, timeoutMs: 120_000 },
  )
  if (!response.ok) throw await readError(response)
  return response
}

export function pipelineApiBase(): string {
  return `${API_BASE}${ADMIN_PIPELINE_BASE}`
}

export async function requestPipelineNotifications(): Promise<boolean> {
  if (typeof window === 'undefined' || !('Notification' in window)) return false
  if (Notification.permission === 'granted') return true
  if (Notification.permission === 'denied') return false
  return (await Notification.requestPermission()) === 'granted'
}

export function notifyPipelineTransition(title: string, body: string): boolean {
  if (typeof window === 'undefined' || !('Notification' in window) || Notification.permission !== 'granted') return false
  try {
    new Notification(title, { body })
    return true
  } catch {
    return false
  }
}
