## ADDED Requirements

### Requirement: PDF-only entries in db serve
The system SHALL index PDF files under `--pdf-root` that do not match an existing JSON paper record, creating PDF-only entries with a derived title.

#### Scenario: PDF-only entry appears in listing
- **WHEN** a PDF file is present without a matching paper JSON record
- **THEN** a PDF-only entry is shown in the papers list

### Requirement: PDF metadata title fallback
The system SHALL use the PDF metadata title when available, and fall back to filename-derived titles when metadata is missing or empty.

#### Scenario: Metadata title available
- **WHEN** a PDF has a non-empty metadata title
- **THEN** the PDF-only entry uses that title

#### Scenario: Metadata title missing
- **WHEN** a PDF has no usable metadata title
- **THEN** the system derives the title from the filename

### Requirement: PDF-only indicators and warnings
The system SHALL surface PDF-only status in the listing and show a warning in detail views when only the PDF is available.

#### Scenario: PDF-only entry badges
- **WHEN** a PDF-only paper is displayed in the list
- **THEN** the UI shows a PDF-only indicator

#### Scenario: PDF-only detail warning
- **WHEN** a PDF-only entry is opened in detail view
- **THEN** the UI shows a warning indicating only the PDF is available

### Requirement: PDF-only exclusion from stats
The system SHALL exclude PDF-only entries from db statistics and homepage stats counts.

#### Scenario: Stats ignore PDF-only entries
- **WHEN** statistics are calculated
- **THEN** PDF-only entries do not contribute to counts

### Requirement: Keyword statistics
The system SHALL compute keyword frequencies from paper keyword fields and show them in db serve stats.

#### Scenario: Keyword stats rendered
- **WHEN** the stats page is opened
- **THEN** a keyword chart is rendered using computed keyword frequencies
