# Career Radar design system

**Status:** product direction and implementation guardrails

Career Radar is a personal job-search intelligence workspace. It should feel
quietly ambitious: editorial, capable, and human. It helps one person make good
career decisions without imitating an AI chatbot, a generic admin dashboard, or
a gamified productivity app.

## Product principles

1. **Today before everything.** The first screen answers: what needs my
   attention today? Deadlines, follow-ups, and strong new matches come before
   historical reporting.
2. **Decision support, not automation theatre.** Scores and AI evidence explain
   a recommendation; they never obscure the listing, replace human judgement,
   or automatically change a workflow decision.
3. **Dense, never crowded.** Career Radar has more information than Brain Train.
   Solve that with hierarchy, progressive disclosure, and a clear primary action
   rather than a wall of cards, badges, and controls.
4. **Trust is a visual feature.** Status, dates, source links, evidence, and
   saved state must be legible. Successful saves should be quiet but unmistakable.
5. **Phone for awareness; desktop for depth.** Phone prioritizes quick checks
   and decisive actions. Desktop provides a spacious workbench for comparing,
   preparing, and recording applications.

## Visual direction

Use an editorial operations aesthetic: warm neutral canvas, near-ink text,
purposeful accent colors, precise typography, and low-contrast borders instead
of default elevation. Shadows are reserved for overlays and temporary notices. Reuse
the restraint and craft of Brain Train as inspiration, not as a visual copy.

- No purple gradients, glowing AI effects, mascot language, confetti, or
  generic "copilot" patterns.
- Do not use card stacks merely as decoration. A surface represents a real
  actionable object, an editable form, or a bounded comparison.
- Establish semantic design tokens before screen-by-screen styling: color,
  typography, spacing, radii, elevation, motion, layout, and touch targets.
- Support light and dark themes. Status never relies on color alone.
- Use a high-quality system sans stack, fluid headings only where helpful, and
  readable line lengths. Let typography and whitespace create hierarchy.

## Information architecture

The React application will organize work into these destinations:

| Destination | Primary purpose |
|---|---|
| Today | Urgent follow-ups, application plan, and strongest unseen matches. |
| Discover | Browse, search, filter, and save curated opportunities. |
| Review | Decide what to do with staged candidates and duplicate pairs. |
| Applications | Prepare, track, and progress active applications. |
| Insights | Skill demand, gaps, category patterns, and research-only listings. |
| Profile | Skill profile and product preferences. |

High-frequency phone navigation will expose Today, Discover, Applications, and
one context-aware More/Review route. Desktop may use a compact left rail or a
top navigation layout, but must keep the current task and relevant actions
visible without filling the page with permanent panels.

## Responsive and interaction rules

- Start mobile-first. Test 320px, 375px, 768px, and 1280px widths, plus text
  scaling and 200% zoom.
- Every touch control has a 44px minimum target; primary actions are 48px.
- Use bottom sheets on phones for filters and bounded secondary choices. Use
  dialogs only for focused confirmation flows.
- Preserve context during job review: title, company, deadline, status, and the
  current decision must remain easy to recover.
- Use motion only to clarify state. Respect `prefers-reduced-motion`.
- Provide keyboard operation, visible focus, semantic controls, useful empty
  states, loading states, and offline/error states.

## Data-safety contract

The UI is a client of the existing Career Radar workflow; it is not a second
source of truth.

- PostgreSQL remains authoritative. Existing listing IDs, canonical URLs,
  application records, candidate review status, AI-enrichment history, skill
  profile, and duplicate decisions must remain intact.
- The existing import and promotion functions remain the only path that writes
  listing/candidate data. The new API must call those same domain functions,
  never reimplement their SQL in the frontend.
- No destructive schema migration, data reset, table recreation, or CSV
  re-import is part of the frontend migration.
- Every mutation stays transactional and preserves the current behaviour that
  prevents repeated imports or UI actions from overwriting later application
  decisions.
- Before replacing a legacy screen, verify equivalent read and write behaviour
  against an isolated PostgreSQL test database. Keep the legacy dashboard
  available until its replacement passes that parity check.

## Implementation checklist for visible work

Before calling a UI slice complete, verify its rendered mobile and desktop
layouts in light and dark themes; check hierarchy, overflow, touch ergonomics,
keyboard focus, contrast, loading/error/empty states, and reduced motion. Add
or update a focused test for every workflow mutation.

## Decision surfaces

Discover and Review are working queues, not read-only reports. Each role must
make the next useful choice clear without adding a separate workflow or status
taxonomy.

- Show the existing state in plain language: application list, future review,
  or not interested.
- For an undecided role, offer only the two immediate choices: **Interested**
  and **Not interested**. These map to the existing application workflow
  states; no additional candidate stage is created.
- Keep the external `Open listing` action quiet and visually consistent across
  all destinations.
- Scores are summaries, not explanations. A tap-safe `Fit details` disclosure
  must reveal fit tier, skill coverage, named gaps, and any available evidence.
- Dense job queues use structured rows with dividers rather than a uniform grid
  of elevated cards. This applies to Today’s strongest matches as well as
  Discover and Review.

## UI Design Specifications to avoid looking like generic AI build

Design for clarity, restraint, and intentionality. Avoid generic “AI dashboard” patterns: excessive gradients, oversized rounded cards, floating blobs, glass effects, and uniformly spaced tile layouts.

- Use a defined spacing scale: 4, 8, 12, 16, 24, 32, 48, and 64px only.
- Limit border radii: 6px for controls, 10–12px for cards/modals; avoid pill-shaped containers except tags and compact filters.
- Use one neutral background, one elevated surface color, one dark text color, and one purposeful accent color. Do not use gradients unless they communicate a real state or brand moment.
- Prefer thin, low-contrast borders (`1px`) over heavy shadows. Use shadows only for overlays, popovers, and raised modals.
- Create hierarchy through type scale, spacing, alignment, and contrast—not decorative effects.
- Set body copy at 14–16px with 1.45–1.6 line height. Use 12px only for secondary metadata; avoid tiny interface text.
- Use a restrained type scale, such as 12 / 14 / 16 / 20 / 28 / 40px. Reserve large display text for genuinely important moments.
- Establish a readable content width: 640–760px for prose, 960–1200px for application layouts.
- Align elements to a consistent grid. Avoid centering every section; use left-aligned content for dense product interfaces.
- Use cards only to group content with a clear shared purpose. Do not put every text block, statistic, or action inside its own card.
- Prefer tables, structured lists, and inline metadata for operational content instead of a wall of metric cards.
- Make primary actions visually singular: one filled primary button per view whenever possible. Secondary actions should be quieter (outline, text, or icon actions).
- Use labels alongside unfamiliar icons. Do not rely on icon-only navigation or emoji as product UI icons.
- Keep navigation stable and compact; clearly distinguish current location, page title, and available actions.
- Write real, specific interface copy. Replace generic labels such as “Unlock insights” or “Get started” with task-based language.
- Use empty states that explain what is missing, why it matters, and the next action—without illustrations unless they add useful meaning.
- Use color semantically and accessibly: reserve red for destructive/error states, amber for caution, green for success, and ensure text contrast meets WCAG AA.
- Add subtle interaction feedback: 150–200ms transitions, visible focus states, clear hover/pressed states, and no exaggerated bouncing or glowing effects.
- Use loading states that preserve layout and communicate progress; avoid generic spinners when a skeleton or inline status is clearer.
- Make responsive behavior intentional: collapse columns before shrinking text, preserve primary actions, and maintain comfortable 16–24px mobile page padding.
- Use realistic content density. Avoid large unused areas, excessive headline spacing, and perfectly symmetrical demo content.
- Include edge cases in every screen: long names, zero values, errors, disabled controls, loading, overflow, and permission-limited states.
- Build visual character from a small number of distinctive decisions—typeface, editorial spacing, custom icon style, or subtle brand color—not from stacked effects.
