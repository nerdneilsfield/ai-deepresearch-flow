## ADDED Requirements
### Requirement: Merge BibTeX inputs
The system SHALL provide a `paper db merge bibtex` command that merges multiple BibTeX files into one output file.

#### Scenario: Merge multiple BibTeX files
- **WHEN** the user runs `paper db merge bibtex` with multiple `--input` `.bib` files and an `--output` path
- **THEN** the command writes a single merged `.bib` file containing all entries

#### Scenario: Handle duplicate BibTeX keys
- **WHEN** multiple input files contain the same BibTeX entry key
- **THEN** the command keeps the entry with the most fields and reports the duplicate keys in the merge summary

#### Scenario: Duplicate field-count tie
- **WHEN** duplicate BibTeX entries have the same number of fields
- **THEN** the command keeps the first occurrence in input order and reports the duplicate keys in the merge summary
