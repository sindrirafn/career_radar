# Release-readiness audit — 2026-09-10

Scope: all tracked Python modules, SQL setup/analysis files, scripts, checked-in
CSV inputs, and public documentation. The repository had no automated tests.

## Changes

- Added shared workflow transactions, including nested savepoints and serialized
  participating writers. Inbox persistence, candidate promotion, and dashboard
  batch actions roll back together on failure.
- Preserved promoted-candidate links during curated duplicate merges and kept
  existing application decisions when historical imports or dashboard actions
  are repeated. Rejected candidates remain rejected during historical sync.
- Added a safe ISO calendar-date parser used by reporting, staging, duplicate
  review, and the archive. Raw text and existing table layouts remain intact.
- Validated CSV widths/headers and AI result fields before persistence. Explicit
  local inbox files no longer also import a configured remote Sheet. Duplicate
  canonical URLs consistently keep the first supplied occurrence.
- Corrected category seed URLs and preserved assignment provenance; aligned
  Power BI gap reporting with excluded profile skills; removed research-only
  and not-applying records from due-action reports.
- Added dashboard host/origin and form-size checks, safe imported hyperlinks,
  local redirects, and a consistent New Matches count.
- Updated workflow documentation and retained the still-supported Alfreð CLI
  adapter. No scraping path was proven fully superseded.

## Verification

31 standard-library tests passed with Python 3.13.6 and PostgreSQL 18.3:
validation, local HTTP handling, fresh/repeated setup, repeat imports, rollback
under injected failures, concurrent review actions, stable listing IDs,
application/profile preservation, duplicate references, and rendering all six
dashboard pages. The sample pipeline processes 69 rows into 59 unique listings.
Compilation also passed. No live scraping or paid AI API calls were made.

The existing database was inspected read-only: 270 curated listings, 361
candidates, 453 enrichment records, 229 application records, 15 profile skills,
and 32 duplicate decisions; no promoted candidate lacked its target link.
An additional full-snapshot upgrade check could not complete because pg_dump
failed. Its temporary database and partial dump were removed. Therefore upgrade
verification covers synthetic isolated schemas, not a restored production copy.

## Before use and remaining limits

Back up the existing database and run `PYTHONPATH=src .venv/bin/python -m
career_radar.cli setup` from the repository root before running the updated app.
This audit changes source files only; it does not migrate the existing database.

- This is still a local single-user app without authentication or production
  hosting hardening. Keep it on loopback.
- Setup recreates reporting views, so custom view dependencies/grants need
  explicit upgrade planning. Very old duplicate canonical URLs can prevent a
  unique index from being created; no destructive automatic cleanup was added.
- Duplicate decisions are pairwise; larger groups and contradictory decisions
  remain follow-up work. CLI candidate lists do not apply the dashboard's
  duplicate-visibility filter.
- Listing activity is deadline-based. Missing/invalid deadlines remain treated
  as active; upstream closed/expired status is preserved in notes, not a typed
  lifecycle field. Review before relying on these as live vacancies.
- `refresh` reloads sample CSVs and can overwrite overlapping curated source
  fields. Use the inbox import for daily updates. Clearing optional application
  fields and persistent removal of seeded profile skills still need design work.
- Dependency ranges remain unpinned; live source/API compatibility was not
  verified. No schema redesign, scoring rewrite, or data deletion was attempted.
