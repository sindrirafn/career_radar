# React frontend migration

## Decision

Career Radar will adopt React and TypeScript for its user interface while
keeping Python and PostgreSQL as the trusted workflow engine. This is an
incremental migration, not a backend rewrite.

## Boundaries

```text
React PWA
  -> versioned local API
     -> existing Python workflow functions
        -> PostgreSQL and existing import sources
```

The API is a thin adapter over the existing query and mutation functions. It
does not duplicate ingestion, promotion, canonicalization, deduplication, or
application-status rules. The existing server-rendered dashboard remains
available while each React route reaches verified behavioural parity.

## Delivery order

1. Establish the design system, data-safety contract, API boundary, and React
   project foundation.
2. Build Today: application plan, deadlines, new-match review, and safe actions.
3. Build Applications: list, detail, status/follow-up/next-action editing.
4. Build Discover and Review, including staged-candidate and duplicate flows.
5. Build Insights and Profile.
6. Add PWA installation, theme persistence, offline app-shell behaviour, and
   deployment hardening after the core workflows are verified.

## Non-negotiable migration checks

- Use an isolated test database for mutation tests; never use the everyday
  database as a test fixture.
- Repeated UI requests must preserve the existing application decision rules.
- Importing the scheduled inbox must continue to use the current importer and
  its transaction boundary.
- The React **Check for updates** control calls that same importer through the
  versioned API; it never fetches, parses, or writes listing data in the
  browser.
- Existing server-rendered routes remain a rollback option until their React
replacement is tested.

During local React development only, the API can permit an explicit Vite origin
through `CAREER_RADAR_DEV_ORIGINS`. Leave it unset in production, where the
React build and API must be served from the same origin. This is an origin
allowlist, not a replacement for authentication.
- No client code receives database credentials or writes directly to PostgreSQL.
