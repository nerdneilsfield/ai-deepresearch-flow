# Fix PaddleOCR Markdown Output — Design Spec

**Date:** 2026-03-30
**Status:** Approved

## Problem

`recognize fix` (`fix_markdown()` in `translator/fixers.py`) only handles MinerU OCR output patterns. PaddleOCR produces markdown with distinct artifacts that are not addressed, causing broken formulas, misidentified references, and rendering issues.

## Design Constraints

- **Idempotent**: All rules must be safe to run multiple times and on both MinerU and PaddleOCR outputs.
- **Additive**: New rules are appended to the existing pipeline; no changes to existing processors.
- **Local regex**: No LLM calls; all fixes are deterministic string transformations.
- **Order matters**: PaddleOCR-specific cleanup runs **before** existing processors (e.g., `ReferenceProcessor`) so that unwrapped references like `$ [^2] $` → `[^2]` can then be processed by the existing pipeline.

## New Rules

Applied in this order, before the existing `merge_paragraphs` / `ReferenceProcessor` / etc.

### 1. `fix_nested_mailto(text) -> str`

**Problem:** PaddleOCR produces nested mailto tags like `<mailto:<mailto:<mailto:foo@bar>>>`.

**Rule:** Collapse to a single `<mailto:addr>`.

```
<mailto:<mailto:<mailto:tobi@ini.uzh.ch>>> → <mailto:tobi@ini.uzh.ch>
```

Regex: `<mailto:(?:<mailto:)+([^<>]+?)>+` → `<mailto:\1>`

### 2. `fix_non_math_in_delimiters(text) -> str`

**Problem:** PaddleOCR wraps non-math content in `$ ... $` delimiters:
- References: `$ [^2] [^36] [^22] $`
- Punctuation/words: `$. $`, `$, where $`

**Rule:** Strip `$ ... $` around content that contains no LaTeX commands (`\`, `^`, `_`, `{`, `}`).

Specifically, if the content between `$...$`:
- Contains only references (`[^N]`), commas, spaces → unwrap
- Contains only punctuation and/or plain words (no backslash, caret, underscore, braces) → unwrap
- Is empty or whitespace-only → remove entirely

This does NOT touch inline math that has any LaTeX-like syntax.

### 3. `fix_math_delimiter_spaces(text) -> str`

**Problem:** PaddleOCR inserts extra spaces inside `$ ... $`: `$ ^{1} $`, `$ \mu $s`.

**Rule:** For inline math `$ ... $` (not `$$ ... $$`), trim leading/trailing whitespace inside the delimiters.

```
$ ^{1} $   → $^{1}$
$ \mu $    → $\mu$
$ \times $ → $\times$
```

Must skip:
- Display math `$$ ... $$`
- Content inside fenced code blocks
- Content inside HTML `<code>` tags

### 4. `fix_html_table_math_spaces(text) -> str`

**Problem:** Same spacing issue occurs inside HTML `<td>` tags: `<td>Input  $ W \times H $</td>`.

**Rule:** Apply the same delimiter space trimming within HTML table cells. This is separate because HTML content is structured differently and needs tag-aware matching.

```
<td>Input  $ W \times H $</td> → <td>Input $W \times H$</td>
```

Also normalizes double spaces around formulas within `<td>` to single spaces.

## Integration Point

In `fix_markdown()` (`translator/fixers.py`), the new rules are called before the existing processors:

```python
def fix_markdown(text: str, level: str) -> str:
    if level == "off":
        return text

    # --- NEW: PaddleOCR-compatible cleanup (idempotent, safe for all OCR) ---
    text = fix_nested_mailto(text)
    text = fix_non_math_in_delimiters(text)
    text = fix_math_delimiter_spaces(text)
    text = fix_html_table_math_spaces(text)

    # --- Existing processors ---
    ref_processor = ReferenceProcessor()
    ...
```

## Scope

- **In scope:** The 4 rules above, added to `translator/fixers.py`.
- **Out of scope:** LLM-based formula repair (already handled by `fix-math`), translation artifacts, image handling.

## Testing

Unit tests for each rule with:
- PaddleOCR-specific inputs (from `test-data/EventCamera/ocr_out/`)
- MinerU inputs (should pass through unchanged)
- Edge cases: nested delimiters, empty formulas, code blocks, display math
