"""Create and upload split 7z archives for static exports."""

from __future__ import annotations

import subprocess
import shlex
import logging
import time
from typing import Callable
from uuid import uuid4
from pathlib import Path

from deepresearch_flow.storage.base import RemoteStorage
import httpx
from tqdm import tqdm

from deepresearch_flow.paper.snapshot.push import (
    DEFAULT_PUSH_RETRIES,
    DEFAULT_PUSH_RETRY_BACKOFF_SECONDS,
    _should_retry_status,
)

logger = logging.getLogger(__name__)


def _with_cooldown(action: Callable[[], None], description: str) -> None:
    for attempt in range(DEFAULT_PUSH_RETRIES + 1):
        try:
            action()
            return
        except httpx.HTTPStatusError as exc:
            if not _should_retry_status(exc.response.status_code) or attempt == DEFAULT_PUSH_RETRIES:
                raise
        except httpx.TransportError:
            if attempt == DEFAULT_PUSH_RETRIES:
                raise
        logger.warning(
            "%s failed; cooling down %.0fs before retry %d/%d",
            description, DEFAULT_PUSH_RETRY_BACKOFF_SECONDS, attempt + 1, DEFAULT_PUSH_RETRIES,
        )
        time.sleep(DEFAULT_PUSH_RETRY_BACKOFF_SECONDS)


def _volume_size(size_mb: int) -> str:
    if size_mb < 1:
        raise ValueError("bundle size must be at least 1 MB")
    return f"{size_mb}m"


def _archive_script(volumes: list[str], overwrite: bool) -> bytes:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'TARGET="${1:?Usage: bash apply.sh /absolute/static/root}"',
        '[[ "$TARGET" = /* ]] || { echo "target must be absolute" >&2; exit 1; }',
        'cd -- "$(dirname -- "$0")"',
        'command -v 7z >/dev/null || { echo "7z is required" >&2; exit 1; }',
        "mkdir -p \"$TARGET\"",
    ]
    lines.append(f'7z x {shlex.quote(volumes[0])} -o"$TARGET" -y ' + ('-aoa' if overwrite else '-aos'))
    lines.extend([
        "rm -f -- " + " ".join(shlex.quote(name) for name in volumes),
        'echo "archives extracted and removed"',
    ])
    return ("\n".join(lines) + "\n").encode()


def push_static_bundles(
    static_export_dir: Path,
    storage: RemoteStorage,
    *,
    bundle_size_mb: int = 50,
    remote_dir: str = "bundles",
    local_root: Path | None = None,
    overwrite: bool = False,
) -> list[str]:
    """Pack static files with high compression and upload split 7z volumes."""
    static_export_dir = static_export_dir.resolve()
    files = sorted(p.relative_to(static_export_dir) for p in static_export_dir.rglob("*") if p.is_file())
    if not files:
        return []
    if any("\n" in str(path) or "\r" in str(path) for path in files):
        raise ValueError("7z file lists cannot contain newline characters in file names")
    volume = _volume_size(bundle_size_mb)
    if not remote_dir.strip("/") or any(p in {".", ".."} for p in remote_dir.split("/")):
        raise ValueError("bundle remote directory must be a relative storage directory")
    if remote_dir.startswith("/"):
        raise ValueError("bundle remote directory must be relative")
    batch_id = uuid4().hex
    remote_dir = remote_dir.rstrip("/") + "/" + batch_id
    if local_root is None:
        local_root = static_export_dir.parent / "bundles"
    local_dir = local_root.resolve() / batch_id
    local_dir.mkdir(parents=True, exist_ok=False)
    stem = "bundle.7z"
    archive = local_dir / stem
    file_list = local_dir / "files.txt"
    file_list.write_text("\n".join(str(path) for path in files) + "\n", encoding="utf-8")
    print(f"Compressing {len(files)} files → {local_dir}", flush=True)
    subprocess.run(
                [
                    "7z", "a", "-t7z", "-mx=9", "-m0=lzma2", "-ms=on", "-mmt=on",
                    f"-v{volume}", "-bb1", "-bsp1", "-scsUTF-8", "-spd", str(archive), f"@{file_list}",
                ],
            cwd=static_export_dir,
            check=True,
        )
    volumes = sorted(local_dir.glob(f"{stem}.*"))
    script = local_dir / "apply.sh"
    script.write_bytes(_archive_script([p.name for p in volumes], overwrite))
    script.chmod(0o755)
    from deepresearch_flow.paper.snapshot.push_static import _ensure_parents

    _with_cooldown(
        lambda: _ensure_parents(storage, f"{remote_dir}/apply.sh"),
        f"Create {remote_dir}",
    )
    # Publish the script last, once all volumes have been uploaded.
    uploads = [*volumes, script]
    for index, path in enumerate(uploads, 1):
        remote_path = f"{remote_dir}/{path.name}"
        with tqdm(
            total=path.stat().st_size, unit="B", unit_scale=True,
            desc=f"Upload [{index}/{len(uploads)}] {path.name}",
        ) as progress:
            def upload_current() -> None:
                progress.reset()
                storage.upload_file(remote_path, path, on_bytes=progress.update)

            _with_cooldown(upload_current, f"Upload {remote_path}")
            tqdm.write(f"Uploaded [{index}/{len(uploads)}] {remote_path}")
    return [f"{remote_dir}/apply.sh"]
