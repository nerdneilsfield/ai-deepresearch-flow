"""Create and upload split 7z archives for static exports."""

from __future__ import annotations

import subprocess
import shlex
from uuid import uuid4
from pathlib import Path

from deepresearch_flow.storage.base import RemoteStorage


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
    subprocess.run(
                [
                    "7z", "a", "-t7z", "-mx=9", "-m0=lzma2", "-ms=on", "-mmt=on",
                    f"-v{volume}", "-scsUTF-8", "-spd", str(archive), f"@{file_list}",
                ],
            cwd=static_export_dir,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    volumes = sorted(local_dir.glob(f"{stem}.*"))
    script = local_dir / "apply.sh"
    script.write_bytes(_archive_script([p.name for p in volumes], overwrite))
    script.chmod(0o755)
    from deepresearch_flow.paper.snapshot.push_static import _ensure_parents

    _ensure_parents(storage, f"{remote_dir}/apply.sh")
    for volume_path in volumes:
        storage.upload(f"{remote_dir}/{volume_path.name}", volume_path.read_bytes())
    # Publish the script last, once all volumes have been uploaded.
    storage.upload(f"{remote_dir}/apply.sh", script.read_bytes())
    return [f"{remote_dir}/apply.sh"]
