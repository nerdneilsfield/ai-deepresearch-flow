## 1. Backend
- [x] 1.1 Add translated markdown discovery based on source markdown path and `.LANG.md` suffixes.
- [x] 1.2 Expose available translation languages per paper in the db serve data model/view context.
- [x] 1.3 Implement translated markdown loading with the same safety rules as Source rendering.
- [x] 1.4 Include translation availability in list filtering and stats counts.

## 2. Frontend
- [x] 2.1 Add Translated tab alongside Summary/Source/PDF in the detail view.
- [x] 2.2 Add a language dropdown to select the translation (default to `zh` when available).
- [x] 2.3 Reuse Source view outline panel + back-to-top control for Translated.
- [x] 2.4 Provide an empty-state message when no translation is available.
- [x] 2.5 Add translation availability to filters, badges, and stats pills.

## 3. Documentation
- [x] 3.1 Update README with the Translated view and language selection behavior.

## 4. Validation
- [x] 4.1 Run `openspec validate add-db-serve-translated-view --strict` and address any findings.
