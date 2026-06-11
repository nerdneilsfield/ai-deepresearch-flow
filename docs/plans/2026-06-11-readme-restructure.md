# README Restructuring Plan

## Goal

Split the current ~1700-line monolithic README files (EN/ZH) into a lean root README + 5 topic-specific docs per language under `docs/en/` and `docs/zh/`.

## Target Structure

```
README.md                 (~400 lines, 精简入口)
README_ZH.md              (~400 lines, 同上中文)
docs/
├── en/
│   ├── workflow.md              (增量构建、合并 JSON/BibTeX、补充模板/翻译)
│   ├── deployment.md            (CDN 部署、Nginx/Caddy、前端构建、Docker)
│   ├── api-and-mcp.md           (Admin API、Push、Push-Semantic、MCP Tools/Resources)
│   ├── reference.md             (Translator/Extract/DB/Recognize 详细功能 + 配置说明)
│   └── snapshot-management.md   (Snapshot 迁移、补充缺失、添加新论文)
└── zh/
    ├── workflow.md
    ├── deployment.md
    ├── api-and-mcp.md
    ├── reference.md
    └── snapshot-management.md
```

## Content Mapping

### README.md / README_ZH.md (kept, ~400 lines each)

| Section | Content |
|---------|---------|
| Hero (badges, logo, tagline) | Unchanged |
| Core Pain Points | Unchanged |
| Solution / Key Features | Unchanged, add link to docs |
| Quick Start: Installation | Unchanged |
| Quick Start: Configuration | Keep the minimal provider config example only, remove embedding/rerank/translator_config detail |
| Zero to Hero (Steps 1-5) | Keep Extract, Verify, Translate, OCR, Repair, Serve — with commands and screenshots |
| Zero to Hero: Semantic Search (4.1) | Keep, move detailed notes to reference.md |
| Step 5: MCP brief mention | Keep one-liner + link to api-and-mcp.md |
| Further Reading | New section with links to all 5 sub-docs |

### docs/{en,zh}/workflow.md

- Incremental PDF Library Workflow
- Merge Paper JSONs (library, templates, BibTeX)
- Merge Multiple Databases

### docs/{en,zh}/deployment.md

- Deployment overview (CDN architecture)
- Build snapshot + static export
- Caddy static serving example
- Nginx example (API + frontend same domain, static separate domain)
- Frontend dev/build
- Docker Support (all variants, compose profiles)

### docs/{en,zh}/api-and-mcp.md

- API server startup (basic + advanced modes)
- Advanced search startup rules
- BibTeX metadata endpoint
- Admin API (enable, endpoints, curl examples)
- Push from Local DB to Remote (full api push reference)
- Push Semantic Only
- MCP (FastMCP Streamable HTTP + SSE)
  - Auth modes (static bearer, GitHub OAuth)
  - All Tools reference (search_papers, search_papers_semantic, get_paper_metadata, etc.)
  - All Resources reference (paper:// URIs)

### docs/{en,zh}/reference.md

- Translator: OCR-Safe Translation (detailed)
- Paper Extract: Structured Knowledge (detailed)
- Database and UI (detailed)
- Paper DB Compare: Coverage Audit
- Paper DB Extract: Matched Export
- Recognize: OCR Post-Processing (detailed)
- Configuration Reference (model routing, weighted pools, mode probing)

### docs/{en,zh}/snapshot-management.md

- Build Snapshot + Serve API + Frontend (from original Step 4.5)
- Supplement Missing Templates (both methods)
- Add New Papers (update)
- Difference between supplement vs update
- Snapshot Migration (legacy to DOI/BibTeX)
- Rebuild with Previous Snapshot
- Supplement Missing Translations

## Rules

1. Each sub-doc has a clear H1 title
2. Cross-reference between docs using relative links (e.g. `[Deployment](./deployment.md)`)
3. README links to sub-docs at the end in a "Further Reading" section
4. Both EN and ZH versions maintain content parity (ZH is NOT a machine translation — it should be separately maintained)
5. Keep all existing screenshots and badges
6. No new content — only reorganize existing content
7. Remove redundancy: content that appears in both README and sub-docs should appear only in one place
