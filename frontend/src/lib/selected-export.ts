import { fetchManifest, getPaperDetailCached, getSummaryPayloadCached } from '@/lib/api'
import { lazySaveAs, lazyZip } from '@/lib/lazy'
import { resolveStaticBaseUrl } from '@/lib/static-base'
import type { Manifest, PaperDetail, SearchItem } from '@/types/api'
import type JSZip from 'jszip'

export type SelectedDownloadMode = 'zip' | 'jsonl'

export type SelectedDownloadOptions = {
  mode: SelectedDownloadMode
  includeMetadata: boolean
  includePdf: boolean
  includeSourceMarkdown: boolean
  includeTranslatedMarkdown: boolean
  includeImages: boolean
  includeSummaries: boolean
  summaryTemplates: string[]
  includeAllManifestSummaryTemplates?: boolean
}

export type SelectedExportStats = {
  papersTotal: number
  papersProcessed: number
  filesAdded: number
  jsonlRows: number
  missingAssets: number
  failedAssets: number
  missingSummaries: number
  failedSummaries: number
  metadataFailures: number
}

export type SelectedPaperJsonlRecord = {
  paper_id: string
  paper_index?: number | string
  title?: string
  year?: number | string
  venue?: string
  authors?: string[]
  doi?: string | null
  metadata?: Record<string, unknown>
  summaries?: Record<string, unknown>
  missing?: {
    metadata?: boolean
    summaries?: string[]
  }
  errors?: Array<{
    kind: 'metadata' | 'summary'
    template?: string
    message: string
  }>
}

export type SelectedDownloadStats = SelectedExportStats
export type SelectedJsonlRecord = SelectedPaperJsonlRecord

export type SelectedExportResult = {
  stats: SelectedExportStats
  saved: boolean
  filename?: string
}

export type SummaryTemplateDiscovery = {
  templates: string[]
  preferredTemplates: string[]
}

export type SelectedExportCallbacks = {
  onStatus?: (status: string) => void
  onProgress?: (progress: number, stats: SelectedExportStats) => void
  onSizeBytes?: (sizeBytes: number) => void
}

type ManifestAsset = {
  static_path?: string | null
  zip_path?: string | null
  sha256?: string | null
  template_tag?: string
  lang?: string
  status?: string
}

type SelectedExportDeps = {
  getPaperDetailCached: typeof getPaperDetailCached
  getSummaryPayloadCached: typeof getSummaryPayloadCached
  fetchManifest: typeof fetchManifest
  fetchBinary: (url: string) => Promise<ArrayBuffer>
  createZip: () => Promise<JSZip>
  saveAs: (blob: Blob, filename: string) => void
  now: () => number
}

type SelectedPaperJsonlError = NonNullable<SelectedPaperJsonlRecord['errors']>[number]

const defaultDeps: SelectedExportDeps = {
  getPaperDetailCached,
  getSummaryPayloadCached,
  fetchManifest,
  fetchBinary: async (url: string) => {
    const resp = await fetch(url)
    if (!resp.ok) throw new Error(`Failed to fetch ${url}`)
    return await resp.arrayBuffer()
  },
  createZip: async () => {
    const Zip = await lazyZip()
    return new Zip()
  },
  saveAs: asyncSaveAs,
  now: () => Date.now(),
}

async function asyncSaveAs(blob: Blob, filename: string) {
  const saveAs = await lazySaveAs()
  saveAs(blob, filename)
}

function createStats(total: number): SelectedExportStats {
  return {
    papersTotal: total,
    papersProcessed: 0,
    filesAdded: 0,
    jsonlRows: 0,
    missingAssets: 0,
    failedAssets: 0,
    missingSummaries: 0,
    failedSummaries: 0,
    metadataFailures: 0,
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export function selectedExportIssueCount(stats: SelectedExportStats): number {
  return stats.missingAssets + stats.failedAssets + stats.missingSummaries + stats.failedSummaries + stats.metadataFailures
}

export function sanitizeSegment(value: string, maxLength: number): string {
  const cleaned = value
    .trim()
    .replace(/[\\/:*?"<>|]/g, ' ')
    .replace(/[\u0000-\u001f]/g, ' ')
    .replace(/\s+/g, ' ')
  const truncated = cleaned.slice(0, maxLength).trim()
  if (!truncated) return 'unknown'
  return truncated.replace(/\s+/g, '-')
}

export function buildFolderName(item: SearchItem | PaperDetail): string {
  const authorRaw = item.authors?.[0] || 'unknown'
  let author = authorRaw
  if (authorRaw.includes(',')) {
    author = authorRaw.split(',')[0]?.trim() || authorRaw
  } else {
    const parts = authorRaw.trim().split(/\s+/)
    author = parts.length > 1 ? parts[parts.length - 1]! : authorRaw
  }
  const year = item.year ? String(item.year) : 'unknown'
  const title = item.title || 'untitled'
  const hash = item.paper_id || 'unknown'
  const safeHash = hash.replace(/[^a-zA-Z0-9]/g, '').slice(0, 6)
  return [
    sanitizeSegment(author, 32),
    sanitizeSegment(year, 10),
    sanitizeSegment(title, 80),
    sanitizeSegment(safeHash, 6),
  ].join('-')
}

export async function resolvePaperDetail(item: SearchItem, deps: Pick<SelectedExportDeps, 'getPaperDetailCached'> = defaultDeps): Promise<PaperDetail | null> {
  try {
    return await deps.getPaperDetailCached(item.paper_id)
  } catch {
    return null
  }
}

export function fallbackSummaryTemplate(item: SearchItem, detail?: PaperDetail | null): string {
  return item.preferred_summary_template || detail?.preferred_summary_template || 'default'
}

export function resolveSummaryUrls(item: SearchItem, detail?: PaperDetail | null): Record<string, string> {
  const urls: Record<string, string> = {}
  if (detail?.summary_urls) {
    for (const [template, url] of Object.entries(detail.summary_urls)) {
      if (url) urls[template] = url
    }
  }
  const fallbackUrl = item.summary_url || detail?.summary_url
  if (fallbackUrl) {
    const template = fallbackSummaryTemplate(item, detail)
    urls[template] ??= fallbackUrl
  }
  return urls
}

export async function discoverSummaryTemplates(
  items: SearchItem[],
  deps: Pick<SelectedExportDeps, 'getPaperDetailCached'> = defaultDeps,
): Promise<SummaryTemplateDiscovery> {
  const templates = new Set<string>()
  const preferredTemplates = new Set<string>()

  for (const item of items) {
    if (item.preferred_summary_template) {
      templates.add(item.preferred_summary_template)
      preferredTemplates.add(item.preferred_summary_template)
    } else if (item.summary_url) {
      templates.add('default')
      preferredTemplates.add('default')
    }

    const detail = await resolvePaperDetail(item, deps)
    const urls = resolveSummaryUrls(item, detail)
    for (const template of Object.keys(urls)) templates.add(template)
    const preferred = fallbackSummaryTemplate(item, detail)
    if (urls[preferred]) preferredTemplates.add(preferred)
  }

  return {
    templates: [...templates].sort((a, b) => a.localeCompare(b)),
    preferredTemplates: [...preferredTemplates].sort((a, b) => a.localeCompare(b)),
  }
}

function addMissingSummary(record: SelectedPaperJsonlRecord, template: string) {
  record.missing ??= {}
  record.missing.summaries ??= []
  if (!record.missing.summaries.includes(template)) record.missing.summaries.push(template)
}

function addRecordError(record: SelectedPaperJsonlRecord, error: SelectedPaperJsonlError) {
  record.errors ??= []
  record.errors.push(error)
}

function baseJsonlRecord(item: SearchItem, detail?: PaperDetail | null): SelectedPaperJsonlRecord {
  const source = detail ?? item
  return {
    paper_id: source.paper_id,
    paper_index: item.paper_index,
    title: source.title || item.title,
    year: source.year ?? item.year,
    venue: source.venue ?? item.venue,
    authors: source.authors ?? item.authors,
    doi: detail?.doi ?? null,
  }
}

function toPlainRecord(value: unknown): Record<string, unknown> {
  return JSON.parse(JSON.stringify(value)) as Record<string, unknown>
}

export async function buildJsonlRecord(
  item: SearchItem,
  options: Pick<SelectedDownloadOptions, 'includeMetadata' | 'includeSummaries' | 'summaryTemplates'>,
  deps: Pick<SelectedExportDeps, 'getPaperDetailCached' | 'getSummaryPayloadCached'> = defaultDeps,
): Promise<{ record: SelectedPaperJsonlRecord; metadataFailed: boolean; missingSummaries: number; failedSummaries: number }> {
  let detail: PaperDetail | null = null
  let metadataFailed = false
  try {
    detail = await deps.getPaperDetailCached(item.paper_id)
  } catch (error) {
    if (options.includeMetadata) metadataFailed = true
  }

  const record = baseJsonlRecord(item, detail)
  if (options.includeMetadata) {
    if (detail) {
      record.metadata = toPlainRecord(detail)
    } else {
      record.missing = { ...(record.missing ?? {}), metadata: true }
      addRecordError(record, { kind: 'metadata', message: 'Paper detail unavailable' })
    }
  }

  const summaryUrls = resolveSummaryUrls(item, detail)
  let missingSummaries = 0
  let failedSummaries = 0
  const templates = options.includeSummaries === false ? [] : (options.summaryTemplates ?? Object.keys(summaryUrls))
  for (const template of templates) {
    const url = summaryUrls[template]
    if (!url) {
      addMissingSummary(record, template)
      missingSummaries += 1
      continue
    }
    try {
      const payload = await deps.getSummaryPayloadCached(item.paper_id, template, url)
      record.summaries ??= {}
      record.summaries[template] = payload
    } catch (error) {
      failedSummaries += 1
      addRecordError(record, { kind: 'summary', template, message: errorMessage(error) })
    }
  }

  return { record, metadataFailed, missingSummaries, failedSummaries }
}

function progress(stats: SelectedExportStats): number {
  if (stats.papersTotal === 0) return 100
  return Math.round((stats.papersProcessed / stats.papersTotal) * 100)
}

function notifyProgress(callbacks: SelectedExportCallbacks | undefined, stats: SelectedExportStats) {
  callbacks?.onProgress?.(progress(stats), { ...stats })
}

export async function downloadSelectedJsonl(
  items: SearchItem[],
  options: SelectedDownloadOptions,
  callbacks: SelectedExportCallbacks = {},
  deps: Partial<SelectedExportDeps> = {},
): Promise<SelectedExportResult> {
  const merged = { ...defaultDeps, ...deps }
  const stats = createStats(items.length)
  const rows: string[] = []

  for (const item of items) {
    callbacks.onStatus?.(`Building JSONL for ${item.paper_id}`)
    const result = await buildJsonlRecord(item, options, merged)
    stats.metadataFailures += result.metadataFailed ? 1 : 0
    stats.missingSummaries += result.missingSummaries
    stats.failedSummaries += result.failedSummaries
    rows.push(JSON.stringify(result.record))
    stats.jsonlRows += 1
    stats.papersProcessed += 1
    notifyProgress(callbacks, stats)
  }

  if (rows.length === 0) return { stats, saved: false }
  const blob = new Blob([`${rows.join('\n')}\n`], { type: 'application/x-ndjson;charset=utf-8' })
  const filename = `paperdb_selected_${merged.now()}.jsonl`
  await merged.saveAs(blob, filename)
  return { stats, saved: true, filename }
}

function safeRelativePath(path?: string | null): string | null {
  if (!path) return null
  const normalized = path.replace(/\\/g, '/').trim()
  if (!normalized || normalized.startsWith('/') || /^https?:\/\//i.test(normalized) || /^[a-z]:\//i.test(normalized)) {
    return null
  }
  if (/[\u0000-\u001f\u007f]/.test(normalized)) return null
  const parts = normalized.split('/').filter((part) => part.length > 0)
  if (parts.length === 0) return null
  const decodedParts: string[] = []
  for (const part of parts) {
    let decoded = part
    try {
      decoded = decodeURIComponent(part)
    } catch {
      return null
    }
    if (decoded === '.' || decoded === '..' || decoded.includes('/') || decoded.includes('\\')) return null
    if (/[\u0000-\u001f\u007f]/.test(decoded)) return null
    decodedParts.push(part)
  }
  return decodedParts.join('/')
}

function joinUrl(base: string, path: string): string {
  return `${base.replace(/\/$/, '')}/${path}`
}

function usableAssetData(data: unknown): data is ArrayBuffer {
  return data instanceof ArrayBuffer && data.byteLength > 0
}

function bytesToHex(buffer: ArrayBuffer): string {
  return [...new Uint8Array(buffer)].map((byte) => byte.toString(16).padStart(2, '0')).join('')
}

async function sha256Hex(buffer: ArrayBuffer): Promise<string | null> {
  const subtle = globalThis.crypto?.subtle
  if (!subtle) return null
  const digest = await subtle.digest('SHA-256', buffer)
  return bytesToHex(digest)
}

async function matchesManifestHash(asset: ManifestAsset, data: ArrayBuffer): Promise<boolean> {
  const expected = asset.sha256?.trim().toLowerCase()
  if (!expected) return true
  if (!/^[a-f0-9]{64}$/.test(expected)) return false
  const actual = await sha256Hex(data)
  return actual === expected
}

async function addManifestAsset(
  folder: JSZip,
  base: string,
  asset: ManifestAsset | undefined,
  stats: SelectedExportStats,
  deps: SelectedExportDeps,
  callbacks: SelectedExportCallbacks,
  addedPaths: Set<string>,
  overrideZipPath?: string,
  kind: 'asset' | 'summary' = 'asset',
): Promise<number> {
  if (!asset?.static_path) {
    if (kind === 'summary') stats.missingSummaries += 1
    else stats.missingAssets += 1
    return 0
  }
  if (asset.status && asset.status !== 'available') {
    if (kind === 'summary') stats.missingSummaries += 1
    else stats.missingAssets += 1
    return 0
  }
  const zipPath = safeRelativePath(overrideZipPath || asset.zip_path)
  const staticPath = safeRelativePath(asset.static_path)
  if (!zipPath || !staticPath) {
    if (kind === 'summary') stats.missingSummaries += 1
    else stats.missingAssets += 1
    return 0
  }
  if (addedPaths.has(zipPath)) return 0
  try {
    const data = await deps.fetchBinary(joinUrl(base, staticPath))
    if (!usableAssetData(data)) {
      if (kind === 'summary') stats.failedSummaries += 1
      else stats.failedAssets += 1
      return 0
    }
    if (!(await matchesManifestHash(asset, data))) {
      if (kind === 'summary') stats.failedSummaries += 1
      else stats.failedAssets += 1
      return 0
    }
    folder.file(zipPath, data)
    addedPaths.add(zipPath)
    stats.filesAdded += 1
    callbacks.onSizeBytes?.(data.byteLength)
    return 1
  } catch {
    if (kind === 'summary') stats.failedSummaries += 1
    else stats.failedAssets += 1
    return 0
  }
}

function selectedTemplateSet(options: SelectedDownloadOptions): Set<string> {
  return new Set(options.summaryTemplates.filter(Boolean))
}

function shouldIncludeDefaultSummary(
  item: SearchItem,
  detail: PaperDetail | null,
  templates: Set<string>,
  asset?: ManifestAsset,
): boolean {
  if (!asset) return false
  if (templates.size === 0) return false
  return templates.has(fallbackSummaryTemplate(item, detail)) || templates.has(asset.template_tag || '') || templates.has('default')
}

async function resolveManifest(
  item: SearchItem,
  detail: PaperDetail | null,
  deps: SelectedExportDeps,
): Promise<{ manifest: Manifest | null; manifestUrl: string | null }> {
  const manifestUrl = detail?.manifest_url || item.manifest_url || null
  if (!manifestUrl) return { manifest: null, manifestUrl: null }
  try {
    const manifest = await deps.fetchManifest(manifestUrl)
    if (manifest.paper_id && manifest.paper_id !== item.paper_id) return { manifest: null, manifestUrl }
    return { manifest, manifestUrl }
  } catch {
    return { manifest: null, manifestUrl }
  }
}

export async function downloadSelectedZip(
  items: SearchItem[],
  options: SelectedDownloadOptions,
  callbacks: SelectedExportCallbacks = {},
  deps: Partial<SelectedExportDeps> = {},
): Promise<SelectedExportResult> {
  const merged = { ...defaultDeps, ...deps }
  const stats = createStats(items.length)
  const zip = await merged.createZip()
  let singleZipName = ''

  for (const item of items) {
    callbacks.onStatus?.(`Fetching manifest for ${item.paper_id}`)
    let detail: PaperDetail | null = null
    let detailFailed = false
    try {
      detail = await merged.getPaperDetailCached(item.paper_id)
    } catch {
      detailFailed = true
    }

    const { manifest, manifestUrl } = await resolveManifest(item, detail, merged)
    const folderSource = manifest ? item : (detail ?? item)
    const folderName = buildFolderName(folderSource) || manifest?.folder_name_short || manifest?.folder_name || item.paper_id
    if (items.length === 1) singleZipName = folderName
    const wantsSummaries = options.includeSummaries !== false && (options.summaryTemplates.length > 0 || options.includeAllManifestSummaryTemplates)
    const wantsManifestAssets = options.includePdf || options.includeSourceMarkdown || options.includeTranslatedMarkdown || options.includeImages || wantsSummaries
    if (!manifest && wantsManifestAssets) stats.missingAssets += 1

    const folder = zip.folder(folderName)
    if (!folder) {
      stats.failedAssets += 1
      stats.papersProcessed += 1
      notifyProgress(callbacks, stats)
      continue
    }
    const addedPaths = new Set<string>()

    if (options.includeMetadata) {
      if (detail) {
        folder.file('metadata.json', JSON.stringify(toPlainRecord(detail), null, 2))
        stats.filesAdded += 1
      } else if (detailFailed) {
        stats.metadataFailures += 1
      }
    }

    if (manifest) {
      const base = resolveStaticBaseUrl(manifestUrl, detail?.manifest_url, item.manifest_url, detail?.pdf_url, item.pdf_url)
      if (options.includePdf) await addManifestAsset(folder, base, manifest.assets?.pdf, stats, merged, callbacks, addedPaths)
      if (options.includeSourceMarkdown) await addManifestAsset(folder, base, manifest.assets?.source_md, stats, merged, callbacks, addedPaths)

      if (wantsSummaries || options.includeAllManifestSummaryTemplates) {
        const templates = selectedTemplateSet(options)
        const matchedTemplates = new Set<string>()
        const preferredTemplate = fallbackSummaryTemplate(item, detail)
        if (
          options.includeAllManifestSummaryTemplates ||
          shouldIncludeDefaultSummary(item, detail, templates, manifest.assets?.summary)
        ) {
          if (templates.has(preferredTemplate)) matchedTemplates.add(preferredTemplate)
          if (templates.has('default')) matchedTemplates.add('default')
          if (manifest.assets?.summary?.template_tag && templates.has(manifest.assets.summary.template_tag)) {
            matchedTemplates.add(manifest.assets.summary.template_tag)
          }
          await addManifestAsset(folder, base, manifest.assets?.summary, stats, merged, callbacks, addedPaths, undefined, 'summary')
        }
        const summaryTemplates = manifest.assets?.summary_templates ?? []
        for (const asset of summaryTemplates) {
          if (options.includeAllManifestSummaryTemplates || templates.has(asset.template_tag)) {
            if (templates.has(asset.template_tag)) matchedTemplates.add(asset.template_tag)
            await addManifestAsset(folder, base, asset, stats, merged, callbacks, addedPaths, undefined, 'summary')
          }
        }
        if (!options.includeAllManifestSummaryTemplates) {
          for (const template of templates) {
            if (!matchedTemplates.has(template)) stats.missingSummaries += 1
          }
        }
      }

      if (options.includeTranslatedMarkdown) {
        for (const asset of manifest.assets?.translated_md ?? []) {
          const lang = asset.lang ? String(asset.lang) : 'translated'
          await addManifestAsset(folder, base, asset, stats, merged, callbacks, addedPaths, `translated-${sanitizeSegment(lang, 16)}.md`)
        }
      }
      if (options.includeImages) {
        for (const image of manifest.images ?? []) {
          await addManifestAsset(folder, base, image, stats, merged, callbacks, addedPaths)
        }
      }
    } else if (wantsSummaries) {
      stats.missingSummaries += options.summaryTemplates.length
    }

    stats.papersProcessed += 1
    notifyProgress(callbacks, stats)
  }

  if (stats.filesAdded === 0) return { stats, saved: false }
  callbacks.onStatus?.('Compressing ZIP...')
  const blob = await zip.generateAsync({ type: 'blob' })
  const filename = items.length === 1 && singleZipName ? `${singleZipName}.zip` : `paperdb_selected_${merged.now()}.zip`
  await merged.saveAs(blob, filename)
  return { stats, saved: true, filename }
}
