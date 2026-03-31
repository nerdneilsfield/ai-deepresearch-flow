# BibTeX Import for Selection Page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to upload a `.bib` file on the selection page and automatically match entries against the paper database, adding matched papers to their selection list.

**Architecture:** New `POST /api/v1/papers/match-bibtex` endpoint receives raw BibTeX text, parses with pybtex, and runs a two-level matching pipeline (DOI exact → title fuzzy with uniqueness/year guards). Frontend splits `.bib` files into 50-entry batches, calls the API per batch, stages results, and commits to selection only after all batches complete (for Replace mode).

**Tech Stack:** Python/Starlette (backend), pybtex (BibTeX parsing), SQLite (paper DB), Vue 3 + TypeScript (frontend)

**Spec:** `docs/superpowers/specs/2026-04-01-bibtex-import-design.md`

**Reviewer note on "failed Z" in toast:** "failed Z" counts entries (= batch_size × failed_batch_count), not batch count. Each failed batch contributes its entry count to the failed total.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `python/deepresearch_flow/paper/snapshot/bibtex_match.py` | Create | Core matching logic: parse BibTeX, DOI match, title fuzzy match with uniqueness/year guards |
| `python/deepresearch_flow/paper/snapshot/api.py` | Modify | Register `POST /api/v1/papers/match-bibtex` route, call matching logic |
| `python/deepresearch_flow/paper/snapshot/tests/test_bibtex_match.py` | Create | Unit tests for matching logic |
| `python/deepresearch_flow/paper/snapshot/tests/test_api_match_bibtex.py` | Create | Integration tests for the API endpoint |
| `frontend/src/lib/api.ts` | Modify | Add `matchBibtex()` function and types |
| `frontend/src/views/SelectedView.vue` | Modify | Import BibTeX button, batch logic, unmatched panel, staging/commit flow |

---

### Task 1: Core Matching Logic — Unit Tests (RED)

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/bibtex_match.py` (stub)
- Create: `python/deepresearch_flow/paper/snapshot/tests/test_bibtex_match.py`

- [ ] **Step 1: Create stub module**

```python
# python/deepresearch_flow/paper/snapshot/bibtex_match.py
"""BibTeX matching against snapshot paper database."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class MatchedEntry:
    bibtex_key: str
    paper_id: str
    match_method: Literal["doi", "title"]
    title: str
    year: str | None
    venue: str | None
    authors: list[str]


@dataclass(frozen=True)
class UnmatchedEntry:
    bibtex_key: str
    title: str | None
    search_query: str


@dataclass(frozen=True)
class MatchResult:
    matched: list[MatchedEntry]
    unmatched: list[UnmatchedEntry]
```

- [ ] **Step 2: Write failing tests for matching logic**

```python
# python/deepresearch_flow/paper/snapshot/tests/test_bibtex_match.py
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
import unittest

from deepresearch_flow.paper.snapshot.bibtex_match import (
    MatchResult,
    match_bibtex_entries,
)
from deepresearch_flow.paper.snapshot.schema import init_snapshot_db


def _setup_db(tmpdir: Path) -> Path:
    """Create a snapshot DB with test papers."""
    db_path = tmpdir / "snapshot.db"
    conn = sqlite3.connect(str(db_path))
    try:
        init_snapshot_db(conn)
        conn.execute(
            "INSERT OR REPLACE INTO snapshot_meta(key, value) VALUES (?, ?)",
            ("snapshot_build_id", "build-test"),
        )
        # Paper 1: has DOI
        conn.execute(
            """
            INSERT INTO paper(
              paper_id, paper_key, paper_key_type, doi, title, year, month, publication_date,
              venue, preferred_summary_template, summary_preview, paper_index, source_hash,
              output_language, provider, model, prompt_template, extracted_at,
              pdf_content_hash, source_md_content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "paper-doi-1", "doi:10.1234/test.001", "doi", "10.1234/test.001",
                "Attention Is All You Need", "2017", "06", "2017-06-01",
                "NeurIPS", "deep_read", "preview", 1, "hash1",
                "en", "prov", "model", "tmpl", "2025-01-01T00:00:00Z", None, None,
            ),
        )
        # Paper 2: no DOI, unique title
        conn.execute(
            """
            INSERT INTO paper(
              paper_id, paper_key, paper_key_type, doi, title, year, month, publication_date,
              venue, preferred_summary_template, summary_preview, paper_index, source_hash,
              output_language, provider, model, prompt_template, extracted_at,
              pdf_content_hash, source_md_content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "paper-title-1", "arxiv:1234.5678", "arxiv", None,
                "BERT Pre-training of Deep Bidirectional Transformers", "2019", "05", "2019-05-01",
                "NAACL", "deep_read", "preview", 2, "hash2",
                "en", "prov", "model", "tmpl", "2025-01-01T00:00:00Z", None, None,
            ),
        )
        # Paper 3: similar title to paper 2 (for ambiguity test)
        conn.execute(
            """
            INSERT INTO paper(
              paper_id, paper_key, paper_key_type, doi, title, year, month, publication_date,
              venue, preferred_summary_template, summary_preview, paper_index, source_hash,
              output_language, provider, model, prompt_template, extracted_at,
              pdf_content_hash, source_md_content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "paper-title-2", "arxiv:1234.9999", "arxiv", None,
                "BERT Pre-training of Deep Bidirectional Transformers for NLU", "2019", "10", "2019-10-01",
                "EMNLP", "deep_read", "preview", 3, "hash3",
                "en", "prov", "model", "tmpl", "2025-01-01T00:00:00Z", None, None,
            ),
        )
        # Paper 4: same title as paper 1 but different year (for year cross-check)
        conn.execute(
            """
            INSERT INTO paper(
              paper_id, paper_key, paper_key_type, doi, title, year, month, publication_date,
              venue, preferred_summary_template, summary_preview, paper_index, source_hash,
              output_language, provider, model, prompt_template, extracted_at,
              pdf_content_hash, source_md_content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "paper-year-diff", "meta:key4", "meta", None,
                "Attention Is All You Need Revisited", "2023", "01", "2023-01-01",
                "ICLR", "deep_read", "preview", 4, "hash4",
                "en", "prov", "model", "tmpl", "2025-01-01T00:00:00Z", None, None,
            ),
        )
        # Authors for paper 1
        conn.execute("INSERT INTO author(author_id, value, paper_count) VALUES (1, 'Vaswani, Ashish', 1)")
        conn.execute("INSERT INTO author(author_id, value, paper_count) VALUES (2, 'Shazeer, Noam', 1)")
        conn.execute("INSERT INTO paper_author(paper_id, author_id) VALUES ('paper-doi-1', 1)")
        conn.execute("INSERT INTO paper_author(paper_id, author_id) VALUES ('paper-doi-1', 2)")
        # Authors for paper 2
        conn.execute("INSERT INTO author(author_id, value, paper_count) VALUES (3, 'Devlin, Jacob', 1)")
        conn.execute("INSERT INTO paper_author(paper_id, author_id) VALUES ('paper-title-1', 3)")
        conn.commit()
    finally:
        conn.close()
    return db_path


class TestMatchBibtexEntries(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.db_path = _setup_db(Path(cls.tmpdir.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmpdir.cleanup()

    def test_doi_exact_match(self) -> None:
        bib = '@article{vaswani2017, title={Attention Is All You Need}, doi={10.1234/test.001}}'
        result = match_bibtex_entries(bib, self.db_path)
        self.assertEqual(len(result.matched), 1)
        self.assertEqual(result.matched[0].paper_id, "paper-doi-1")
        self.assertEqual(result.matched[0].match_method, "doi")
        self.assertEqual(result.matched[0].bibtex_key, "vaswani2017")
        self.assertIn("Vaswani, Ashish", result.matched[0].authors)
        self.assertEqual(len(result.unmatched), 0)

    def test_doi_with_url_prefix(self) -> None:
        bib = '@article{key1, title={Test}, doi={https://doi.org/10.1234/test.001}}'
        result = match_bibtex_entries(bib, self.db_path)
        self.assertEqual(len(result.matched), 1)
        self.assertEqual(result.matched[0].paper_id, "paper-doi-1")

    def test_title_fuzzy_match_unique(self) -> None:
        bib = '@article{key1, title={Attention Is All You Need Revisited}, year={2023}}'
        result = match_bibtex_entries(bib, self.db_path)
        self.assertEqual(len(result.matched), 1)
        self.assertEqual(result.matched[0].paper_id, "paper-year-diff")
        self.assertEqual(result.matched[0].match_method, "title")

    def test_title_unique_match_with_large_gap(self) -> None:
        # paper-title-1 is exact match (score ~1.0), paper-title-2 has extra "for NLU" (score < 0.95)
        # Gap > 0.05, so paper-title-1 should match confidently
        bib = '@article{key1, title={BERT Pre-training of Deep Bidirectional Transformers}}'
        result = match_bibtex_entries(bib, self.db_path)
        self.assertEqual(len(result.matched), 1)
        self.assertEqual(result.matched[0].paper_id, "paper-title-1")

    def test_title_ambiguous_within_005_gap_returns_unmatched(self) -> None:
        # Query title is a midpoint between paper-title-1 and paper-title-2,
        # so both score similarly and gap < 0.05 → should be unmatched
        bib = '@article{key1, title={BERT Pre-training of Deep Bidirectional Transformers for}}'
        result = match_bibtex_entries(bib, self.db_path)
        # paper-title-1: "BERT Pre-training of Deep Bidirectional Transformers" vs query "...for"
        # paper-title-2: "BERT Pre-training of Deep Bidirectional Transformers for NLU" vs query "...for"
        # Both should score very close → ambiguous → unmatched
        self.assertEqual(len(result.matched), 0)
        self.assertEqual(len(result.unmatched), 1)

    def test_title_year_mismatch_returns_unmatched(self) -> None:
        # Title matches paper-doi-1 (2017) but bib says year=2020
        bib = '@article{key1, title={Attention Is All You Need}, year={2020}}'
        result = match_bibtex_entries(bib, self.db_path)
        # DOI not provided, title matches paper-doi-1 but year differs → unmatched
        self.assertEqual(len(result.matched), 0)
        self.assertEqual(len(result.unmatched), 1)

    def test_no_match(self) -> None:
        bib = '@article{key1, title={A Completely Unknown Paper Title}}'
        result = match_bibtex_entries(bib, self.db_path)
        self.assertEqual(len(result.matched), 0)
        self.assertEqual(len(result.unmatched), 1)
        self.assertEqual(result.unmatched[0].bibtex_key, "key1")
        self.assertEqual(result.unmatched[0].search_query, "A Completely Unknown Paper Title")

    def test_malformed_entry_goes_to_unmatched(self) -> None:
        bib = '@article{broken_entry, this is not valid bibtex at all'
        result = match_bibtex_entries(bib, self.db_path)
        # Should not crash, entry goes to unmatched
        self.assertEqual(len(result.matched), 0)
        self.assertEqual(len(result.unmatched), 1)

    def test_multiple_entries_mixed(self) -> None:
        bib = (
            '@article{key_doi, title={Attention Is All You Need}, doi={10.1234/test.001}}\n'
            '@article{key_title, title={Attention Is All You Need Revisited}, year={2023}}\n'
            '@article{key_miss, title={Nonexistent Paper}}\n'
        )
        result = match_bibtex_entries(bib, self.db_path)
        self.assertEqual(len(result.matched), 2)
        self.assertEqual(len(result.unmatched), 1)
        matched_ids = {m.paper_id for m in result.matched}
        self.assertIn("paper-doi-1", matched_ids)
        self.assertIn("paper-year-diff", matched_ids)

    def test_empty_bibtex(self) -> None:
        result = match_bibtex_entries("", self.db_path)
        self.assertEqual(len(result.matched), 0)
        self.assertEqual(len(result.unmatched), 0)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && python -m pytest python/deepresearch_flow/paper/snapshot/tests/test_bibtex_match.py -v`
Expected: FAIL — `ImportError: cannot import name 'match_bibtex_entries'`

---

### Task 2: Core Matching Logic — Implementation (GREEN)

**Files:**
- Modify: `python/deepresearch_flow/paper/snapshot/bibtex_match.py`

- [ ] **Step 1: Implement `match_bibtex_entries`**

```python
# python/deepresearch_flow/paper/snapshot/bibtex_match.py
"""BibTeX matching against snapshot paper database."""
from __future__ import annotations

import difflib
import io
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from deepresearch_flow.paper.snapshot.bibtex_utils import extract_doi_from_bibtex_raw
from deepresearch_flow.paper.snapshot.common import _column_exists, _open_ro_conn

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class MatchedEntry:
    bibtex_key: str
    paper_id: str
    match_method: Literal["doi", "title"]
    title: str
    year: str | None
    venue: str | None
    authors: list[str]


@dataclass(frozen=True)
class UnmatchedEntry:
    bibtex_key: str
    title: str | None
    search_query: str


@dataclass(frozen=True)
class MatchResult:
    matched: list[MatchedEntry]
    unmatched: list[UnmatchedEntry]


def _normalize_title(title: str) -> str:
    """Normalize title for fuzzy matching: lowercase, strip braces/punctuation, collapse whitespace."""
    value = title.replace("{", "").replace("}", "")
    value = _NORMALIZE_RE.sub(" ", value.lower())
    return _WHITESPACE_RE.sub(" ", value).strip()


def _title_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _fetch_authors(conn: sqlite3.Connection, paper_id: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT f.value
        FROM paper_author j
        JOIN author f ON f.author_id = j.author_id
        WHERE j.paper_id = ?
        ORDER BY f.value ASC
        """,
        (paper_id,),
    ).fetchall()
    return [str(r["value"]) for r in rows]


def _parse_bibtex_entries(raw: str) -> list[dict[str, Any]]:
    """Parse BibTeX text into a list of entry dicts using pybtex.

    Each dict has keys: key, type, title, year, doi_raw, fields.
    Entries that fail to parse are returned with title=None.
    """
    if not raw or not raw.strip():
        return []

    try:
        from pybtex.database.input.bibtex import Parser
    except ImportError:
        raise RuntimeError("pybtex is required for BibTeX matching")

    entries: list[dict[str, Any]] = []
    parser = Parser()

    # Split raw text into individual entry strings so we can parse each one
    # independently. This way a malformed entry only affects itself, not
    # valid siblings in the same batch.
    entry_starts: list[int] = []
    _ENTRY_RE = re.compile(r"@(?=\w+\s*\{)")
    for m in _ENTRY_RE.finditer(raw):
        entry_starts.append(m.start())

    if not entry_starts:
        return []

    raw_segments: list[str] = []
    for i, start in enumerate(entry_starts):
        end = entry_starts[i + 1] if i + 1 < len(entry_starts) else len(raw)
        raw_segments.append(raw[start:end])

    for segment in raw_segments:
        try:
            bib_data = parser.parse_stream(io.StringIO(segment))
            for key, entry in bib_data.entries.items():
                fields = dict(entry.fields)
                title = str(fields.get("title", "")).replace("{", "").replace("}", "").strip() or None
                year = str(fields.get("year", "")).strip() or None
                doi_raw = fields.get("doi")
                entries.append({
                    "key": key,
                    "type": entry.type,
                    "title": title,
                    "year": year,
                    "doi_raw": str(doi_raw).strip() if doi_raw else None,
                    "fields": fields,
                })
        except Exception:
            # Single entry failed to parse — extract key at minimum
            key_match = re.search(r"@\w+\s*\{([^,\s]+)", segment)
            entries.append({
                "key": key_match.group(1) if key_match else "unknown",
                "type": "unknown",
                "title": None,
                "year": None,
                "doi_raw": None,
                "fields": {},
            })

    return entries


def match_bibtex_entries(bibtex_raw: str, db_path: Path) -> MatchResult:
    """Match BibTeX entries against the snapshot paper database.

    Matching pipeline per entry (stop at first match):
    1. DOI exact match
    2. Title fuzzy match (SequenceMatcher ≥ 0.9, uniqueness gap ≥ 0.05, year cross-check)
    """
    entries = _parse_bibtex_entries(bibtex_raw)
    if not entries:
        return MatchResult(matched=[], unmatched=[])

    conn = _open_ro_conn(db_path)
    try:
        has_doi = _column_exists(conn, "paper", "doi")
        doi_select = "doi" if has_doi else "NULL AS doi"

        # Build title prefix index from all papers in DB
        all_papers = conn.execute(
            f"SELECT paper_id, title, year, venue, {doi_select} FROM paper"
        ).fetchall()

        paper_index: list[dict[str, Any]] = []
        by_prefix: dict[str, list[int]] = {}
        doi_lookup: dict[str, dict[str, Any]] = {}

        for row in all_papers:
            record = {
                "paper_id": str(row["paper_id"]),
                "title": str(row["title"] or ""),
                "year": str(row["year"]) if row["year"] else None,
                "venue": str(row["venue"]) if row["venue"] else None,
                "doi": str(row["doi"]) if row["doi"] else None,
            }
            title_norm = _normalize_title(record["title"])
            record["_title_norm"] = title_norm
            idx = len(paper_index)
            paper_index.append(record)

            if title_norm:
                prefix = title_norm[:16]
                by_prefix.setdefault(prefix, []).append(idx)

            if record["doi"]:
                doi_lookup[record["doi"]] = record

        matched: list[MatchedEntry] = []
        unmatched: list[UnmatchedEntry] = []

        for entry in entries:
            bib_key = entry["key"]
            bib_title = entry["title"]
            bib_year = entry["year"]
            search_query = bib_title or bib_key

            # Level 1: DOI exact match
            if entry["doi_raw"] and has_doi:
                from deepresearch_flow.paper.snapshot.identity import canonicalize_doi
                canonical = canonicalize_doi(entry["doi_raw"])
                if canonical and canonical in doi_lookup:
                    paper = doi_lookup[canonical]
                    authors = _fetch_authors(conn, paper["paper_id"])
                    matched.append(MatchedEntry(
                        bibtex_key=bib_key,
                        paper_id=paper["paper_id"],
                        match_method="doi",
                        title=paper["title"],
                        year=paper["year"],
                        venue=paper["venue"],
                        authors=authors,
                    ))
                    continue

            # Level 2: Title fuzzy match
            if bib_title:
                query_norm = _normalize_title(bib_title)
                if query_norm:
                    # Get candidates via prefix index
                    prefix = query_norm[:16]
                    candidate_indices = by_prefix.get(prefix, [])
                    if not candidate_indices:
                        # Fallback: scan all papers
                        candidate_indices = list(range(len(paper_index)))

                    # Score all candidates
                    scores: list[tuple[int, float]] = []
                    for ci in candidate_indices:
                        score = _title_similarity(query_norm, paper_index[ci]["_title_norm"])
                        if score >= 0.9:
                            scores.append((ci, score))

                    if scores:
                        scores.sort(key=lambda x: x[1], reverse=True)
                        best_idx, best_score = scores[0]

                        # Uniqueness check: best must lead second-best by >= 0.05
                        is_unique = len(scores) == 1 or (best_score - scores[1][1]) >= 0.05

                        if is_unique:
                            paper = paper_index[best_idx]
                            # Year cross-check
                            year_ok = True
                            if bib_year and paper["year"] and bib_year != paper["year"]:
                                year_ok = False

                            if year_ok:
                                authors = _fetch_authors(conn, paper["paper_id"])
                                matched.append(MatchedEntry(
                                    bibtex_key=bib_key,
                                    paper_id=paper["paper_id"],
                                    match_method="title",
                                    title=paper["title"],
                                    year=paper["year"],
                                    venue=paper["venue"],
                                    authors=authors,
                                ))
                                continue

            # No match
            unmatched.append(UnmatchedEntry(
                bibtex_key=bib_key,
                title=bib_title,
                search_query=search_query,
            ))

        return MatchResult(matched=matched, unmatched=unmatched)
    finally:
        conn.close()
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && python -m pytest python/deepresearch_flow/paper/snapshot/tests/test_bibtex_match.py -v`
Expected: All 11 tests PASS

- [ ] **Step 3: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/bibtex_match.py python/deepresearch_flow/paper/snapshot/tests/test_bibtex_match.py
git commit -m "feat(snapshot): add BibTeX matching logic with DOI and title fuzzy match"
```

---

### Task 3: API Endpoint — Tests (RED)

**Files:**
- Create: `python/deepresearch_flow/paper/snapshot/tests/test_api_match_bibtex.py`

- [ ] **Step 1: Write failing API tests**

```python
# python/deepresearch_flow/paper/snapshot/tests/test_api_match_bibtex.py
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
import unittest

from starlette.testclient import TestClient

from deepresearch_flow.paper.snapshot.api import create_app
from deepresearch_flow.paper.snapshot.common import ApiLimits
from deepresearch_flow.paper.snapshot.schema import init_snapshot_db


class TestApiMatchBibtex(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory()
        root = Path(cls.tmpdir.name)
        cls.db_path = root / "snapshot.db"

        conn = sqlite3.connect(str(cls.db_path))
        try:
            init_snapshot_db(conn)
            conn.execute(
                "INSERT OR REPLACE INTO snapshot_meta(key, value) VALUES (?, ?)",
                ("snapshot_build_id", "build-test"),
            )
            conn.execute(
                """
                INSERT INTO paper(
                  paper_id, paper_key, paper_key_type, doi, title, year, month, publication_date,
                  venue, preferred_summary_template, summary_preview, paper_index, source_hash,
                  output_language, provider, model, prompt_template, extracted_at,
                  pdf_content_hash, source_md_content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "paper-1", "doi:10.1234/test", "doi", "10.1234/test",
                    "Graph Neural Networks for NLP", "2023", "01", "2023-01-01",
                    "ACL", "deep_read", "preview", 1, "hash1",
                    "en", "prov", "model", "tmpl", "2025-01-01T00:00:00Z", None, None,
                ),
            )
            conn.execute("INSERT INTO author(author_id, value, paper_count) VALUES (1, 'Smith, Alice', 1)")
            conn.execute("INSERT INTO paper_author(paper_id, author_id) VALUES ('paper-1', 1)")
            conn.commit()
        finally:
            conn.close()

        app = create_app(
            snapshot_db=cls.db_path,
            static_base_url="http://localhost/static",
            limits=ApiLimits(),
        )
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmpdir.cleanup()

    def test_match_by_doi(self) -> None:
        resp = self.client.post(
            "/api/v1/papers/match-bibtex",
            json={"bibtex_raw": '@article{key1, title={Test}, doi={10.1234/test}}'},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["matched"]), 1)
        self.assertEqual(data["matched"][0]["paper_id"], "paper-1")
        self.assertEqual(data["matched"][0]["match_method"], "doi")
        self.assertEqual(data["matched"][0]["authors"], ["Smith, Alice"])
        self.assertEqual(data["stats"]["total"], 1)
        self.assertEqual(data["stats"]["matched"], 1)
        self.assertEqual(data["stats"]["unmatched"], 0)

    def test_unmatched_entry(self) -> None:
        resp = self.client.post(
            "/api/v1/papers/match-bibtex",
            json={"bibtex_raw": '@article{key1, title={Nonexistent Paper}}'},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["matched"]), 0)
        self.assertEqual(len(data["unmatched"]), 1)
        self.assertEqual(data["unmatched"][0]["search_query"], "Nonexistent Paper")

    def test_missing_bibtex_raw_returns_400(self) -> None:
        resp = self.client.post(
            "/api/v1/papers/match-bibtex",
            json={},
        )
        self.assertEqual(resp.status_code, 400)

    def test_empty_bibtex_returns_empty(self) -> None:
        resp = self.client.post(
            "/api/v1/papers/match-bibtex",
            json={"bibtex_raw": ""},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["stats"]["total"], 0)

    def test_response_includes_year_venue_authors(self) -> None:
        resp = self.client.post(
            "/api/v1/papers/match-bibtex",
            json={"bibtex_raw": '@article{key1, title={Test}, doi={10.1234/test}}'},
        )
        m = resp.json()["matched"][0]
        self.assertEqual(m["year"], "2023")
        self.assertEqual(m["venue"], "ACL")
        self.assertIsInstance(m["authors"], list)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && python -m pytest python/deepresearch_flow/paper/snapshot/tests/test_api_match_bibtex.py -v`
Expected: FAIL — 405 Method Not Allowed (route doesn't exist yet)

---

### Task 4: API Endpoint — Implementation (GREEN)

**Files:**
- Modify: `python/deepresearch_flow/paper/snapshot/api.py` (add route + handler at ~line 480, register at ~line 975)

- [ ] **Step 1: Add endpoint handler in `api.py`**

Insert after `_api_paper_bibtex` (line ~480):

```python
async def _api_match_bibtex(request: Request) -> Response:
    cfg: SnapshotApiConfig = request.app.state.cfg
    try:
        body = await request.json()
    except Exception:
        return _json_error(400, error="invalid_json", detail="request body must be valid JSON")

    bibtex_raw = body.get("bibtex_raw")
    if bibtex_raw is None:
        return _json_error(400, error="missing_field", detail="bibtex_raw is required")
    if not isinstance(bibtex_raw, str):
        return _json_error(400, error="invalid_field", detail="bibtex_raw must be a string")

    from deepresearch_flow.paper.snapshot.bibtex_match import match_bibtex_entries
    result = match_bibtex_entries(bibtex_raw, cfg.snapshot_db)

    return JSONResponse({
        "matched": [
            {
                "bibtex_key": m.bibtex_key,
                "paper_id": m.paper_id,
                "match_method": m.match_method,
                "title": m.title,
                "year": m.year,
                "venue": m.venue,
                "authors": m.authors,
            }
            for m in result.matched
        ],
        "unmatched": [
            {
                "bibtex_key": u.bibtex_key,
                "title": u.title,
                "search_query": u.search_query,
            }
            for u in result.unmatched
        ],
        "stats": {
            "total": len(result.matched) + len(result.unmatched),
            "matched": len(result.matched),
            "unmatched": len(result.unmatched),
        },
    })
```

- [ ] **Step 2: Register route**

In the `routes = [...]` list (~line 975), add after the bibtex GET route:

```python
Route("/api/v1/papers/match-bibtex", _api_match_bibtex, methods=["POST"]),
```

- [ ] **Step 3: Run API tests to verify they pass**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && python -m pytest python/deepresearch_flow/paper/snapshot/tests/test_api_match_bibtex.py -v`
Expected: All 5 tests PASS

- [ ] **Step 4: Run all existing tests to verify no regressions**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && python -m pytest python/deepresearch_flow/paper/snapshot/tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/api.py python/deepresearch_flow/paper/snapshot/tests/test_api_match_bibtex.py
git commit -m "feat(snapshot): add POST /papers/match-bibtex API endpoint"
```

---

### Task 5: Frontend API Client

**Files:**
- Modify: `frontend/src/lib/api.ts` (add `matchBibtex` function at end)

- [ ] **Step 1: Add types and function to `api.ts`**

Add before the final `export type { ... }` block:

```typescript
export interface BibtexMatchedItem {
  bibtex_key: string
  paper_id: string
  match_method: 'doi' | 'title'
  title: string
  year: string | null
  venue: string | null
  authors: string[]
}

export interface BibtexUnmatchedItem {
  bibtex_key: string
  title: string | null
  search_query: string
}

export interface BibtexMatchResult {
  matched: BibtexMatchedItem[]
  unmatched: BibtexUnmatchedItem[]
  stats: { total: number; matched: number; unmatched: number }
}

export async function matchBibtex(bibtexRaw: string): Promise<BibtexMatchResult> {
  const url = buildUrl('/papers/match-bibtex')
  const data = await fetchJson(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ bibtex_raw: bibtexRaw }),
  })
  return data as BibtexMatchResult
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow/frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(frontend): add matchBibtex API client function"
```

---

### Task 6: Frontend — BibTeX Import Logic in SelectedView

**Files:**
- Modify: `frontend/src/views/SelectedView.vue`

- [ ] **Step 1: Add imports and state variables**

Add to the `<script setup>` imports (line 7):

```typescript
import { matchBibtex } from '@/lib/api'
import type { BibtexMatchedItem, BibtexUnmatchedItem } from '@/lib/api'
```

Add new icon import (line 14):

```typescript
import { Download, Upload, Save, Trash2, FileDown, FileUp } from 'lucide-vue-next'
```

Add state variables after existing refs (~line 23):

```typescript
const bibFileInput = ref<HTMLInputElement | null>(null)
const bibImporting = ref(false)
const bibProgress = ref(0)
const bibStatus = ref('')
const bibUnmatched = ref<BibtexUnmatchedItem[]>([])
const bibShowUnmatched = ref(true)
const bibMode = ref<'append' | 'replace'>('append')
const bibShowModePopover = ref(false)
```

- [ ] **Step 2: Add the BibTeX import functions**

Add after `handleFileLoad` function (~line 274):

```typescript
const BIB_BATCH_SIZE = 50
const BIB_ENTRY_RE = /@(?=\w+\s*\{)/g

function splitBibEntries(text: string): string[] {
  const positions: number[] = []
  let match: RegExpExecArray | null
  BIB_ENTRY_RE.lastIndex = 0
  while ((match = BIB_ENTRY_RE.exec(text)) !== null) {
    positions.push(match.index)
  }
  if (positions.length === 0) return []
  const entries: string[] = []
  for (let i = 0; i < positions.length; i++) {
    const start = positions[i]!
    const end = i + 1 < positions.length ? positions[i + 1]! : text.length
    entries.push(text.slice(start, end))
  }
  return entries
}

function triggerBibImport(mode: 'append' | 'replace') {
  bibMode.value = mode
  bibShowModePopover.value = false
  bibFileInput.value?.click()
}

async function handleBibFileLoad(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return

  bibImporting.value = true
  bibProgress.value = 0
  bibStatus.value = 'Reading file...'
  bibUnmatched.value = []
  bibShowUnmatched.value = true

  try {
    const text = await file.text()
    if (!text.trim()) {
      ui.pushToast('BibTeX file is empty', 'error')
      return
    }

    const entries = splitBibEntries(text)
    if (entries.length === 0) {
      ui.pushToast('No BibTeX entries found in file', 'error')
      return
    }

    // Chunk into batches
    const batches: string[] = []
    for (let i = 0; i < entries.length; i += BIB_BATCH_SIZE) {
      batches.push(entries.slice(i, i + BIB_BATCH_SIZE).join('\n'))
    }

    const stagedMatched: BibtexMatchedItem[] = []
    const stagedUnmatched: BibtexUnmatchedItem[] = []
    let failedEntryCount = 0
    let allBatchesOk = true

    // INVARIANT: Replace mode only clears existing selection if ALL batches succeed.
    // If any batch fails (network/server error), we degrade to Append mode to
    // prevent data loss. This is the critical safety contract — see spec section
    // "Frontend Logic" step 7 and Task 7 in the implementation plan.

    for (let i = 0; i < batches.length; i++) {
      bibStatus.value = `Matching ${Math.min((i + 1) * BIB_BATCH_SIZE, entries.length)}/${entries.length}...`
      bibProgress.value = Math.round(((i + 1) / batches.length) * 100)

      try {
        const result = await matchBibtex(batches[i]!)
        stagedMatched.push(...result.matched)
        stagedUnmatched.push(...result.unmatched)
      } catch (err) {
        console.error(`Batch ${i + 1} failed:`, err)
        allBatchesOk = false
        // Count entries in failed batch
        const batchEntryCount = splitBibEntries(batches[i]!).length
        failedEntryCount += batchEntryCount || BIB_BATCH_SIZE
      }
    }

    // Commit staged results — store methods are async (IndexedDB), must await
    if (bibMode.value === 'replace' && allBatchesOk) {
      await selection.clear()
    }

    for (const m of stagedMatched) {
      await selection.add({
        paper_id: m.paper_id,
        title: m.title,
        year: m.year ?? '',
        venue: m.venue ?? '',
        authors: m.authors,
      } as any) // SearchItem optional fields will be undefined
    }

    bibUnmatched.value = stagedUnmatched

    // Build toast message
    const parts: string[] = [`Matched ${stagedMatched.length}`]
    if (stagedUnmatched.length > 0) parts.push(`not found ${stagedUnmatched.length}`)
    if (failedEntryCount > 0) parts.push(`failed ${failedEntryCount}`)
    const toastType = failedEntryCount > 0 ? 'warning' as const : 'success' as const
    ui.pushToast(parts.join(', '), toastType)

    if (!allBatchesOk && bibMode.value === 'replace') {
      ui.pushToast('Replace cancelled due to batch failures — items appended instead', 'warning')
    }

    bibStatus.value = 'Done'
  } catch (err) {
    console.error(err)
    ui.pushToast('Failed to import BibTeX', 'error')
  } finally {
    bibImporting.value = false
    if (bibFileInput.value) bibFileInput.value.value = ''
  }
}
```

- [ ] **Step 3: Add template elements**

Add hidden file input after the existing one (line ~295):

```html
<input
  ref="bibFileInput"
  type="file"
  accept=".bib"
  class="hidden"
  @change="handleBibFileLoad"
/>
```

Add the Import BibTeX button group after the "Load List" button (line ~301):

```html
<div class="relative">
  <Button variant="outline" size="sm" @click="bibShowModePopover = !bibShowModePopover">
    <FileUp class="mr-2 h-4 w-4" /> {{ t('importBibtex') || 'Import BibTeX' }}
  </Button>
  <div
    v-if="bibShowModePopover"
    class="absolute right-0 top-full z-10 mt-1 w-40 rounded-md border border-ink-200 bg-white p-1 shadow-lg"
  >
    <button
      class="w-full rounded px-3 py-1.5 text-left text-sm hover:bg-ink-100"
      @click="triggerBibImport('append')"
    >
      Append
    </button>
    <button
      class="w-full rounded px-3 py-1.5 text-left text-sm hover:bg-ink-100"
      @click="triggerBibImport('replace')"
    >
      Replace
    </button>
  </div>
</div>
```

Add BibTeX progress bar after the existing download progress bar (line ~323):

```html
<div v-if="bibImporting" class="rounded-xl border border-blue-100 bg-blue-50 p-4">
  <div class="space-y-2">
    <div class="flex justify-between text-sm text-blue-700">
      <span>{{ bibStatus }}</span>
      <span>{{ bibProgress }}%</span>
    </div>
    <Progress :model-value="bibProgress" class="h-2" />
  </div>
</div>
```

Add unmatched panel before the paper list (before `<div v-if="selection.count === 0" ...>`):

```html
<div
  v-if="bibUnmatched.length > 0"
  class="rounded-xl border border-amber-200 bg-amber-50 p-4"
>
  <button
    class="flex w-full items-center justify-between text-sm font-medium text-amber-800"
    @click="bibShowUnmatched = !bibShowUnmatched"
  >
    <span>⚠ {{ bibUnmatched.length }} papers not found in database</span>
    <span class="text-xs">{{ bibShowUnmatched ? '▲' : '▼' }}</span>
  </button>
  <ul v-if="bibShowUnmatched" class="mt-2 space-y-1">
    <li
      v-for="item in bibUnmatched"
      :key="item.bibtex_key"
      class="flex items-center justify-between text-sm text-amber-700"
    >
      <span class="truncate">"{{ item.title || item.bibtex_key }}"</span>
      <a
        :href="`/?q=${encodeURIComponent(item.search_query)}`"
        target="_blank"
        class="ml-2 shrink-0 text-xs text-blue-600 hover:underline"
      >
        Search →
      </a>
    </li>
  </ul>
</div>
```

- [ ] **Step 4: Verify TypeScript compiles**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow/frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/SelectedView.vue
git commit -m "feat(frontend): add BibTeX import with batch matching and unmatched panel"
```

---

### Task 7: Backend Resilience Tests + Replace Safety Verification

Two backend tests: (a) malformed BibTeX returns 200 + unmatched (not 500), (b) mixed valid+malformed entries in same batch — valid entries still match. Plus a manual verification checklist for the frontend Replace-degradation contract (automated component test is impractical here since it requires simulating a network failure mid-batch in a Vue component).

**Files:**
- Modify: `python/deepresearch_flow/paper/snapshot/tests/test_api_match_bibtex.py`

- [ ] **Step 1: Add backend test for malformed entry resilience**

Add to `TestApiMatchBibtex` in `test_api_match_bibtex.py`:

```python
def test_malformed_bibtex_returns_unmatched_not_500(self) -> None:
    """Malformed entries should appear as unmatched, not crash the endpoint.

    This is critical for the frontend Replace-mode degradation logic:
    if a batch returns 200 with unmatched entries, it counts as 'succeeded'.
    Only network/500 errors trigger the 'failed batch' path that prevents Replace.
    """
    resp = self.client.post(
        "/api/v1/papers/match-bibtex",
        json={"bibtex_raw": "@article{broken, this is not valid bibtex"},
    )
    self.assertEqual(resp.status_code, 200)
    data = resp.json()
    self.assertEqual(data["stats"]["matched"], 0)
    self.assertGreaterEqual(data["stats"]["unmatched"], 1)
```

- [ ] **Step 2: Add backend test for mixed valid+malformed entries in same batch**

Add to `TestApiMatchBibtex`:

```python
def test_mixed_valid_and_malformed_in_same_batch(self) -> None:
    """A malformed entry must not prevent valid siblings from matching.

    This verifies per-entry parsing resilience: the valid entry should still
    match via DOI, while the malformed one goes to unmatched.
    """
    bib = (
        '@article{good_entry, title={Graph Neural Networks for NLP}, doi={10.1234/test}}\n'
        '@article{bad_entry, this is broken bibtex with no closing brace\n'
    )
    resp = self.client.post(
        "/api/v1/papers/match-bibtex",
        json={"bibtex_raw": bib},
    )
    self.assertEqual(resp.status_code, 200)
    data = resp.json()
    self.assertEqual(data["stats"]["matched"], 1)
    self.assertEqual(data["matched"][0]["paper_id"], "paper-1")
    self.assertGreaterEqual(data["stats"]["unmatched"], 1)
```

- [ ] **Step 3: Document and manually verify frontend staging logic**

The frontend Replace-degradation logic lives in `handleBibFileLoad` in SelectedView.vue.
Since this is a critical data-safety invariant, add a focused comment block at the top
of `handleBibFileLoad` documenting the contract, and verify the behavior manually:

**Manual verification checklist** (to be run after Task 8 integration):
1. Start dev server, add 3 papers to selection manually
2. Prepare a `.bib` file with 60 entries (2 batches), where batch 2 will fail (e.g., disconnect network after batch 1)
3. Click "Import BibTeX" → Replace mode
4. Verify: original 3 papers are still in selection + batch 1 matched papers are appended
5. Verify: toast shows "Replace cancelled due to batch failures — items appended instead"

Add this contract comment to `handleBibFileLoad` in Task 6 code (after the staging variable declarations):

```typescript
    // INVARIANT: Replace mode only clears existing selection if ALL batches succeed.
    // If any batch fails (network/server error), we degrade to Append mode to
    // prevent data loss. This is the critical safety contract — see spec section
    // "Frontend Logic" step 7 and Task 7 in the implementation plan.
```

- [ ] **Step 4: Run backend tests**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && python -m pytest python/deepresearch_flow/paper/snapshot/tests/test_api_match_bibtex.py -v`
Expected: All tests PASS (including the two new ones)

- [ ] **Step 5: Commit**

```bash
git add python/deepresearch_flow/paper/snapshot/tests/test_api_match_bibtex.py
git commit -m "test(snapshot): add Replace degradation and per-entry resilience tests"
```

---

### Task 8: Final Integration Verification

- [ ] **Step 1: Run all backend tests**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && python -m pytest python/deepresearch_flow/paper/snapshot/tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Run frontend type check**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow/frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Run frontend dev server smoke test**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow/frontend && npx vite build`
Expected: Build succeeds without errors
