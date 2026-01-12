"""Placeholder store for protected markdown segments."""

from __future__ import annotations

import json
from typing import Dict, List


class PlaceHolderStore:
    def __init__(self) -> None:
        self._map: dict[str, str] = {}
        self._rev: dict[str, str] = {}
        self._kind_count: dict[str, int] = {}
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
        }
        with open(file_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)

    def load(self, file_path: str) -> None:
        with open(file_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self._map = payload.get("map", {})
        self._rev = payload.get("rev", {})
        self._kind_count = payload.get("kind_count", {})
        self.length = len(self._map)

    def restore_all(self, text: str) -> str:
        for placeholder, raw in sorted(self._rev.items(), key=lambda item: -len(item[0])):
            if raw.endswith("\n"):
                text = text.replace(f"{placeholder}\n", raw)
            text = text.replace(placeholder, raw)
        return text

    def contains_all(self, text: str) -> bool:
        return all(placeholder in text for placeholder in self._map.values())

    def diff_missing(self, text: str) -> List[str]:
        return [ph for ph in self._map.values() if ph not in text]

    def snapshot(self) -> Dict[str, str]:
        return dict(self._map)

    def kind_counts(self) -> Dict[str, int]:
        return dict(self._kind_count)
