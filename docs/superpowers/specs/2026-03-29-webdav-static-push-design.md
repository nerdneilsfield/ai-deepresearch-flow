# Remote Storage + Static File Push — Design Spec

**Date:** 2026-03-29
**Scope:** Spec 1 — Add a pluggable remote storage abstraction (WebDAV first) and integrate static file push into the existing `paper db api push` CLI command. Completes the "local merge to remote" workflow by automating static file upload (previously manual).

## Overview

The existing `paper db api push` command pushes paper metadata to a remote admin API but leaves static files (PDFs, images, markdown, translations, summaries) for manual upload. This spec adds:

1. A **pluggable remote storage layer** (`storage/`) with Protocol-based abstraction, first implementing WebDAV.
2. A **`push_static_files()` function** that uses any `RemoteStorage` backend to push the `static_export_dir`.
3. **CLI integration** into the existing `paper db api push` command.

## Architecture

```
storage/                        # Reusable remote storage abstraction
  base.py                       # RemoteStorage protocol
  webdav.py                     # WebDAV implementation (httpx)
  factory.py                    # type → instance dispatcher

paper/snapshot/
  push.py                       # Extend config loading for [remote.storage]
  push_static.py                # push_static_files() — uses RemoteStorage protocol
```

`push_static.py` depends only on `RemoteStorage` protocol — it does not know whether the backend is WebDAV, S3, or R2.

## Remote Storage Protocol (`storage/base.py`)

```python
class RemoteStorage(Protocol):
    def exists(self, remote_path: str) -> bool:
        """Check if a file exists at the remote path."""
        ...

    def mkdir(self, remote_path: str) -> None:
        """Ensure a directory exists at the remote path (idempotent)."""
        ...

    def upload(self, remote_path: str, data: bytes) -> None:
        """Upload bytes to the remote path. Raises on failure."""
        ...

    def close(self) -> None:
        """Release underlying resources (connections, etc.)."""
        ...

    def __enter__(self) -> RemoteStorage: ...
    def __exit__(self, *args: object) -> None: ...
```

- `remote_path` is always a forward-slash relative path (e.g., `pdf/abc123.pdf`).
- The base URL / bucket / root is encapsulated inside each implementation.
- `exists()` returning True means "skip this file" — assumes immutable-by-convention because filenames are content-addressed. No ETag or checksum verification.
- `mkdir()` is idempotent — calling it on an existing directory is a no-op.
- `upload()` raises `httpx.HTTPStatusError` or similar on failure.

## WebDAV Implementation (`storage/webdav.py`)

```python
class WebDavStorage:
    def __init__(self, url: str, username: str, password: str) -> None: ...
    def exists(self, remote_path: str) -> bool: ...     # HEAD → 200?
    def mkdir(self, remote_path: str) -> None: ...      # MKCOL, ignore 405
    def upload(self, remote_path: str, data: bytes) -> None: ...  # PUT
```

- Uses `httpx.Client` with Basic Auth, created in `__init__`, closed via `close()` method.
- `exists()`: `HEAD {url}/{remote_path}` → 200 = True, 404 = False, 401 = raise `StorageAuthError`. **All other status codes (500, 403, 429, etc.)** raise `httpx.HTTPStatusError` — they must not be silently treated as "not found".
- `mkdir()`: `MKCOL {url}/{remote_path}/` → 201 or 405 = OK, 401 = raise `StorageAuthError`. **All other status codes** raise.
- `upload()`: `PUT {url}/{remote_path}` → 200/201/204 = OK, 401 = raise `StorageAuthError`, else raise.
- Supports context manager (`__enter__` / `__exit__`) for resource cleanup.

### Custom Exception

```python
class StorageAuthError(RuntimeError):
    """Raised when remote storage authentication fails."""
```

Defined in `storage/base.py`. All backends raise `StorageAuthError` on 401/auth failure. `push_static_files()` catches `StorageAuthError` to abort immediately, while catching generic `Exception` for per-file failure recording.

## Config Type (`storage/config.py`)

```python
@dataclass(frozen=True)
class StorageConfig:
    type: str       # "webdav", future: "s3", "r2"
    url: str
    username: str
    password: str
```

## Factory (`storage/factory.py`)

```python
def create_storage(config: StorageConfig) -> RemoteStorage:
    if config.type == "webdav":
        from .webdav import WebDavStorage
        return WebDavStorage(url=config.url, username=config.username, password=config.password)
    raise ValueError(f"Unknown storage type: {config.type}")
```

## Configuration

### `remote.toml` — new `[remote.storage]` section

```toml
[remote]
api_base_url = "https://api.example.com"
admin_token = "env:ADMIN_TOKEN"

[remote.storage]
type = "webdav"
url = "https://cdn.example.com/static"
username = "deploy"
password = "env:WEBDAV_PASSWORD"
```

- `type`: Storage backend type. Currently only `"webdav"`. Future: `"s3"`, `"r2"`.
- Other fields are backend-specific but validated at config load time.
- `password` supports `env:` prefix.
- `[remote.storage]` section is optional — omitting it preserves existing behavior (metadata-only push).

### Config Types

```python
@dataclass(frozen=True)
class StorageConfig:
    type: str       # "webdav", future: "s3", "r2"
    url: str
    username: str
    password: str
```

`load_remote_config()` in `push.py` is extended to parse `[remote.storage]` and return an optional `StorageConfig` on `RemoteConfig`:

```python
@dataclass(frozen=True)
class RemoteConfig:
    api_base_url: str
    admin_token: str
    batch_size: int = DEFAULT_BATCH_SIZE
    storage: StorageConfig | None = None
```

Validation:
- If `[remote.storage]` exists, `type`, `url`, `username`, `password` must be non-empty.
- `env:` prefix resolution reuses the same pattern as `admin_token`.

## Push Static Function (`push_static.py`)

```python
def push_static_files(
    static_export_dir: Path,
    storage: RemoteStorage,
    *,
    only_files: list[str] | None = None,  # For --retry-failed
) -> PushStaticStats:
```

Note: accepts `RemoteStorage` protocol, not a config. The CLI creates the storage instance via factory.

### PushStaticStats

```python
@dataclass
class PushStaticStats:
    uploaded: int = 0
    skipped: int = 0
    failed: int = 0
    failed_files: list[dict[str, str]] = field(default_factory=list)
    per_directory: dict[str, dict[str, int]] = field(default_factory=dict)
```

### Flow

1. Discover **all** files under `static_export_dir` recursively, covering all subdirectories: `pdf/`, `images/`, `md/`, `md_translate/`, `summary/`, `manifest/`, and any others that exist. The function does not hardcode directory names — it walks the entire tree.
2. If `only_files` is provided, filter to only those relative paths (retry mode).
3. For each file:
   a. `storage.exists(subpath)` — if True, increment `skipped`, continue.
   b. `storage.mkdir(parent_path)` — ensure parent directories exist.
   c. `storage.upload(subpath, file_bytes)` — on success, increment `uploaded`.
   d. On failure (any exception except `StorageAuthError`): log warning, record in `failed_files`, increment `failed`.
4. `StorageAuthError` (from any `storage.*` call) → propagate immediately, abort all. Other exceptions are caught per-file.
5. Return `PushStaticStats`.

### Properties

- **Backend-agnostic**: Only uses `RemoteStorage` protocol methods.
- **Idempotent**: `exists()` returns True for content-addressed files that are already uploaded. This assumes the remote path is immutable-by-convention.
- **Resumable**: Failed files are recorded; re-run with `--retry-failed` pushes only those.
- **Sync/serial**: No concurrency within `push_static_files()`.

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

| `--static-export-dir` | `[remote.storage]` in config | Static push? |
|---|---|---|
| Not provided | Any | No (existing behavior) |
| Provided | Missing | No — only reads summary JSONs (existing behavior) |
| Provided | Present | Yes — push static files after metadata |

### `--retry-failed <path>`

- **Requires** `--static-export-dir` — the error JSON contains relative paths that are resolved against this directory. CLI raises `ClickException` if `--static-export-dir` is not provided.
- **Requires** `[remote.storage]` in config — CLI raises `ClickException("--retry-failed requires [remote.storage] in config")` if the storage section is missing.
- Reads the error JSON file produced by a previous run.
- Extracts the list of failed file paths.
- Passes them as `only_files` to `push_static_files()`.
- Metadata push is skipped in retry mode (only static files are retried).

## Error Handling

- **Auth failure (`StorageAuthError`)**: From any `RemoteStorage` method → abort immediately with `ClickException`.
- **Single file failure**: Record in `failed_files`, continue with remaining files.
- **Network timeout**: Treated as failure, recorded.

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
- Deduplication: `exists()` returns True = skip.
- Idempotent push: safe to re-run without side effects.

## Module Structure (complete)

```
python/deepresearch_flow/
  storage/
    __init__.py
    base.py                # RemoteStorage protocol + StorageAuthError
    config.py              # StorageConfig dataclass
    webdav.py              # WebDAV implementation
    factory.py             # type → instance dispatcher
    tests/
      __init__.py
      test_webdav.py       # WebDAV tests with mocked transport
      test_factory.py      # Factory tests
  paper/snapshot/
    push.py                # Extend RemoteConfig + load_remote_config()
    push_static.py         # push_static_files() — uses RemoteStorage
    tests/
      test_push.py         # Extend with storage config tests
      test_push_static.py  # push_static_files() tests with fake storage
```

## Dependencies

- `httpx` — already in project. Used by WebDAV implementation for HTTP requests.
- No new dependencies.

## Testing Strategy

- Unit tests for `RemoteStorage` protocol compliance (WebDAV implementation with mocked httpx transport).
- Unit tests for factory dispatch (webdav type, unknown type).
- Unit tests for `StorageConfig` loading from TOML (valid, missing fields, env: prefix).
- Unit tests for `push_static_files()` with a **fake in-memory `RemoteStorage`** — tests are backend-agnostic.
- Unit tests for retry mode (`only_files` filtering).
- Unit tests for error JSON report writing/reading.
- CLI integration test via `click.testing.CliRunner`.

## Future (Out of Scope)

- S3 / R2 storage backend implementations (new files in `storage/`).
- Concurrent upload (parallel calls to `storage.upload()`).
- Spec 2 will add `[pipeline.storage]` in `config.toml` for server-side push, reusing the same `storage/` package and `push_static_files()`.
