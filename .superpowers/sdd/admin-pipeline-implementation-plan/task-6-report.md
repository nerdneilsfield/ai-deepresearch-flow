# Task 6 report — Admin upload, batch, and review frontend

Status: DONE_WITH_CONCERNS

Base commit: `2a1cee5 feat(frontend): add admin pipeline workflow`
Follow-up commits: `fix(frontend): harden admin pipeline lifecycle`; re-review fix is this commit

Implemented:

- Session-only Admin token lifecycle and config validation in `stores/admin-pipeline.ts`.
- Authenticated Task5 DTO client in `lib/admin-pipeline.ts` for config, upload, batch/job actions, BibTeX binding, artifacts, and notifications.
- Admin upload view with multi-PDF/optional BibTeX inputs, allowlisted model defaults, count/size/type validation, upload errors, recent batches, and worker banner.
- Batch view with status/progress/BibTeX diagnostics, polling only while active, worker offline/degraded banner, revision-aware partial batch publication, cancellation, and explicit notification permission.
- Job review view with authenticated protected PDF Blob URL, read-only source/summary/translation previews, URL revocation, BibTeX candidate/no-Bib binding, model-aware retry, reject, publish using current revision, and stale-409 handling.
- Adversarial hardening: review-ready transition notifications with dedupe, real error-only publish-409 refresh, strict filename extensions with accessible validation, route-param generation fencing, auth-loss redirect/poll shutdown, and protected preview URL cleanup across failures, route changes, and concurrent loads.
- Re-review hardening: stale publish/indexing conflicts revoke and reload all protected previews before actions re-enable; empty PDF selection has a live, input-associated error; auth validation operations are generation-fenced against response reordering.
- Routes and conditional desktop/mobile Admin navigation; public routes remain unchanged.
- Black-box tests for API auth/upload/artifacts, token lifecycle/defaults/aggregate/empty-selection validation, preview cleanup, polling termination/offline, partial publish, BibTeX correction/no-Bib, stale publish/indexing retry with preview reload, retry/reject, notification permission, and auth response reordering.

Verification:

- Focused admin/frontend tests: 22 passed across API, auth store, upload, batch/job workflow, and protected preview suites.
- Full frontend tests: 206 passed; 2 existing `mockPaperServer.test.mjs` tests fail in sandbox because loopback bind to `127.0.0.1` returns `EPERM`.
- `npm run build` passed (`vue-tsc` plus Vite production build).
- `git diff --check` passed.

Concerns:

- Full suite's two loopback tests need a host/network-permitted run for final confirmation; failure is environmental (`listen EPERM`), not frontend assertion failure.
- Focused suite covers review-ready notification dedupe, route reuse, auth-loss polling stop/redirect, error-only 409 refresh, extension-only upload errors, and Blob URL lifecycle fencing.
- Re-review suite covers stale conflict preview revocation/reload, indexing retry conflict recovery, empty PDF live error association, and newest-auth-operation wins.
- UI uses English strings directly; public i18n behavior is untouched.
