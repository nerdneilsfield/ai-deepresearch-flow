"""Placeholder store for protected markdown segments."""

from __future__ import annotations

import json
import re
from typing import Dict, List


class PlaceHolderStore:
    _placeholder_like = re.compile(r"__PH_[A-Z0-9_]+__")

    def __init__(self) -> None:
        self._map: dict[str, str] = {}
        self._rev: dict[str, str] = {}
        self._kind_count: dict[str, int] = {}
        self._source_placeholder_likes: set[str] = set()
        self.length = 0

    def add(self, kind: str, text: str) -> str:
        if text in self._map:
            return self._map[text]

        self.length += 1
        length_str = str(self.length).zfill(6)
        placeholder = f"__PH_{kind}_{length_str}__"
        self._map[text] = placeholder
        self._rev[placeholder] = text
        self._kind_count[kind] = self._kind_count.get(kind, 0) + 1
        return placeholder

    def save(self, file_path: str) -> None:
        payload = {
            "map": self._map,
            "rev": self._rev,
            "kind_count": self._kind_count,
            "source_placeholder_likes": sorted(self._source_placeholder_likes),
        }
        with open(file_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)

    def load(self, file_path: str) -> None:
        with open(file_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self._map = payload.get("map", {})
        self._rev = payload.get("rev", {})
        self._kind_count = payload.get("kind_count", {})
        self._source_placeholder_likes = set(payload.get("source_placeholder_likes", []))
        self.length = len(self._map)

    def record_source_placeholder_like_tokens(self, text: str) -> None:
        self._source_placeholder_likes.update(self.find_placeholder_like_tokens(text))

    def source_placeholder_like_tokens(self) -> set[str]:
        return set(self._source_placeholder_likes)

    def restore_all(self, text: str) -> str:
        for placeholder, raw in sorted(self._rev.items(), key=lambda item: -len(item[0])):
            if raw.endswith("\n"):
                text = text.replace(f"{placeholder}\n", raw)
            text = text.replace(placeholder, raw)
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
        restored = self.restore_all(text)
        unresolved = self.find_unresolved_placeholder_tokens(
            restored, ignore_source_literals=ignore_source_literals
        )
        if unresolved:
            raise ValueError(
                "unresolved placeholder token(s) remain after restore: "
                + ", ".join(unresolved)
            )
        return restored

    def contains_all(self, text: str) -> bool:
        return all(placeholder in text for placeholder in self._map.values())

    def diff_missing(self, text: str) -> List[str]:
        return [ph for ph in self._map.values() if ph not in text]

    def snapshot(self) -> Dict[str, str]:
        return dict(self._map)

    def kind_counts(self) -> Dict[str, int]:
        return dict(self._kind_count)
