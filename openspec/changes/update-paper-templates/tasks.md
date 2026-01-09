## 1. Schemas
- [x] 1.1 Require publication_date and publication_venue in all built-in schemas.
- [x] 1.2 Update simple schema to require abstract, keywords, and summary.
- [x] 1.3 Update eight_questions schema to eight questions (question1..question8).
- [x] 1.4 Update deep_read schema to include module_c8 and 8-question semantics.

## 2. Prompts
- [x] 2.1 Update simple prompt to produce a single summary covering all eight question aspects.
- [x] 2.2 Update eight_questions prompt to eight questions and question1..question8 field names.
- [x] 2.3 Update deep_read prompt modules to eight questions.

## 3. Multi-stage extraction
- [x] 3.1 Define stage groups for eight_questions (questions_1to4, questions_5to8).
- [x] 3.2 Update stage validation to accept per-stage field lists and new required metadata fields.

## 4. Render templates
- [x] 4.1 Localize headings for zh/en in default, eight_questions, deep_read, and three_pass templates.
- [x] 4.2 Update eight_questions/deep_read render headings for the new questions/modules.

## 5. Documentation
- [x] 5.1 Update README to reflect the eight-question workflow, summary template, and required fields.
