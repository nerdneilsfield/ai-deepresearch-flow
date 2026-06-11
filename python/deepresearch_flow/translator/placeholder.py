"""Placeholder store for protected markdown segments."""

from __future__ import annotations

import json
import re
from typing import Dict, List


class PlaceHolderStore:
    _placeholder_like = re.compile(r"__PH_[A-Z0-9_]+?_\d+__")

    def __init__(self) -> None:
        self._map: dict[tuple[str, str], str] = {}
        self._rev: dict[str, str] = {}
        self._kind_count: dict[str, int] = {}
        self._source_placeholder_likes: set[str] = set()
        self.length = 0

    def add(self, kind: str, text: str) -> str:
        key = (kind, text)
        if key in self._map:
            return self._map[key]

        while True:
            self.length += 1
            length_str = str(self.length).zfill(6)
            placeholder = f"__PH_{kind}_{length_str}__"
            if placeholder not in self._rev and placeholder not in self._source_placeholder_likes:
                break

        self._map[key] = placeholder
        self._rev[placeholder] = text
        self._kind_count[kind] = self._kind_count.get(kind, 0) + 1
        return placeholder

    def save(self, file_path: str) -> None:
        payload = {
            "entries": [
                {"kind": kind, "text": text, "placeholder": placeholder}
                for (kind, text), placeholder in self._map.items()
            ],
            "rev": self._rev,
            "kind_count": self._kind_count,
            "source_placeholder_likes": sorted(self._source_placeholder_likes),
        }
        with open(file_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)

    def load(self, file_path: str) -> None:
        with open(file_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        entries = payload.get("entries")
        if isinstance(entries, list):
            self._map = {
                (str(entry.get("kind", "")), str(entry.get("text", ""))): str(
                    entry.get("placeholder", "")
                )
                for entry in entries
                if entry.get("placeholder")
            }
        else:
            legacy_map = payload.get("map", {})
            self._map = {}
            for text, placeholder in legacy_map.items():
                match = re.match(r"__PH_([A-Z0-9_]+?)_\d+__", str(placeholder))
                kind = match.group(1) if match else "LEGACY"
                self._map[(str(kind), str(text))] = str(placeholder)
        self._rev = payload.get("rev", {})
        self._kind_count = payload.get("kind_count", {})
        self._source_placeholder_likes = set(payload.get("source_placeholder_likes", []))
        self.length = len(self._map)

    def record_source_placeholder_like_tokens(self, text: str) -> None:
        self._source_placeholder_likes.update(self.find_placeholder_like_tokens(text))

    def source_placeholder_like_tokens(self) -> set[str]:
        return set(self._source_placeholder_likes)

    def _referenced_placeholders(self) -> set[str]:
        referenced: set[str] = set()
        for raw in self._rev.values():
            referenced.update(
                token for token in self.find_placeholder_like_tokens(raw) if token in self._rev
            )
        return referenced

    def root_placeholders(self) -> set[str]:
        return set(self._rev) - self._referenced_placeholders()

    def restore_all(self, text: str) -> str:
        max_rounds = max(len(self._rev), 1)
        for _ in range(max_rounds):
            visible = {
                token for token in self.find_placeholder_like_tokens(text) if token in self._rev
            }
            if not visible:
                break
            previous = text
            for placeholder in sorted(visible, key=len, reverse=True):
                raw = self._rev[placeholder]
                if raw.endswith("\n"):
                    text = text.replace(f"{placeholder}\n", raw)
                text = text.replace(placeholder, raw)
            if text == previous:
                break
        return text

    def find_placeholder_like_tokens(self, text: str) -> List[str]:
        return [match.group(0) for match in self._placeholder_like.finditer(text)]

    def find_unresolved_placeholder_tokens(
        self, text: str, *, ignore_source_literals: bool = True
    ) -> List[str]:
        known = set(self._rev)
        source_literals = self._source_placeholder_likes if ignore_source_literals else set()
        return [
            token
            for token in self.find_placeholder_like_tokens(text)
            if token not in known and token not in source_literals
        ]

    def has_unresolved_placeholder_tokens(
        self, text: str, *, ignore_source_literals: bool = True
    ) -> bool:
        return bool(
            self.find_unresolved_placeholder_tokens(
                text, ignore_source_literals=ignore_source_literals
            )
        )

    def restore_all_checked(self, text: str, *, ignore_source_literals: bool = True) -> str:
        missing = self.diff_missing(text)
        if missing:
            raise ValueError("placeholder token(s) missing before restore: " + ", ".join(missing))
        restored = self.restore_all(text)
        source_literals = self._source_placeholder_likes if ignore_source_literals else set()
        residual = [
            token
            for token in self.find_placeholder_like_tokens(restored)
            if token not in source_literals
        ]
        if residual:
            raise ValueError(
                "unresolved placeholder token(s) remain after restore: " + ", ".join(residual)
            )
        return restored

    def contains_all(self, text: str) -> bool:
        return all(placeholder in text for placeholder in self.root_placeholders())

    def diff_missing(self, text: str) -> List[str]:
        return [ph for ph in sorted(self.root_placeholders()) if ph not in text]

    def snapshot(self) -> Dict[str, str]:
        return {f"{kind}\n{text}": placeholder for (kind, text), placeholder in self._map.items()}

    def placeholders(self) -> set[str]:
        return set(self._rev)

    def kind_counts(self) -> Dict[str, int]:
        return dict(self._kind_count)
