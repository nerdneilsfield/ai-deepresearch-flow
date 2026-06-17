# DeepResearch Flow Frontend

## Local paper mock server

Use this when debugging Markdown, KaTeX, Mermaid, PDF, image, selection, and summary rendering without deploying the backend.

The mock server serves a small set of real papers from `dev/fixtures/EventCamera-static-dir`. The default tracked fixture set is copied from `../test-data/EventCamera` and includes papers with:

- summary JSON containing `$$...$$` formulas;
- summary JSON containing Mermaid code fences;
- source Markdown containing `\textcircled`, which is useful for KaTeX compatibility checks;
- real manifest, translated Markdown, image paths, and one small PDF fixture when available.

Start the mock API/static server:

```bash
cd frontend
npm run mock:paper
```

In another shell, point Vite at the mock server:

```bash
cd frontend
VITE_PAPER_DB_API_BASE=http://127.0.0.1:4317/api/v1 \
VITE_PAPER_DB_STATIC_BASE=http://127.0.0.1:4317 \
npm run dev
```

Open one of the sample URLs printed by the mock server, for example:

```text
http://127.0.0.1:5173/paper/9b5301a567bbc2e99cc7ac6d2d4946a6?view=summary&template=deep_read
```

Useful options:

```bash
npm run mock:paper -- --port 4321
npm run mock:paper -- --limit 3
npm run mock:paper -- --static-root test-data/EventCamera/EventCamera-static-dir
```

The server exposes the same frontend-facing shape used by the app for the common rendering path:

- `GET /api/v1/config`
- `GET /api/v1/search`
- `GET /api/v1/papers/:paper_id`
- `GET /api/v1/stats`
- `GET /api/v1/facets/:facet`
- static `summary/`, `md/`, `md_translate/`, `manifest/`, `pdf/`, and `images/` files
