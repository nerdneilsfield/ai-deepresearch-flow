# WebDAV Static File Push — Design Spec

**Date:** 2026-03-29
**Scope:** Spec 1 — Add WebDAV push for static files to the existing `paper db api push` CLI command. Completes the "local merge to remote" workflow by automating static file upload (previously manual).

## Overview

The existing `paper db api push` command pushes paper metadata to a remote admin API but leaves static files (PDFs, images, markdown, translations) for manual upload. This spec adds automatic WebDAV push of the `static_export_dir` to a remote caddy server, integrated into the same command.

## Configuration

### `remote.toml` — new `[remote.webdav]` section

```toml
[remote]
api_base_url = "https://api.example.com"
admin_token = "env:ADMIN_TOKEN"

[remote.webdav]
url = "https://cdn.example.com/static"
username = "deploy"
password = "env:WEBDAV_PASSWORD"
```

- `url`: WebDAV root URL, corresponding to the caddy static file directory.
- `username` / `password`: HTTP Basic Auth credentials. `password` supports `env:` prefix.
- All three fields are required when `--static-export-dir` is used.
- `[remote.webdav]` section is optional — omitting it preserves existing behavior (metadata-only push).

### Config Loading

`load_remote_config()` in `push.py` is extended to parse `[remote.webdav]` and return an optional `WebDavConfig`. Validation:
- If `[remote.webdav]` exists, all three fields must be non-empty.
- `env:` prefix resolution reuses the same pattern as `admin_token`.

## Module Structure

```
python/deepresearch_flow/paper/snapshot/
  push.py              # Existing — extend load_remote_config() to parse webdav
  push_static.py       # New — WebDAV push logic
```

## Core Types (`push_static.py`)

```python
@dataclass(frozen=True)
class WebDavConfig:
    url: str        # https://cdn.example.com/static (no trailing slash)
    username: str
    password: str

@dataclass
class PushStaticStats:
    uploaded: int = 0
    skipped: int = 0   # Remote already exists (HEAD 200)
    failed: int = 0
    failed_files: list[dict[str, str]] = field(default_factory=list)
    # Each entry: {"path": "pdf/abc123.pdf", "error": "timeout"}
```

## Core Function

```python
def push_static_files(
    static_export_dir: Path,
    config: WebDavConfig,
    *,
    only_files: list[str] | None = None,  # For --retry-failed
) -> PushStaticStats:
```

### Flow

1. Discover **all** files under `static_export_dir` recursively, covering all subdirectories: `pdf/`, `images/`, `md/`, `md_translate/`, `summary/`, `manifest/`, and any others that exist. The function does not hardcode directory names — it walks the entire tree.
2. If `only_files` is provided, filter to only those relative paths (retry mode).
3. For each file:
   a. `HEAD {url}/{subpath}` — if 200, increment `skipped`, continue.
   b. `MKCOL {url}/{parent_dirs}/` — ensure parent directories exist (idempotent, ignore 405 "already exists").
   c. `PUT {url}/{subpath}` with file bytes — on success, increment `uploaded`.
   d. On failure (network error, non-2xx response): log warning, record in `failed_files`, increment `failed`.
4. Return `PushStaticStats`.

### Properties

- **Idempotent**: Content-addressed filenames mean HEAD 200 = already correct, safe to skip. This assumes the remote path is immutable-by-convention — once a content-hash filename exists, its content never changes. No ETag or checksum verification is performed.
- **Resumable**: Failed files are recorded; re-run with `--retry-failed` pushes only those.
- **Sync/serial**: Single-threaded httpx.Client with Basic Auth. No concurrency (caddy is the bottleneck, not the client).

## CLI Integration

### Extended `paper db api push` command

```bash
# Metadata only (existing behavior, unchanged)
uv run deepresearch-flow paper db api push \
  --snapshot-db ./snapshot.db \
  --config remote.toml

# Metadata + static files
uv run deepresearch-flow paper db api push \
  --snapshot-db ./snapshot.db \
  --static-export-dir ./static_export \
  --config remote.toml

# Retry only failed static files
uv run deepresearch-flow paper db api push \
  --snapshot-db ./snapshot.db \
  --static-export-dir ./static_export \
  --config remote.toml \
  --retry-failed push-static-errors.json
```

### Behavior matrix

| `--static-export-dir` | `[remote.webdav]` in config | Static push? |
|---|---|---|
| Not provided | Any | No (existing behavior) |
| Provided | Missing | No — only reads summary JSONs (existing behavior) |
| Provided | Present | Yes — push static files after metadata |

### `--retry-failed <path>`

- **Requires** `--static-export-dir` — the error JSON contains relative paths that are resolved against this directory. CLI raises `ClickException` if `--static-export-dir` is not provided.
- Reads the error JSON file produced by a previous run.
- Extracts the list of failed file paths.
- Passes them as `only_files` to `push_static_files()`.
- Metadata push is skipped in retry mode (only static files are retried).

## Error Handling

- **Auth failure (401)**: Abort immediately with `ClickException("WebDAV authentication failed")`.
- **Single file failure**: Record in `failed_files`, continue with remaining files.
- **Network timeout**: Treated as failure, recorded.
- **No retry within a single run**: Content-addressed files can be retried by re-running the command or using `--retry-failed`.

## Error Report

On any failures, writes `push-static-errors.json` to the shell's current working directory (not `static_export_dir` or the config file directory):

```json
[
  {"path": "pdf/a1b2c3d4e5f6.pdf", "error": "ReadTimeout"},
  {"path": "images/f7e8d9c0b1a2.png", "error": "HTTP 500"}
]
```

## CLI Output (rich table)

```
Static Files Push
┌──────────────┬──────────┬──────────┬────────┐
│ Directory    │ Uploaded │ Skipped  │ Failed │
├──────────────┼──────────┼──────────┼────────┤
│ pdf/         │        3 │       12 │      0 │
│ images/      │       45 │      130 │      1 │
│ md/          │       15 │       12 │      0 │
│ md_translate/│       15 │       12 │      0 │
│ summary/     │       30 │       24 │      0 │
│ manifest/    │        1 │        0 │      0 │
├──────────────┼──────────┼──────────┼────────┤
│ Total        │      109 │      190 │      1 │
└──────────────┴──────────┴──────────┴────────┘
```

The table rows are dynamically generated from whatever subdirectories exist in `static_export_dir` — not hardcoded.

When failures exist, prints the first 10 failed file paths below the table, and notes that the full list is saved to `push-static-errors.json`.

## Static Export Directory Structure (reference)

```
static_export_dir/
├── pdf/              {sha256}.pdf
├── images/           {sha256}.{ext}
├── md/               {sha256}.md
├── md_translate/
│   └── {lang}/       {sha256}.md
├── summary/          {paper_id}/{template_tag}.json
└── manifest/         (build metadata)
```

All files are content-addressed (SHA256 hash filenames). This enables:
- Deduplication: same content = same filename = skip on HEAD 200.
- Idempotent push: safe to re-run without side effects.

## Dependencies

- `httpx` — already in project. Used for HEAD/PUT/MKCOL requests with Basic Auth.
- No new dependencies.

## Testing Strategy

- Unit tests for `WebDavConfig` loading from TOML (valid, missing fields, env: prefix).
- Unit tests for `push_static_files()` with mocked httpx transport (HEAD 200 skip, PUT success, PUT failure, MKCOL).
- Unit tests for retry mode (`only_files` filtering).
- Unit tests for error JSON report writing/reading.
- CLI integration test via `click.testing.CliRunner`.

## Future (Out of Scope)

- Concurrent upload (parallel PUT requests).
- Spec 2 will add `[pipeline.webdav]` in `config.toml` for server-side push, reusing `push_static_files()`.
