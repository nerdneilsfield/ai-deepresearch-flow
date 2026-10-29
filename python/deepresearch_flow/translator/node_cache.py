"""Per-document cache of successfully translated nodes, so an interrupted run can resume.

Keys are the node text with placeholders resolved: placeholder numbers are assigned per
document, so the same token can stand for different content after the source changes.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

CACHE_DIR_NAME = ".translate_cache"


class NodeCache:
    def __init__(
        self, path: Path, source_lang: str | None, target_lang: str, entries: dict[str, str]
    ) -> None:
        self.path = path
        self._lang = f"{source_lang or ''}\0{target_lang}\0"
        self._entries = entries

    @classmethod
    def for_output(
        cls, output_path: Path, source_lang: str | None, target_lang: str, *, load: bool
    ) -> NodeCache:
        path = output_path.parent / CACHE_DIR_NAME / f"{output_path.name}.json"
        entries: dict[str, str] = {}
        if load and path.exists():
            entries = json.loads(path.read_text(encoding="utf-8"))
        return cls(path, source_lang, target_lang, entries)

    def _key(self, origin_text: str) -> str:
        return hashlib.sha256((self._lang + origin_text).encode("utf-8")).hexdigest()

    def get(self, origin_text: str) -> str | None:
        return self._entries.get(self._key(origin_text))

    def put(self, origin_text: str, translated_text: str) -> None:
        self._entries[self._key(origin_text)] = translated_text

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self._entries, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            tmp.replace(self.path)
            dir_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            tmp.unlink(missing_ok=True)

    def discard(self) -> None:
        self.path.unlink(missing_ok=True)
        try:
            self.path.parent.rmdir()
        except OSError:
            pass  # other documents still have caches in this directory
