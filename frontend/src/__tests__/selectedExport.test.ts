import { describe, expect, it, vi } from 'vitest'
import JSZip from 'jszip'

import type { Manifest, PaperDetail, SearchItem } from '@/types/api'
import {
  discoverSummaryTemplates,
  downloadSelectedJsonl,
  downloadSelectedZip,
  resolveSummaryUrls,
  type SelectedDownloadOptions,
} from '@/lib/selected-export'

function makeItem(overrides: Partial<SearchItem> = {}): SearchItem {
  return {
    paper_id: 'paper-1',
    paper_index: 7,
    title: 'A Study',
    year: '2026',
    venue: 'ICML',
    authors: ['Ada Lovelace'],
    preferred_summary_template: 'default',
    summary_url: 'https://cdn.example/summary/paper-1.json',
    manifest_url: 'https://cdn.example/manifest/paper-1.json',
    ...overrides,
  }
}

function makeDetail(overrides: Partial<PaperDetail> = {}): PaperDetail {
  return {
    paper_id: 'paper-1',
    title: 'A Detailed Study',
    year: '2026',
    venue: 'ICML',
    authors: ['Ada Lovelace'],
    keywords: ['agents'],
    institutions: [],
    tags: [],
    doi: '10.0000/example',
    preferred_summary_template: 'default',
    summary_url: 'https://cdn.example/summary/paper-1.json',
    summary_urls: {
      default: 'https://cdn.example/summary/paper-1.json',
      deep_read: 'https://cdn.example/summary/paper-1-deep.json',
    },
    manifest_url: 'https://cdn.example/manifest/paper-1.json',
    ...overrides,
  }
}

function makeManifest(): Manifest {
  return {
    paper_id: 'paper-1',
    folder_name: 'manifest-folder',
    folder_name_short: 'manifest-short',
    assets: {
      pdf: { static_path: 'pdf/paper-1.pdf', zip_path: 'paper.pdf' },
      source_md: { static_path: 'md/paper-1.md', zip_path: 'source.md' },
      summary: { static_path: 'summary/paper-1.json', zip_path: 'summary.json', template_tag: 'default' },
      summary_templates: [
        { static_path: 'summary/paper-1-deep.json', zip_path: 'summaries/deep_read.json', template_tag: 'deep_read' },
      ],
      translated_md: [
        { static_path: 'md_translate/zh/paper-1.md', zip_path: 'translated/zh.md', lang: 'zh' },
      ],
    },
    images: [
      { static_path: 'images/paper-1/fig1.png', zip_path: 'images/fig1.png', status: 'available' },
      { static_path: 'images/paper-1/missing.png', zip_path: 'images/missing.png', status: 'missing' },
    ],
  }
}

async function textFromBlob(blob: Blob): Promise<string> {
  return await blob.text()
}

const jsonlOptions: SelectedDownloadOptions = {
  mode: 'jsonl',
  includeMetadata: true,
  includePdf: false,
  includeSourceMarkdown: false,
  includeTranslatedMarkdown: false,
  includeImages: false,
  includeSummaries: true,
  summaryTemplates: ['default', 'deep_read'],
}

describe('selected export helper', () => {
  it('resolves summary URLs from detail templates and fallback summary URL', () => {
    const urls = resolveSummaryUrls(
      makeItem({ preferred_summary_template: 'search_default', summary_url: 'https://cdn.example/summary/search.json' }),
      makeDetail({
        preferred_summary_template: 'detail_default',
        summary_urls: { deep_read: 'https://cdn.example/summary/deep.json' },
        summary_url: 'https://cdn.example/summary/detail.json',
      }),
    )

    expect(urls).toEqual({
      deep_read: 'https://cdn.example/summary/deep.json',
      search_default: 'https://cdn.example/summary/search.json',
    })
  })

  it('discovers the union of summary templates across selected papers', async () => {
    const discovery = await discoverSummaryTemplates(
      [makeItem({ paper_id: 'paper-a', preferred_summary_template: 'quick' }), makeItem({ paper_id: 'paper-b' })],
      {
        getPaperDetailCached: async (paperId: string) => makeDetail({
          paper_id: paperId,
          summary_urls: paperId === 'paper-a'
            ? { default: 'https://cdn.example/a/default.json' }
            : { deep_read: 'https://cdn.example/b/deep.json' },
        }),
      },
    )

    expect(discovery.templates.sort()).toEqual(['deep_read', 'default', 'quick'])
    expect(discovery.preferredTemplates).toContain('quick')
  })

  it('saves JSONL with one raw-summary row per selected paper and fallback metadata on detail failure', async () => {
    const saved = vi.fn()
    const progress: number[] = []

    const result = await downloadSelectedJsonl(
      [makeItem({ paper_id: 'paper-1' }), makeItem({ paper_id: 'paper-2', summary_url: undefined })],
      jsonlOptions,
      {
        onProgress: (value) => progress.push(value),
      },
      {
        getPaperDetailCached: async (paperId: string) => {
          if (paperId === 'paper-2') throw new Error('detail unavailable')
          return makeDetail({ paper_id: paperId })
        },
        getSummaryPayloadCached: async (_paperId: string, template: string) => ({ template, raw: true }),
        saveAs: saved,
        now: () => 123,
      },
    )

    expect(saved).toHaveBeenCalledTimes(1)
    const [blob, fileName] = saved.mock.calls[0] as [Blob, string]
    expect(fileName).toBe('paperdb_selected_123.jsonl')
    const lines = (await textFromBlob(blob)).trimEnd().split('\n').map((line) => JSON.parse(line))
    expect(lines).toHaveLength(2)
    expect(lines[0]).toMatchObject({
      paper_id: 'paper-1',
      metadata: { paper_id: 'paper-1', title: 'A Detailed Study' },
      summaries: {
        default: { template: 'default', raw: true },
        deep_read: { template: 'deep_read', raw: true },
      },
    })
    expect(lines[1]).toMatchObject({
      paper_id: 'paper-2',
      title: 'A Study',
      missing: { metadata: true, summaries: ['default', 'deep_read'] },
    })
    expect(result.stats).toMatchObject({ papersProcessed: 2, jsonlRows: 2, metadataFailures: 1, missingSummaries: 2 })
    expect(progress).toEqual([50, 100])
  })

  it('creates ZIP entries according to selected content while preserving manifest paths and translated filename compatibility', async () => {
    const saved = vi.fn()
    const result = await downloadSelectedZip(
      [makeItem()],
      {
        mode: 'zip',
        includeMetadata: true,
        includePdf: false,
        includeSourceMarkdown: true,
        includeTranslatedMarkdown: true,
        includeImages: true,
        includeSummaries: true,
        summaryTemplates: ['default', 'deep_read'],
      },
      {},
      {
        createZip: async () => new JSZip(),
        getPaperDetailCached: async () => makeDetail(),
        fetchManifest: async () => makeManifest(),
        fetchBinary: async (url: string) => await new Blob([`payload:${url}`]).arrayBuffer(),
        saveAs: saved,
      },
    )

    expect(saved).toHaveBeenCalledTimes(1)
    const [blob, fileName] = saved.mock.calls[0] as [Blob, string]
    expect(String(fileName).endsWith('.zip')).toBe(true)
    const zip = await JSZip.loadAsync(blob)
    const names = Object.keys(zip.files).filter((name) => !zip.files[name]?.dir).sort()

    expect(names).toEqual([
      'Lovelace-2026-A-Study-paper1/images/fig1.png',
      'Lovelace-2026-A-Study-paper1/metadata.json',
      'Lovelace-2026-A-Study-paper1/source.md',
      'Lovelace-2026-A-Study-paper1/summaries/deep_read.json',
      'Lovelace-2026-A-Study-paper1/summary.json',
      'Lovelace-2026-A-Study-paper1/translated-zh.md',
    ])
    expect(await zip.file('Lovelace-2026-A-Study-paper1/metadata.json')?.async('string'))
      .toContain('A Detailed Study')
    expect(result.stats).toMatchObject({ filesAdded: 6, missingAssets: 1, papersProcessed: 1 })
  })

  it('writes metadata-only ZIP entries when the manifest is missing but detail is available', async () => {
    const saved = vi.fn()

    const result = await downloadSelectedZip(
      [makeItem({ manifest_url: undefined })],
      {
        mode: 'zip',
        includeMetadata: true,
        includePdf: true,
        includeSourceMarkdown: true,
        includeTranslatedMarkdown: true,
        includeImages: true,
        includeSummaries: true,
        summaryTemplates: ['default'],
      },
      {},
      {
        createZip: async () => new JSZip(),
        getPaperDetailCached: async () => makeDetail({ manifest_url: undefined }),
        fetchManifest: async () => { throw new Error('no manifest') },
        fetchBinary: async () => new ArrayBuffer(0),
        saveAs: saved,
      },
    )

    const [blob] = saved.mock.calls[0] as [Blob, string]
    const zip = await JSZip.loadAsync(blob)
    const names = Object.keys(zip.files).filter((name) => !zip.files[name]?.dir)
    expect(names).toEqual(['Lovelace-2026-A-Detailed-Study-paper1/metadata.json'])
    expect(result.stats).toMatchObject({ filesAdded: 1, failedAssets: 0, metadataFailures: 0, papersProcessed: 1 })
  })

  it('skips unsafe manifest paths instead of writing zip-slip entries or fetching external static paths', async () => {
    const saved = vi.fn()
    const fetchBinary = vi.fn(async () => await new Blob(['payload']).arrayBuffer())
    const unsafeManifest = makeManifest()
    unsafeManifest.assets = {
      ...unsafeManifest.assets,
      pdf: { static_path: 'pdf/paper-1.pdf', zip_path: '../evil.pdf' },
      source_md: { static_path: 'https://evil.example/source.md', zip_path: 'source.md' },
      summary: { static_path: '%2e%2e/summary.json', zip_path: 'summary.json', template_tag: 'default' },
      summary_templates: [
        { static_path: 'summary/deep.json', zip_path: '.%2e/deep.json', template_tag: 'deep_read' },
      ],
    }
    unsafeManifest.images = [
      { static_path: 'images/good.png', zip_path: 'images/good.png', status: 'available' },
    ]

    const result = await downloadSelectedZip(
      [makeItem()],
      {
        mode: 'zip',
        includeMetadata: false,
        includePdf: true,
        includeSourceMarkdown: true,
        includeTranslatedMarkdown: false,
        includeImages: true,
        includeSummaries: true,
        summaryTemplates: ['default', 'deep_read'],
      },
      {},
      {
        createZip: async () => new JSZip(),
        getPaperDetailCached: async () => makeDetail(),
        fetchManifest: async () => unsafeManifest,
        fetchBinary,
        saveAs: saved,
      },
    )

    const [blob] = saved.mock.calls[0] as [Blob, string]
    const zip = await JSZip.loadAsync(blob)
    const names = Object.keys(zip.files).filter((name) => !zip.files[name]?.dir)
    expect(names).toEqual(['Lovelace-2026-A-Study-paper1/images/good.png'])
    expect(fetchBinary).toHaveBeenCalledTimes(1)
    expect(result.stats).toMatchObject({ filesAdded: 1, missingAssets: 2, missingSummaries: 2 })
  })


  it('counts manually selected ZIP summary templates missing from a paper manifest', async () => {
    const saved = vi.fn()
    const manifest = makeManifest()
    manifest.assets = {
      ...manifest.assets,
      summary_templates: [],
    }

    const result = await downloadSelectedZip(
      [makeItem()],
      {
        mode: 'zip',
        includeMetadata: false,
        includePdf: false,
        includeSourceMarkdown: false,
        includeTranslatedMarkdown: false,
        includeImages: false,
        includeSummaries: true,
        summaryTemplates: ['default', 'deep_read'],
      },
      {},
      {
        createZip: async () => new JSZip(),
        getPaperDetailCached: async () => makeDetail(),
        fetchManifest: async () => manifest,
        fetchBinary: async () => await new Blob(['summary']).arrayBuffer(),
        saveAs: saved,
      },
    )

    expect(result.stats).toMatchObject({ filesAdded: 1, missingSummaries: 1 })
  })

})
