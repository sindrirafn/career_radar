# Data Dictionary

This document explains the current Career Radar database schema in plain English.

The project uses a normalized storage model plus reporting views:

```text
candidate staging -> curated raw storage tables -> normalized skill/category/application tables -> analytical views
```

## Relationship Map

```text
collected_job_candidates
  |-- collected_job_candidates_ranked_v1
  |-- candidate_ai_enrichments
      |-- candidate_ai_enrichments_latest_v1
      |-- collected_job_candidates_ai_v1
  |-- optional promotion into raw_job_listings
  |-- candidate_duplicate_reviews (pairs of source candidates)

raw_job_listings
  |-- job_listing_skills -- skills
                         |-- profile_skills
  |-- job_listing_categories -- job_categories
  |-- job_applications
  |-- job_listings_v1
  |-- job_listings_enriched_v1
  |-- possible_duplicate_job_listings_v1
  |-- job_skill_match_v1
  |-- skill_gap_summary_v1
  |-- powerbi_jobs_v1
  |-- powerbi_applications_v1
  |-- powerbi_skill_demand_v1
  |-- powerbi_skill_gaps_v1
  |-- powerbi_category_trends_v1
```

Important idea:

`collected_job_candidates` is the broad staging table for automated collection. `candidate_ai_enrichments` stores structured AI judgement about staged candidates. `raw_job_listings` is the central curated listing table. Skills, categories, and applications all attach to curated listings through `raw_job_listings.id`.

The scheduled ChatGPT task/Sheet export enters through the same staging layer.
It can arrive as a local CSV file or a live CSV URL. Most rows await human
promotion. Reconciliation rows marked `APPLIED.` or `SKIPPED.` are promoted
through the shared ingestion layer and get a historical application row if one
does not already exist; rejected candidates are left alone.

## Tables

### `candidate_duplicate_reviews`

Stores a decision for an ordered pair of candidate IDs (`first_candidate_id` <
`second_candidate_id`). Both source rows remain stored. `decision` is
`auto_merged`, `confirmed_duplicate`, or `not_duplicate`; a duplicate decision
names one of the pair as `canonical_candidate_id`, while `not_duplicate` leaves
that field null. `evidence` and `decided_at` explain the decision. The dashboard
hides the other candidate from its normal review lists.

Automatic matches require equal company/title plus a shared valid deadline,
a shared valid posting date and nonempty location, or identical substantive
text. Existing human decisions are not overwritten by repeat imports. This
pairwise model does not yet represent a canonical group of three or more rows.

### `collected_job_candidates`

Purpose:

Stores jobs collected automatically before they are allowed into the curated analytics dataset.

Design intent:

Automated collection should optimize for recall. That means it will collect some irrelevant jobs. This table protects the main job-market analysis from that noise by staging collected listings with rule-based relevance signals first.

Key columns:

| Column | Type | Meaning |
|---|---:|---|
| `id` | `bigint` | Internal primary key for each collected candidate. |
| `source` | `text` | Where the candidate came from, such as Alfreð. |
| `title` | `text` | Job title from the source listing. |
| `company` | `text` | Employer/company name. |
| `url` | `text` | Original listing URL. |
| `canonical_url` | `text` | Cleaned URL used to update the same candidate across repeated collection runs. |
| `category_guess` | `text` | Source-provided or task-provided category hint before normalized categories are assigned. |
| `description` | `text` | Listing text collected from the source. |
| `rule_relevance_score` | `integer` | First-pass high-recall relevance score from the rule filter. |
| `rule_relevance_label` | `text` | Rule label: `strong_candidate`, `possible_candidate`, `likely_irrelevant`, or `hard_negative`. |
| `matched_tracks` | `text[]` | Which broad tracks matched: `software_it`, `engineering`, or `transferable`. |
| `matched_keywords` | `text[]` | Keywords that caused the rule filter to notice the listing. |
| `hard_negative_flags` | `text[]` | Signals such as high experience or possible mandatory license. |
| `needs_ai_review` | `boolean` | Whether this candidate should later be sent to an AI classifier. |
| `review_status` | `text` | Human workflow status: `unreviewed`, `approved`, `rejected`, or `promoted`. |
| `promoted_job_listing_id` | `bigint` | If promoted, the matching `raw_job_listings.id`. |
| `first_seen_at` | `timestamptz` | When the system first collected this candidate. |
| `last_seen_at` | `timestamptz` | When the system most recently saw this candidate. |

Important constraints/indexes:

- Primary key: `id`
- Unique candidate identity: `canonical_url`
- Relevance index: `rule_relevance_label`, `rule_relevance_score`, `review_status`

Interview explanation:

> I keep automatically collected jobs in a staging table before promoting them to the curated listing table. The collector is optimized for recall, while the staging/relevance layer protects downstream analytics from irrelevant jobs.

### `candidate_ai_enrichments`

Purpose:

Stores structured AI review results for staged candidates.

Design intent:

The rule filter catches plausible jobs with broad keyword logic. AI enrichment handles the softer judgement: whether the role is actually relevant to the target career direction, which skills are explicit, which skills are inferred, what gaps matter, and why the candidate should or should not be reviewed.

This table stores enrichment history instead of only the latest result. That matters because prompts and models will improve over time. The scheduled-task importer skips unchanged repeat results, so a growing Sheet can be re-imported without creating a new AI-evidence row for every old listing every day.

AI enrichment can come from the API or from a manual ChatGPT workflow. The manual path can be single-candidate or bulk. All paths save the same structured result shape into this table.

When a candidate is promoted, the latest AI `explicit_skills` are merged into the promoted listing's normal skill text. Conservative AI-supported categories can also be attached through `job_listing_categories` with `source = 'model'`. Inferred skills stay in this table as evidence because they are not directly stated requirements.

Key columns:

| Column | Type | Meaning |
|---|---:|---|
| `id` | `bigint` | Internal primary key for each AI enrichment run. |
| `candidate_id` | `bigint` | References `collected_job_candidates.id`. |
| `provider` | `text` | Enrichment source, such as `openai` or `chatgpt_manual`. |
| `model` | `text` | Model used for the enrichment run. |
| `prompt_version` | `text` | Version label for the prompt/rules used. |
| `ai_relevance_label` | `text` | Controlled label: `target`, `adjacent`, `weak`, or `exclude`. |
| `ai_fit_tier` | `text` | Controlled fit tier: `strong`, `reasonable`, `stretch`, `weak`, or `exclude`. |
| `ai_fit_score` | `integer` | 0-100 current-fit score. |
| `ai_priority_score` | `integer` | 0-100 review-priority score. |
| `tracks` | `text[]` | Strategic tracks the AI assigned. |
| `explicit_skills` | `text[]` | Skills directly stated in the listing. |
| `inferred_skills` | `jsonb` | Skills inferred from tasks, with confidence and evidence. |
| `skill_gaps` | `text[]` | Important requirements or gaps to consider. |
| `positive_signals` | `text[]` | Evidence that supports review/promotion. |
| `concerns` | `text[]` | Evidence that weakens the fit. |
| `evidence` | `text` | Short explanation grounded in the listing text. |
| `candidate_snapshot` | `jsonb` | Exact candidate data shown to the model. |
| `raw_response` | `jsonb` | Stored response payload and response metadata. |
| `created_at` | `timestamptz` | When the enrichment result was created. |

Important constraints/indexes:

- Foreign key: `candidate_id -> collected_job_candidates.id`
- Score checks: `ai_fit_score` and `ai_priority_score` must be 0-100
- Controlled-label checks for relevance and fit tier
- Latest-result index: `candidate_id`, `created_at desc`, `id desc`

Interview explanation:

> I use AI enrichment after rule-based staging, not before. The AI returns strict structured fields, and I store each run in a history table so model and prompt changes are auditable.

### `raw_job_listings`

Purpose:

Stores the job listings imported from the raw CSV.

Design intent:

This table preserves messy source fields. Cleaned fields are exposed through views instead of overwriting the original imported values.

When multiple CSV files are imported, this table also records which raw file and source row most recently supplied each listing.

Key columns:

| Column | Type | Meaning |
|---|---:|---|
| `id` | `bigint` | Internal primary key for each listing. |
| `title` | `text` | Job title from the source listing. |
| `company` | `text` | Employer/company name. |
| `source` | `text` | Where the listing came from. |
| `url` | `text` | Original listing URL. |
| `canonical_url` | `text` | Cleaned URL used as the stable import identity. Tracking parameters, `www.`, trailing slashes, and Alfred locale prefixes are normalized where relevant. |
| `location` | `text` | Listed job location. |
| `posted_date_raw` | `text` | Raw posted date text, if known. |
| `deadline_raw` | `text` | Raw deadline text, including non-date values like `No stated deadline`. |
| `category_guess` | `text` | Original broad human-readable category guess. |
| `description` | `text` | Short listing description. |
| `requirements` | `text` | Short requirements summary. |
| `skills_mentioned` | `text` | Original semicolon-separated skill text from the CSV. |
| `notes` | `text` | Notes from collection/review. |
| `source_file` | `text` | Raw CSV file that most recently supplied this listing. |
| `source_row_number` | `integer` | Row number in that source CSV, using spreadsheet-style numbering where the header is row 1. |
| `imported_at` | `timestamptz` | When this row was originally imported. |
| `first_seen_at` | `timestamptz` | When this listing was first observed by the system. |
| `last_seen_at` | `timestamptz` | When this listing was most recently seen/refreshed. |

Important constraints/indexes:

- Primary key: `id`
- Unique listing identity: `canonical_url`

Why this matters:

`canonical_url` lets the import script update existing rows instead of deleting and recreating them. That keeps application tracking stable.

The normalizer intentionally started small and became more specific when real duplicate patterns appeared. For example, Alfred listings can appear as `/uk/starf/...`, `/en/starf/...`, or `/starf/...` while still representing the same listing.

Interview explanation:

> `raw_job_listings` is my source-preserving landing table. I keep raw date and category text there for auditability, then expose cleaned values through views.

### `skills`

Purpose:

Stores the canonical list of skills observed across job listings.

Key columns:

| Column | Type | Meaning |
|---|---:|---|
| `id` | `bigint` | Internal primary key for each skill. |
| `name` | `text` | Human-readable display name. |
| `normalized_name` | `text` | Lowercase/comparison form used for grouping and deduplication. |
| `created_at` | `timestamptz` | When the skill row was created. |

Important constraints/indexes:

- Primary key: `id`
- Unique: `normalized_name`

Why this matters:

One normalized skill row can be linked to many job listings.

Interview explanation:

> I normalized skills because skill demand is one of the main analytical outputs. This lets me count recurring skill mentions consistently across jobs.

### `job_listing_skills`

Purpose:

Bridge table connecting job listings to skills.

Why it exists:

A job can mention many skills, and one skill can appear in many jobs. That is a many-to-many relationship.

Key columns:

| Column | Type | Meaning |
|---|---:|---|
| `job_listing_id` | `bigint` | References `raw_job_listings.id`. |
| `skill_id` | `bigint` | References `skills.id`. |
| `source_text` | `text` | Skill text as it appeared when linked. |
| `created_at` | `timestamptz` | When the link was created. |

Important constraints/indexes:

- Primary key: `(job_listing_id, skill_id)`
- Foreign key: `job_listing_id -> raw_job_listings.id`
- Foreign key: `skill_id -> skills.id`

Interview explanation:

> `job_listing_skills` is the bridge between listings and skills. It avoids duplicating skill names on every listing and supports queries like “which jobs mention automation?” or “which skills occur most often?”

### `profile_skills`

Purpose:

Stores your current relationship to skills in the shared `skills` table.

Why it exists:

The job-market side tells us what employers ask for. `profile_skills` tells us which of those skills you currently have, are learning, or can strongly defend. That makes skill-gap analysis possible.

Key columns:

| Column | Type | Meaning |
|---|---:|---|
| `skill_id` | `bigint` | References `skills.id`; also the primary key. |
| `proficiency_level` | `text` | Current level: `learning`, `working`, or `strong`. |
| `evidence` | `text` | Short explanation for why the skill is in your profile. |
| `include_in_gap_analysis` | `boolean` | Whether this skill should count in gap/match views. |
| `created_at` | `timestamptz` | When the profile row was created. |
| `updated_at` | `timestamptz` | When the profile row was last updated. |

Important design detail:

`skills` is now a shared skill catalog, not only a list of skills currently seen in job listings. The import cleanup keeps profile-only skills so your profile is not damaged when the current job dataset changes.

Operational commands:

```text
python -m career_radar.cli profile-skill list
python -m career_radar.cli profile-skill set --skill "Testing" --level learning --evidence "Practicing through project work."
python -m career_radar.cli profile-skill remove --skill "Testing"
```

Interview explanation:

> I added a profile-skill table so the system can compare employer demand against a declared skill profile. The levels are deliberately simple: learning, working, and strong.

### `job_categories`

Purpose:

Stores the controlled category vocabulary used for career-track analysis.

Examples:

- `engineering`
- `biomedical`
- `data`
- `it_support`
- `infrastructure`
- `automation`

Key columns:

| Column | Type | Meaning |
|---|---:|---|
| `id` | `bigint` | Internal primary key for each category. |
| `name` | `text` | Human-readable display name. |
| `normalized_name` | `text` | Stable machine-friendly category name. |
| `description` | `text` | Explanation of what the category means. |
| `created_at` | `timestamptz` | When the category row was created. |

Important constraints/indexes:

- Primary key: `id`
- Unique: `normalized_name`

Interview explanation:

> I kept categories in a controlled vocabulary so analysis and scoring do not depend on messy free-text category labels.

### `job_listing_categories`

Purpose:

Bridge table connecting job listings to cleaned categories.

Why it exists:

A job can belong to more than one category. For example, a role can be both `engineering` and `automation`.

Key columns:

| Column | Type | Meaning |
|---|---:|---|
| `job_listing_id` | `bigint` | References `raw_job_listings.id`. |
| `category_id` | `bigint` | References `job_categories.id`. |
| `source` | `text` | How the category assignment was made: `manual`, `seed`, `rule`, or `model`. |
| `assigned_at` | `timestamptz` | When the category link was created. |

Important constraints/indexes:

- Primary key: `(job_listing_id, category_id)`
- Foreign key: `job_listing_id -> raw_job_listings.id`
- Foreign key: `category_id -> job_categories.id`
- `source` check constraint: `manual`, `seed`, `rule`, or `model`

Interview explanation:

> I separated raw category guesses from cleaned categories. The bridge table lets one listing belong to multiple strategic career categories.

### `job_applications`

Purpose:

Tracks your workflow for jobs you are interested in or applying to.

Key columns:

| Column | Type | Meaning |
|---|---:|---|
| `id` | `bigint` | Internal primary key for the application workflow row. |
| `job_listing_id` | `bigint` | References `raw_job_listings.id`. |
| `status` | `text` | Current application status. |
| `applied_on` | `date` | Date the application was submitted, if submitted. |
| `application_url` | `text` | URL used for the application, if separate from listing URL. |
| `cv_version` | `text` | CV version used or planned. |
| `cover_letter_version` | `text` | Cover letter version used or planned. |
| `follow_up_date` | `date` | Date to follow up. |
| `next_action` | `text` | Next practical action. |
| `notes` | `text` | Application notes. |
| `created_at` | `timestamptz` | When this workflow row was created. |
| `updated_at` | `timestamptz` | When this workflow row was last updated. |

Allowed statuses:

- `interested`
- `preparing`
- `applied`
- `interviewing`
- `rejected`
- `tracking_only`
- `not_applying`
- `withdrawn`
- `offer`

`tracking_only` means the listing is useful for market analysis but should not
appear in the active application queue.
`not_applying` records an explicit decision not to pursue a role. Neither status
appears in the CLI's due-action report.

Important constraints/indexes:

- Primary key: `id`
- Foreign key: `job_listing_id -> raw_job_listings.id`
- Unique index: one application workflow row per listing

Operational commands:

```text
PYTHONPATH=src .venv/bin/python -m career_radar.cli applications list
PYTHONPATH=src .venv/bin/python -m career_radar.cli applications due
PYTHONPATH=src .venv/bin/python -m career_radar.cli applications due --include-overdue
PYTHONPATH=src .venv/bin/python -m career_radar.cli applications set --job-id 175 --status interested --next-action "Review listing"
PYTHONPATH=src .venv/bin/python -m career_radar.cli applications remove --job-id 175
```

Important command behavior:

`applications set` creates or updates one workflow row. When updating, optional fields you do not provide are preserved instead of erased.

Dashboard candidate actions and historical imports only initialize a missing
application row. Replaying those actions cannot reset a later status, notes, or
application date. Edit existing workflow records through `applications set` or
the dashboard's application controls.

Interview explanation:

> Application tracking turns the project from passive market analysis into an active workflow system. It lets me connect job-market intelligence to actions, follow-ups, CV versions, and application status.

## Views

### `candidate_ai_enrichments_latest_v1`

Purpose:

Returns the newest AI enrichment run for each staged candidate.

Why it exists:

The base table stores history. Most reporting commands only need the current/latest judgement. This view gives us that without deleting old results.

### `collected_job_candidates_ai_v1`

Purpose:

Joins staged candidates with their latest AI enrichment result.

Important columns:

| Column | Meaning |
|---|---|
| `ai_relevance_label` | Latest AI relevance label, if enriched. |
| `ai_fit_score` | Latest AI fit score. |
| `ai_priority_score` | Latest AI review-priority score. |
| `ai_tracks` | Strategic tracks assigned by the latest AI result. |
| `ai_explicit_skills` | Skills directly stated in the listing. |
| `ai_inferred_skills` | JSON evidence for inferred skills. |
| `ai_skill_gaps` | Skill gaps identified by the AI step. |
| `recommended_action` | Suggested review action such as `review_for_promotion`, `manual_review`, `needs_ai_review`, `reject`, or `not_actionable`. |

Interview explanation:

> The candidate AI view joins rule signals and model judgement into one review surface. It recommends actions, but promotion into the curated dataset still stays explicit.

Operational detail:

`recommended_action = 'review_for_promotion'` does not automatically promote the candidate. It means the candidate is worth human review. If promoted, the latest explicit AI skills are added to the curated listing's normal skill links. Clean AI tracks such as `engineering`, `biomedical_healthtech`, and `data_automation` can add controlled categories during promotion, while broad tracks like `software_it` and `transferable` remain review evidence unless another rule or human assignment categorizes them.

`recommended_action = 'not_actionable'` is used for unenriched rows that the rule filter already labeled as likely irrelevant or hard negative. This keeps the manual review queue from being flooded by obvious noise.

### `job_listings_v1`

Purpose:

Cleaned view over `raw_job_listings`.

Design intent:

This is the first analytical layer. It keeps one row per job while adding cleaner fields.

Important columns:

| Column | Meaning |
|---|---|
| `id` | Listing ID from `raw_job_listings`. |
| `canonical_url` | Stable cleaned listing URL. |
| `posted_date` | Date-cast version of `posted_date_raw`, or null if invalid/missing. |
| `deadline` | Date-cast version of `deadline_raw`, or null if invalid/missing. |
| `listing_status` | Current lifecycle label: `active`, `expired`, or `no_deadline`. |
| `is_active_listing` | True when the listing has no deadline or the deadline has not passed. |
| `days_until_deadline` | Days from today to the cleaned deadline, if known. |
| `deadline_raw` | Original deadline text. |
| `skills_array` | Array created from semicolon-separated `skills_mentioned`. |
| `source_file` | Raw CSV file that most recently supplied this listing. |
| `source_row_number` | Row number in that source CSV. |
| `first_seen_at` | First time the listing was observed. |
| `last_seen_at` | Most recent time the listing was refreshed. |

Why it exists:

It lets queries use clean dates without destroying the raw imported text.

Interview explanation:

> `job_listings_v1` is a cleaned view over the raw table. It validates date strings before casting them, so non-date text does not crash analysis queries.

`career_radar_iso_date(text)` validates both the ISO shape and the actual
calendar date. For example, `2026-02-30` becomes null in reporting while remaining
unchanged in the raw column. Staging, duplicate review, and archive queries use
the same parser. Lifecycle status remains deadline-based: `no_deadline` means
unknown or unparseable, and is treated as active by existing reports; it is not
proof that an employer is still accepting applications.

### `job_listings_enriched_v1`

Purpose:

One-row-per-job reporting and prioritization view.

Design intent:

This is the main analysis surface for reports, CLI commands, and future dashboards.

Important columns:

| Column | Meaning |
|---|---|
| `job_listing_id` | Listing ID. |
| `title` | Job title. |
| `company` | Employer/company. |
| `canonical_url` | Stable listing URL. |
| `posted_date` | Cleaned posted date. |
| `deadline` | Cleaned deadline date. |
| `listing_status` | Current lifecycle label: `active`, `expired`, or `no_deadline`. |
| `is_active_listing` | True when the listing has no deadline or the deadline has not passed. |
| `days_until_deadline` | Days from today to the cleaned deadline, if known. |
| `cleaned_categories` | Comma-separated controlled category names. |
| `category_array` | Array of controlled category names. |
| `skills` | Comma-separated normalized skill names. |
| `skill_array` | Array of normalized skill names. |
| `application_status` | Actual status, or `not_started` if no application row exists. |
| `next_action` | Next application action, if any. |
| `source_file` | Raw CSV file that most recently supplied this listing. |
| `source_row_number` | Row number in that source CSV. |
| `data_engineering_automation_score` | Track score for data/engineering/automation positioning. |
| `biomedical_engineering_score` | Track score for biomedical/engineering positioning. |
| `it_support_infrastructure_score` | Track score for IT support/infrastructure positioning. |
| `software_score` | Track score for software roles. |
| `first_seen_at` | When listing was first observed. |
| `last_seen_at` | When listing was most recently refreshed. |

Why it exists:

Normalized tables are good for storage, but reporting often needs one convenient row per business entity. This view provides one row per job while preserving the normalized model underneath.

Important implementation detail:

Categories and skills are aggregated separately before joining into the final view. This prevents row multiplication.

Interview explanation:

> `job_listings_enriched_v1` is the reporting view. It aggregates categories and skills separately so each job remains one row, then adds application status and track-specific scores.

### `possible_duplicate_job_listings_v1`

Purpose:

Flags pairs of listings that may represent the same real-world opportunity even though they have different URLs.

Design intent:

This is a review view, not an automatic merge tool. URL-level deduplication is safe enough to automate. Opportunity-level deduplication is less certain because companies can reuse titles, repost jobs months later, or list similar jobs in multiple locations.

Important columns:

| Column | Meaning |
|---|---|
| `first_job_listing_id` | First listing in the possible duplicate pair. |
| `second_job_listing_id` | Second listing in the possible duplicate pair. |
| `company` | Company shared by both listings after normalization. |
| `title` | Title shared by both listings after normalization. |
| `posted_date_gap_days` | Difference between posted dates, if both are known. |
| `deadline_gap_days` | Difference between deadlines, if both are known. |
| `same_deadline` | Whether both listings have the exact same cleaned deadline. |
| `same_posted_date` | Whether both listings have the exact same cleaned posted date. |
| `same_location` | Whether the location text matches after normalization. |
| `same_summary_text` | Whether description/requirements text is exactly the same after normalization. |
| `candidate_score` | Transparent review score based on supporting evidence. |
| `candidate_confidence` | Human-readable label: `high`, `medium`, or `review`. |
| `evidence` | Short explanation of why the pair was flagged. |

Why it exists:

It separates two different ideas:

- Same listing page after URL cleanup: safe automatic dedupe.
- Same possible opportunity across different URLs: review candidate.

Interview explanation:

> I do not automatically merge different URLs just because the company and title match. Instead, I flag possible duplicates using company, title, dates, location, and text evidence, then review them separately from URL-level deduplication.

### `job_skill_match_v1`

Purpose:

Shows one row per job with skill-profile coverage.

Important columns:

| Column | Meaning |
|---|---|
| `requested_skill_count` | Number of skills requested by the listing. |
| `covered_skill_count` | Requested skills in your profile at `working` or `strong`. |
| `learning_skill_count` | Requested skills in your profile at `learning`. |
| `gap_skill_count` | Requested skills not currently in your profile. |
| `skill_coverage_score` | Transparent percentage: covered skills count fully, learning skills count half. |
| `covered_skills` | Requested skills currently covered. |
| `learning_skills` | Requested skills currently being learned. |
| `gap_skills` | Requested skills missing from the profile. |

Why it exists:

It helps separate two questions:

- Is this job strategically interesting?
- How well does the current skill profile match the listing?

Interview explanation:

> I added a job-level skill match view that compares requested skills against my profile. It uses transparent scoring rather than pretending the match percentage is objective.

### `skill_gap_summary_v1`

Purpose:

Summarizes recurring requested skills by profile status.

Important columns:

| Column | Meaning |
|---|---|
| `normalized_name` | Skill name used for grouping. |
| `jobs_requesting` | Number of current listings that mention the skill. |
| `demand_percent` | Share of current listings mentioning the skill. |
| `profile_status` | `missing`, `learning`, or `covered`. |
| `proficiency_level` | Current profile level, if present. |
| `example_jobs` | A few listings that mention the skill. |

Why it exists:

This turns job listings into a learning-priority signal. A missing skill mentioned once is interesting; a missing skill mentioned repeatedly is much more actionable.

Interview explanation:

> I aggregate skill gaps by market demand so learning priorities come from observed job listings rather than from a generic course roadmap.

### Power BI Views

Purpose:

Expose stable dashboard-friendly views while keeping the internal database model normalized.

Views:

| View | Purpose |
|---|---|
| `powerbi_jobs_v1` | One row per job with listing status, application status, fit scores, skill coverage, and source tracing. |
| `powerbi_applications_v1` | Application workflow dashboard view, including tracked/not-tracked and follow-up flags. |
| `powerbi_skill_demand_v1` | Skill demand counts across all listings and active listings. |
| `powerbi_skill_gaps_v1` | Skill demand plus your profile status: missing, learning, or covered. |
| `powerbi_category_trends_v1` | Category counts by first-seen month and listing status. |

Important design detail:

Expired listings are retained in Power BI views. Dashboards can filter to active listings for operational questions, but historical/trend analysis can still use expired rows.

Interview explanation:

> I created separate Power BI views so dashboards connect to stable reporting surfaces rather than raw internal tables. The views expose listing lifecycle fields so I can filter active opportunities without losing old listings from market analysis.

## Common Join Patterns

### Jobs To Skills

```sql
select
    j.title,
    j.company,
    s.normalized_name as skill
from job_listings_v1 j
join job_listing_skills jls on jls.job_listing_id = j.id
join skills s on s.id = jls.skill_id;
```

Mental model:

```text
jobs -> job_listing_skills -> skills
```

### Jobs To Categories

```sql
select
    j.title,
    j.company,
    c.normalized_name as category
from job_listings_v1 j
join job_listing_categories jlc on jlc.job_listing_id = j.id
join job_categories c on c.id = jlc.category_id;
```

Mental model:

```text
jobs -> job_listing_categories -> job_categories
```

### Jobs To Applications

```sql
select
    j.title,
    j.company,
    coalesce(a.status, 'not_started') as application_status
from job_listings_v1 j
left join job_applications a on a.job_listing_id = j.id;
```

Mental model:

```text
jobs -> optional application workflow row
```

## Design Rules For Future Changes

1. Preserve raw imported values when practical.
2. Add cleaned analytical fields in views or normalized tables.
3. Use bridge tables for many-to-many relationships.
4. Keep IDs stable once other tables depend on them.
5. Prefer transparent scoring before machine learning.
6. Use the enriched view for reporting and CLI output.
7. Keep expired listings for market analysis, but filter them out of operational queues by default.
8. Keep credentials in `.env`, never in committed code.
9. Use AI enrichment as evidence-producing decision support, not as automatic truth.
