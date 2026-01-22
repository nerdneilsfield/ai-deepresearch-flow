## ADDED Requirements
### Requirement: Extract range filtering
The system SHALL allow users to limit extraction to a subrange of the resolved input list using `--start-idx` (inclusive) and `--end-idx` (exclusive), where `--end-idx -1` means the last item. The range filter SHALL be applied before retry-failed filtering, and the system SHALL log the before/after counts, warning when the range yields zero files.

#### Scenario: Slice a directory input
- **WHEN** `paper extract` runs with `--start-idx 2 --end-idx 5`
- **THEN** only items 2, 3, and 4 of the resolved input list are processed

#### Scenario: End index to last item
- **WHEN** `paper extract` runs with `--end-idx -1`
- **THEN** extraction proceeds from `--start-idx` through the final resolved input item

#### Scenario: Retry filtering after slice
- **WHEN** `paper extract` runs with `--start-idx 0 --end-idx 100 --retry-failed`
- **THEN** the range is applied before retry-failed filtering is used to skip non-failed items

#### Scenario: Range yields no files
- **WHEN** `paper extract` runs with a range that selects no items
- **THEN** the system warns that the range yielded zero files
