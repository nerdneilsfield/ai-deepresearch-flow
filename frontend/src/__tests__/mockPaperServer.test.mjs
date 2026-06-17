import { afterEach, describe, expect, it } from 'vitest'

const handles = []

afterEach(async () => {
  while (handles.length) {
    const handle = handles.pop()
    await handle.close()
  }
})

describe('dev mock paper server', () => {
  it('finds bundled fixtures when started from the frontend directory', async () => {
    const { startMockPaperServer } = await import('../../dev/mock-paper-server.mjs')
    const handle = await startMockPaperServer({ repoRoot: process.cwd(), host: '127.0.0.1', port: 0, quiet: true })
    handles.push(handle)

    const response = await fetch(`${handle.url}/healthz`)
    expect(response.status).toBe(200)
    const health = await response.json()
    expect(health.ok).toBe(true)
    expect(health.papers).toBeGreaterThan(0)
  })

  it('serves real test-data paper APIs and rendering assets through HTTP', async () => {
    const { startMockPaperServer } = await import('../../dev/mock-paper-server.mjs')
    const handle = await startMockPaperServer({ host: '127.0.0.1', port: 0, quiet: true })
    handles.push(handle)

    const configResponse = await fetch(`${handle.url}/api/v1/config`)
    expect(configResponse.status).toBe(200)
    const config = await configResponse.json()
    expect(config.static_base_url).toBe(handle.url)

    const searchResponse = await fetch(`${handle.url}/api/v1/search?page=1&page_size=3&q=visual`)
    expect(searchResponse.status).toBe(200)
    const search = await searchResponse.json()
    expect(search.total).toBeGreaterThan(0)
    expect(search.items.length).toBeGreaterThan(0)
    expect(search.items[0].paper_id).toEqual(expect.any(String))
    expect(new URL(search.items[0].summary_url).origin).toBe(handle.url)

    const paperId = '9b5301a567bbc2e99cc7ac6d2d4946a6'
    const detailResponse = await fetch(`${handle.url}/api/v1/papers/${paperId}`)
    expect(detailResponse.status).toBe(200)
    const detail = await detailResponse.json()
    expect(detail.paper_id).toBe(paperId)
    const simpleSummaryUrl = new URL(detail.summary_urls.simple)
    const deepReadSummaryUrl = new URL(detail.summary_urls.deep_read)
    expect(simpleSummaryUrl.origin).toBe(handle.url)
    expect(simpleSummaryUrl.pathname).toBe(`/summary/${paperId}/simple.json`)
    expect(deepReadSummaryUrl.origin).toBe(handle.url)
    expect(deepReadSummaryUrl.pathname).toBe(`/summary/${paperId}/deep_read.json`)
    expect(new URL(detail.source_md_url).origin).toBe(handle.url)
    expect(new URL(detail.translated_md_urls.zh).origin).toBe(handle.url)

    const deepReadResponse = await fetch(detail.summary_urls.deep_read)
    expect(deepReadResponse.status).toBe(200)
    const deepRead = await deepReadResponse.text()
    expect(deepRead).toContain('```mermaid')
    expect(deepRead).toContain('$$')

    const markdownResponse = await fetch(detail.source_md_url)
    expect(markdownResponse.status).toBe(200)
    const markdown = await markdownResponse.text()
    expect(markdown).toContain('\\textcircled')



    const pdfPaperResponse = await fetch(`${handle.url}/api/v1/papers/67e1d4d97d3be2a1e70f686a55408da5`)
    expect(pdfPaperResponse.status).toBe(200)
    const pdfPaper = await pdfPaperResponse.json()
    expect(new URL(pdfPaper.pdf_url).origin).toBe(handle.url)
    const pdfResponse = await fetch(pdfPaper.pdf_url)
    expect(pdfResponse.status).toBe(200)
    expect(pdfResponse.headers.get('content-type')).toContain('application/pdf')

    const facetResponse = await fetch(`${handle.url}/api/v1/facets/authors?page=1&page_size=5`)
    expect(facetResponse.status).toBe(200)
    const facet = await facetResponse.json()
    expect(facet.items.length).toBeGreaterThan(0)
    expect(facet.items[0].paper_count).toBeGreaterThan(0)
  })
})
