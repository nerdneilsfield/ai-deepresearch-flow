#!/usr/bin/env node
import { createServer } from 'node:http'
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { extname, isAbsolute, join, normalize, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

function resolveDefaultRepoRoot() {
  try {
    const moduleDir = fileURLToPath(new URL('.', import.meta.url))
    return resolve(moduleDir, '..', '..')
  } catch {
    const cwd = process.cwd()
    if (existsSync(join(cwd, 'test-data'))) return cwd
    if (existsSync(join(cwd, '..', 'test-data'))) return resolve(cwd, '..')
    return cwd
  }
}

const DEFAULT_REPO_ROOT = resolveDefaultRepoRoot()
const DEFAULT_STATIC_ROOT = 'frontend/dev/fixtures/EventCamera-static-dir'
const DEFAULT_FIXTURE_IDS = [
  // Source markdown contains \textcircled; deep_read contains $$ formulas and mermaid.
  '9b5301a567bbc2e99cc7ac6d2d4946a6',
  // Real summary used during KaTeX/Mermaid debugging; small enough for fast loading.
  '7d4e270f6113fe81a1a076dd9c591002',
  // Includes a small real PDF for local PDF viewer smoke testing.
  '67e1d4d97d3be2a1e70f686a55408da5',
]

const MIME_TYPES = new Map([
  ['.json', 'application/json; charset=utf-8'],
  ['.md', 'text/markdown; charset=utf-8'],
  ['.pdf', 'application/pdf'],
  ['.jpg', 'image/jpeg'],
  ['.jpeg', 'image/jpeg'],
  ['.png', 'image/png'],
  ['.gif', 'image/gif'],
  ['.webp', 'image/webp'],
  ['.svg', 'image/svg+xml; charset=utf-8'],
  ['.txt', 'text/plain; charset=utf-8'],
])

function readJsonFile(path) {
  return JSON.parse(readFileSync(path, 'utf8'))
}

function arrayFrom(value) {
  if (Array.isArray(value)) return value.filter((item) => typeof item === 'string')
  if (typeof value === 'string' && value.trim()) return [value.trim()]
  return []
}

function safeSnippet(value, max = 260) {
  if (typeof value !== 'string') return ''
  const compact = value.replace(/\s+/g, ' ').trim()
  return compact.length <= max ? compact : `${compact.slice(0, max - 1)}…`
}

function firstYear(...values) {
  for (const value of values) {
    const match = String(value ?? '').match(/\b(19|20)\d{2}\b/)
    if (match) return match[0]
  }
  return ''
}

function firstExistingJson(staticRoot, staticPaths) {
  for (const staticPath of staticPaths) {
    if (!staticPath) continue
    const filePath = join(staticRoot, staticPath)
    if (existsSync(filePath)) {
      try {
        return readJsonFile(filePath)
      } catch {
        return {}
      }
    }
  }
  return {}
}

function summaryTemplatePaths(staticRoot, manifest) {
  const assets = manifest.assets ?? {}
  const templates = new Map()

  for (const item of assets.summary_templates ?? []) {
    if (!item?.template_tag || !item.static_path) continue
    if (existsSync(join(staticRoot, item.static_path))) {
      templates.set(String(item.template_tag), item.static_path)
    }
  }

  const topLevelSummary = assets.summary?.static_path
  if (topLevelSummary && existsSync(join(staticRoot, topLevelSummary))) {
    if (!templates.has('simple')) templates.set('simple', topLevelSummary)
    if (!templates.has('default')) templates.set('default', topLevelSummary)
  }

  return templates
}

function existingStaticPath(staticRoot, staticPath) {
  if (!staticPath) return null
  return existsSync(join(staticRoot, staticPath)) ? staticPath : null
}

function existingTranslatedMarkdownPaths(staticRoot, manifest) {
  const translated = {}
  for (const item of manifest.assets?.translated_md ?? []) {
    if (item?.lang && existingStaticPath(staticRoot, item.static_path)) {
      translated[String(item.lang)] = item.static_path
    }
  }
  return translated
}

function loadPaperRecord(staticRoot, manifestPath) {
  const manifest = readJsonFile(manifestPath)
  const paperId = manifest.paper_id ?? manifestPath.replace(/\.json$/, '')
  const templates = summaryTemplatePaths(staticRoot, manifest)
  const preferredTemplate = templates.has('simple') ? 'simple' : (templates.keys().next().value ?? 'default')
  const metadata = firstExistingJson(staticRoot, [
    templates.get('simple'),
    manifest.assets?.summary?.static_path,
    templates.values().next().value,
  ])

  const title = String(
    metadata.paper_title ?? metadata.title ?? manifest.title ?? manifest.folder_name_short ?? paperId,
  )
  const authors = arrayFrom(metadata.paper_authors ?? metadata.authors)
  const venue = String(metadata.publication_venue ?? metadata.venue ?? '')
  const year = firstYear(metadata.publication_date, metadata.year, manifest.folder_name, manifest.folder_name_short)

  return {
    paper_id: String(paperId),
    manifest,
    metadata,
    title,
    year,
    venue,
    authors,
    keywords: arrayFrom(metadata.keywords),
    institutions: arrayFrom(metadata.paper_institutions ?? metadata.institutions),
    tags: ['mock', 'test-data', 'event-camera'],
    preferredTemplate,
    summaryTemplates: Object.fromEntries(templates),
    summaryPreview: safeSnippet(metadata.summary ?? metadata.abstract),
    abstract: String(metadata.abstract ?? ''),
    output_language: typeof metadata.output_language === 'string' ? metadata.output_language : undefined,
    provider: typeof metadata.provider === 'string' ? metadata.provider : undefined,
    model: typeof metadata.model === 'string' ? metadata.model : undefined,
    prompt_template: typeof metadata.prompt_template === 'string' ? metadata.prompt_template : undefined,
    sourceMd: existingStaticPath(staticRoot, manifest.assets?.source_md?.static_path),
    translatedMd: existingTranslatedMarkdownPaths(staticRoot, manifest),
    pdf: existingStaticPath(staticRoot, manifest.assets?.pdf?.static_path),
    imageCount: Array.isArray(manifest.images) ? manifest.images.length : 0,
  }
}

function discoverPapers({ repoRoot = DEFAULT_REPO_ROOT, staticRoot, fixtureIds = DEFAULT_FIXTURE_IDS, limit = 8 } = {}) {
  const absoluteStaticRoot = resolve(repoRoot, staticRoot ?? DEFAULT_STATIC_ROOT)
  const manifestRoot = join(absoluteStaticRoot, 'manifest')
  if (!existsSync(manifestRoot)) {
    throw new Error(`Mock paper manifest directory not found: ${manifestRoot}`)
  }

  const loaded = new Map()
  const loadById = (paperId) => {
    if (!paperId || loaded.has(paperId)) return
    const manifestPath = join(manifestRoot, `${paperId}.json`)
    if (!existsSync(manifestPath)) return
    loaded.set(paperId, loadPaperRecord(absoluteStaticRoot, manifestPath))
  }

  for (const paperId of fixtureIds ?? []) {
    loadById(paperId)
    if (loaded.size >= limit) break
  }

  if (loaded.size < limit) {
    for (const entry of readdirSync(manifestRoot).sort()) {
      if (!entry.endsWith('.json')) continue
      loadById(entry.slice(0, -'.json'.length))
      if (loaded.size >= limit) break
    }
  }

  const papers = [...loaded.values()]
  if (!papers.length) throw new Error(`No mock papers found under ${manifestRoot}`)

  return {
    repoRoot: resolve(repoRoot),
    staticRoot: absoluteStaticRoot,
    papers,
    byId: new Map(papers.map((paper) => [paper.paper_id, paper])),
  }
}

function originFor(req, publicBaseUrl) {
  if (publicBaseUrl) return publicBaseUrl.replace(/\/$/, '')
  const host = req.headers.host ?? '127.0.0.1'
  return `http://${host}`
}

function assetUrl(origin, staticPath) {
  return `${origin}/${String(staticPath).split('/').map(encodeURIComponent).join('/')}`
}

function manifestUrl(origin, paperId) {
  return `${origin}/manifest/${encodeURIComponent(paperId)}.json`
}

function paperToDetail(paper, origin) {
  const summaryUrls = Object.fromEntries(
    Object.entries(paper.summaryTemplates).map(([template, staticPath]) => [template, assetUrl(origin, staticPath)]),
  )
  const summaryUrl = summaryUrls[paper.preferredTemplate] ?? summaryUrls.simple ?? Object.values(summaryUrls)[0]
  const translated = Object.fromEntries(
    Object.entries(paper.translatedMd).map(([lang, staticPath]) => [lang, assetUrl(origin, staticPath)]),
  )

  return {
    paper_id: paper.paper_id,
    title: paper.title,
    year: paper.year,
    venue: paper.venue,
    authors: paper.authors,
    keywords: paper.keywords,
    institutions: paper.institutions,
    tags: paper.tags,
    output_language: paper.output_language,
    provider: paper.provider,
    model: paper.model,
    prompt_template: paper.prompt_template,
    preferred_summary_template: paper.preferredTemplate,
    summary_urls: summaryUrls,
    summary_url: summaryUrl,
    pdf_url: paper.pdf ? assetUrl(origin, paper.pdf) : null,
    source_md_url: paper.sourceMd ? assetUrl(origin, paper.sourceMd) : null,
    translated_md_urls: translated,
    images_base_url: `${origin}/images/`,
    manifest_url: manifestUrl(origin, paper.paper_id),
  }
}

function paperToSearchItem(paper, origin, index) {
  const detail = paperToDetail(paper, origin)
  return {
    ...detail,
    paper_index: index,
    summary_preview: paper.summaryPreview,
    has_pdf: Boolean(paper.pdf),
    has_source: Boolean(paper.sourceMd),
    has_translated: Object.keys(paper.translatedMd).length > 0,
  }
}

function parsePositiveInt(value, fallback, max = 200) {
  const parsed = Number.parseInt(String(value ?? ''), 10)
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback
  return Math.min(parsed, max)
}

function paginate(items, url) {
  const page = parsePositiveInt(url.searchParams.get('page'), 1, 10_000)
  const pageSize = parsePositiveInt(url.searchParams.get('page_size'), 20, 200)
  const start = (page - 1) * pageSize
  const pageItems = items.slice(start, start + pageSize)
  return { page, page_size: pageSize, total: items.length, has_more: start + pageSize < items.length, pageItems }
}

function matchesQuery(paper, query) {
  if (!query) return true
  const haystack = [
    paper.title,
    paper.venue,
    paper.abstract,
    paper.summaryPreview,
    ...paper.authors,
    ...paper.keywords,
    ...paper.institutions,
  ].join(' ').toLowerCase()
  return haystack.includes(query.toLowerCase())
}

function sortPapers(papers, sort) {
  const copy = [...papers]
  if (sort === 'title_asc') return copy.sort((a, b) => a.title.localeCompare(b.title))
  if (sort === 'year_asc') return copy.sort((a, b) => String(a.year).localeCompare(String(b.year)))
  return copy.sort((a, b) => String(b.year).localeCompare(String(a.year)) || a.title.localeCompare(b.title))
}

function facetValues(paper, facet) {
  switch (facet) {
    case 'authors': return paper.authors
    case 'venues': return paper.venue ? [paper.venue] : []
    case 'years': return paper.year ? [paper.year] : []
    case 'institutions': return paper.institutions
    case 'keywords': return paper.keywords
    case 'tags': return paper.tags
    case 'summary_templates': return Object.keys(paper.summaryTemplates)
    case 'output_languages': return paper.output_language ? [paper.output_language] : []
    case 'providers': return paper.provider ? [paper.provider] : []
    case 'models': return paper.model ? [paper.model] : []
    case 'prompt_templates': return paper.prompt_template ? [paper.prompt_template] : []
    case 'translation_langs': return Object.keys(paper.translatedMd)
    default: return []
  }
}

function facetItems(papers, facet) {
  const counts = new Map()
  for (const paper of papers) {
    for (const value of facetValues(paper, facet)) {
      const key = String(value)
      counts.set(key, (counts.get(key) ?? 0) + 1)
    }
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([value, paper_count]) => ({ id: value, value, paper_count }))
}

function statsFor(papers) {
  return {
    total: papers.length,
    years: facetItems(papers, 'years'),
    months: [],
    authors: facetItems(papers, 'authors'),
    venues: facetItems(papers, 'venues'),
    institutions: facetItems(papers, 'institutions'),
    keywords: facetItems(papers, 'keywords'),
    tags: facetItems(papers, 'tags'),
  }
}

function filterByFacet(papers, facet, value) {
  const expected = String(value)
  return papers.filter((paper) => facetValues(paper, facet).some((item) => String(item) === expected))
}

function writeHeaders(res, status, contentType) {
  res.writeHead(status, {
    'content-type': contentType,
    'access-control-allow-origin': '*',
    'access-control-allow-methods': 'GET,HEAD,OPTIONS',
    'access-control-allow-headers': 'content-type,authorization',
    'cache-control': 'no-store',
  })
}

function sendJson(res, status, body) {
  writeHeaders(res, status, 'application/json; charset=utf-8')
  res.end(JSON.stringify(body, null, 2))
}

function sendText(res, status, text) {
  writeHeaders(res, status, 'text/plain; charset=utf-8')
  res.end(text)
}

function staticFilePath(staticRoot, pathname) {
  const decoded = decodeURIComponent(pathname).replace(/^\/+/, '')
  if (!decoded || decoded.includes('\0') || isAbsolute(decoded)) return null
  const normalized = normalize(decoded)
  if (normalized.startsWith('..') || normalized.includes(`..${sep}`)) return null
  const candidate = join(staticRoot, normalized)
  const rootWithSep = staticRoot.endsWith(sep) ? staticRoot : `${staticRoot}${sep}`
  if (candidate !== staticRoot && !candidate.startsWith(rootWithSep)) return null
  return candidate
}

function serveStatic(dataset, pathname, res, method) {
  const filePath = staticFilePath(dataset.staticRoot, pathname)
  if (!filePath || !existsSync(filePath) || !statSync(filePath).isFile()) {
    sendJson(res, 404, { error: 'not_found', path: pathname })
    return true
  }
  const contentType = MIME_TYPES.get(extname(filePath).toLowerCase()) ?? 'application/octet-stream'
  writeHeaders(res, 200, contentType)
  if (method === 'HEAD') {
    res.end()
    return true
  }
  res.end(readFileSync(filePath))
  return true
}

function handleApi(dataset, req, res, url, origin) {
  const pathname = url.pathname

  if (pathname === '/api/v1/config') {
    sendJson(res, 200, { static_base_url: origin })
    return true
  }

  if (pathname === '/api/v1/search') {
    const query = url.searchParams.get('q') ?? ''
    const sort = url.searchParams.get('sort') ?? 'year_desc'
    const filtered = sortPapers(dataset.papers.filter((paper) => matchesQuery(paper, query)), sort)
    const { page, page_size, total, has_more, pageItems } = paginate(filtered, url)
    sendJson(res, 200, {
      page,
      page_size,
      total,
      has_more,
      items: pageItems.map((paper, index) => paperToSearchItem(paper, origin, index)),
    })
    return true
  }

  if (pathname === '/api/v1/stats') {
    sendJson(res, 200, statsFor(dataset.papers))
    return true
  }

  const bibtexMatch = pathname.match(/^\/api\/v1\/papers\/([^/]+)\/bibtex$/)
  if (bibtexMatch) {
    const paper = dataset.byId.get(decodeURIComponent(bibtexMatch[1]))
    if (!paper) {
      sendJson(res, 404, { error: 'paper_not_found' })
      return true
    }
    sendJson(res, 200, {
      paper_id: paper.paper_id,
      doi: null,
      bibtex_key: paper.paper_id,
      entry_type: 'misc',
      bibtex_raw: `@misc{${paper.paper_id},\n  title={${paper.title.replace(/[{}]/g, '')}},\n  year={${paper.year || 'unknown'}}\n}`,
    })
    return true
  }

  const paperMatch = pathname.match(/^\/api\/v1\/papers\/([^/]+)$/)
  if (paperMatch) {
    const paper = dataset.byId.get(decodeURIComponent(paperMatch[1]))
    if (!paper) {
      sendJson(res, 404, { error: 'paper_not_found' })
      return true
    }
    sendJson(res, 200, paperToDetail(paper, origin))
    return true
  }

  const byValuePapersMatch = pathname.match(/^\/api\/v1\/facets\/([^/]+)\/by-value\/([^/]+)\/papers$/)
  if (byValuePapersMatch) {
    const facet = decodeURIComponent(byValuePapersMatch[1])
    const value = decodeURIComponent(byValuePapersMatch[2])
    const filtered = filterByFacet(dataset.papers, facet, value)
    const { page, page_size, total, has_more, pageItems } = paginate(filtered, url)
    sendJson(res, 200, {
      page,
      page_size,
      total,
      has_more,
      items: pageItems.map((paper, index) => paperToSearchItem(paper, origin, index)),
    })
    return true
  }

  const byValueStatsMatch = pathname.match(/^\/api\/v1\/facets\/([^/]+)\/by-value\/([^/]+)\/stats$/)
  if (byValueStatsMatch) {
    const facet = decodeURIComponent(byValueStatsMatch[1])
    const value = decodeURIComponent(byValueStatsMatch[2])
    sendJson(res, 200, { facet, value, stats: statsFor(filterByFacet(dataset.papers, facet, value)) })
    return true
  }

  const facetPapersMatch = pathname.match(/^\/api\/v1\/facets\/([^/]+)\/([^/]+)\/papers$/)
  if (facetPapersMatch) {
    const facet = decodeURIComponent(facetPapersMatch[1])
    const value = decodeURIComponent(facetPapersMatch[2])
    const filtered = filterByFacet(dataset.papers, facet, value)
    const { page, page_size, total, has_more, pageItems } = paginate(filtered, url)
    sendJson(res, 200, {
      page,
      page_size,
      total,
      has_more,
      items: pageItems.map((paper, index) => paperToSearchItem(paper, origin, index)),
    })
    return true
  }

  const facetMatch = pathname.match(/^\/api\/v1\/facets\/([^/]+)$/)
  if (facetMatch) {
    const facet = decodeURIComponent(facetMatch[1])
    const { page, page_size, total, has_more, pageItems } = paginate(facetItems(dataset.papers, facet), url)
    sendJson(res, 200, { page, page_size, total, has_more, items: pageItems })
    return true
  }

  return false
}

function requestHandler(dataset, options = {}) {
  return (req, res) => {
    if (!req.url) {
      sendJson(res, 400, { error: 'missing_url' })
      return
    }
    if (req.method === 'OPTIONS') {
      writeHeaders(res, 204, 'text/plain; charset=utf-8')
      res.end()
      return
    }
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      sendJson(res, 405, { error: 'method_not_allowed' })
      return
    }

    const origin = originFor(req, options.publicBaseUrl)
    const url = new URL(req.url, origin)
    try {
      if (url.pathname.startsWith('/api/v1/') && handleApi(dataset, req, res, url, origin)) return
      if (/^\/(summary|md|md_translate|manifest|pdf|images)\//.test(url.pathname)) {
        serveStatic(dataset, url.pathname, res, req.method)
        return
      }
      if (url.pathname === '/healthz') {
        sendJson(res, 200, { ok: true, papers: dataset.papers.length })
        return
      }
      sendJson(res, 404, { error: 'not_found', path: url.pathname })
    } catch (error) {
      sendJson(res, 500, { error: 'mock_server_error', message: error instanceof Error ? error.message : String(error) })
    }
  }
}

export async function startMockPaperServer(options = {}) {
  const host = options.host ?? process.env.DRFLOW_MOCK_HOST ?? '127.0.0.1'
  const port = Number(options.port ?? process.env.DRFLOW_MOCK_PORT ?? 4317)
  const limit = Number(options.limit ?? process.env.DRFLOW_MOCK_LIMIT ?? DEFAULT_FIXTURE_IDS.length)
  const repoRoot = options.repoRoot ?? process.env.DRFLOW_MOCK_REPO_ROOT ?? DEFAULT_REPO_ROOT
  const staticRoot = options.staticRoot ?? process.env.DRFLOW_MOCK_STATIC_ROOT ?? DEFAULT_STATIC_ROOT
  const fixtureIds = options.fixtureIds ?? DEFAULT_FIXTURE_IDS
  const dataset = discoverPapers({ repoRoot, staticRoot, fixtureIds, limit })
  const server = createServer(requestHandler(dataset, { publicBaseUrl: options.publicBaseUrl }))

  await new Promise((resolveListen, rejectListen) => {
    server.once('error', rejectListen)
    server.listen(port, host, () => {
      server.off('error', rejectListen)
      resolveListen()
    })
  })

  const address = server.address()
  const addressHost = typeof address === 'object' && address ? address.address : host
  const addressPort = typeof address === 'object' && address ? address.port : port
  const printableHost = addressHost === '::' ? '127.0.0.1' : addressHost
  const url = (options.publicBaseUrl ?? `http://${printableHost}:${addressPort}`).replace(/\/$/, '')

  if (!options.quiet) {
    const sample = dataset.papers[0]
    console.log(`Mock paper server: ${url}`)
    console.log(`Fixture dataset: ${dataset.staticRoot}`)
    console.log(`Papers: ${dataset.papers.length}`)
    console.log(`Sample: ${url}/paper/${sample.paper_id}?view=summary&template=deep_read`)
    console.log(`API base: ${url}/api/v1`)
  }

  return {
    url,
    dataset,
    server,
    close: () => new Promise((resolveClose, rejectClose) => {
      server.close((error) => (error ? rejectClose(error) : resolveClose()))
    }),
  }
}

function parseCliArgs(argv) {
  const options = {}
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]
    const next = argv[index + 1]
    if (arg === '--host' && next) { options.host = next; index += 1 }
    else if (arg === '--port' && next) { options.port = Number(next); index += 1 }
    else if (arg === '--limit' && next) { options.limit = Number(next); index += 1 }
    else if (arg === '--repo-root' && next) { options.repoRoot = next; index += 1 }
    else if (arg === '--static-root' && next) { options.staticRoot = next; index += 1 }
    else if (arg === '--public-base-url' && next) { options.publicBaseUrl = next; index += 1 }
  }
  return options
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  startMockPaperServer(parseCliArgs(process.argv.slice(2))).catch((error) => {
    console.error(error instanceof Error ? error.stack : error)
    process.exit(1)
  })
}
