## ADDED Requirements

### Requirement: Translated view tab
The system SHALL provide a Translated view in the db serve detail page alongside Summary, Source, and PDF.

#### Scenario: Translated tab is visible
- **WHEN** a user opens a paper detail page
- **THEN** a Translated tab is available in the view selector

### Requirement: Translation discovery by suffix
The system SHALL discover translated markdown files by suffixing the source markdown filename with `.<lang>.md`
under the configured `--md-translated-root` directories.
The language identifier SHALL be derived from the suffix segment.

#### Scenario: Discover translation languages
- **WHEN** the source markdown is `paper.md` and the translated root contains `paper.zh.md` and `paper.ja.md`
- **THEN** available translation languages include `zh` and `ja`

### Requirement: Translation language selection
The system SHALL provide a dropdown to choose among available translation languages.
The default selection SHALL be `zh` when available, otherwise the first available language in sorted order.

#### Scenario: Default to zh
- **WHEN** `zh` is available among translations
- **THEN** the Translated view loads the `zh` translation by default

#### Scenario: Fallback to first language
- **WHEN** `zh` is not available but `ja` and `de` are present
- **THEN** the Translated view defaults to `de` (sorted order)

### Requirement: Source-style rendering
The Translated view SHALL reuse the same markdown renderer and safety rules as the Source view.
It SHALL include the outline panel and back-to-top control used by Source.

#### Scenario: Outline is available in Translated view
- **WHEN** a user opens the Translated view
- **THEN** the outline panel and back-to-top control are visible

### Requirement: Empty state when missing
If no translated markdown files are available, the system SHALL show an empty-state message in the Translated view and disable the language dropdown.

#### Scenario: No translations available
- **WHEN** a paper has no translated markdown files
- **THEN** the Translated view shows an empty-state message and no language is selectable

### Requirement: Translation availability in filters and stats
The system SHALL expose translation availability in the db serve list filters and stats counts.

#### Scenario: Filter by translation availability
- **WHEN** a user selects the Translated filter as “With”
- **THEN** only papers with translated markdown are shown

#### Scenario: Stats include translated count
- **WHEN** the db serve list renders stats
- **THEN** the counts include a Translated value alongside Summary/Source/PDF
