-- Staging table for automatically collected job-listing candidates.
--
-- This table is intentionally separate from raw_job_listings. Source collectors
-- can be broad and noisy; the main analytics tables should stay curated for
-- career-relevant listings.
drop view if exists collected_job_candidates_ai_v1;
drop view if exists collected_job_candidates_ranked_v1;

create table if not exists collected_job_candidates (
    id bigserial primary key,
    source text not null,
    title text not null,
    company text not null,
    url text not null,
    canonical_url text not null,
    location text,
    posted_date_raw text,
    deadline_raw text,
    category_guess text,
    description text,
    requirements text,
    skills_mentioned text,
    notes text,

    -- Rule-based relevance is a high-recall gate. It should find plausible
    -- candidates for review; it should not be treated as final fit judgement.
    rule_relevance_score integer not null default 0,
    rule_relevance_label text not null default 'likely_irrelevant' check (
        rule_relevance_label in (
            'strong_candidate',
            'possible_candidate',
            'likely_irrelevant',
            'hard_negative'
        )
    ),
    matched_tracks text[] not null default '{}',
    matched_keywords text[] not null default '{}',
    hard_negative_flags text[] not null default '{}',
    needs_ai_review boolean not null default false,

    -- Human review workflow. Promotion means the candidate has been copied into
    -- raw_job_listings through the normal shared ingestion layer.
    review_status text not null default 'unreviewed' check (
        review_status in ('unreviewed', 'approved', 'rejected', 'promoted')
    ),
    promoted_job_listing_id bigint references raw_job_listings(id) on delete set null,

    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table collected_job_candidates
    add column if not exists category_guess text;

alter table collected_job_candidates
    add column if not exists requirements text;

alter table collected_job_candidates
    add column if not exists skills_mentioned text;

alter table collected_job_candidates
    add column if not exists rule_relevance_score integer not null default 0;

alter table collected_job_candidates
    add column if not exists rule_relevance_label text not null default 'likely_irrelevant';

alter table collected_job_candidates
    add column if not exists matched_tracks text[] not null default '{}';

alter table collected_job_candidates
    add column if not exists matched_keywords text[] not null default '{}';

alter table collected_job_candidates
    add column if not exists hard_negative_flags text[] not null default '{}';

alter table collected_job_candidates
    add column if not exists needs_ai_review boolean not null default false;

alter table collected_job_candidates
    add column if not exists review_status text not null default 'unreviewed';

alter table collected_job_candidates
    add column if not exists promoted_job_listing_id bigint references raw_job_listings(id) on delete set null;

alter table collected_job_candidates
    add column if not exists first_seen_at timestamptz not null default now();

alter table collected_job_candidates
    add column if not exists last_seen_at timestamptz not null default now();

alter table collected_job_candidates
    add column if not exists created_at timestamptz not null default now();

alter table collected_job_candidates
    add column if not exists updated_at timestamptz not null default now();

create unique index if not exists idx_collected_job_candidates_canonical_url
    on collected_job_candidates (canonical_url);

create index if not exists idx_collected_job_candidates_relevance
    on collected_job_candidates (
        rule_relevance_label,
        rule_relevance_score desc,
        review_status
    );

create index if not exists idx_collected_job_candidates_needs_ai_review
    on collected_job_candidates (needs_ai_review)
    where needs_ai_review;

create view collected_job_candidates_ranked_v1 as
select
    c.*,
    career_radar_iso_date(c.deadline_raw) as deadline,
    case
        when career_radar_iso_date(c.deadline_raw) < current_date then 'expired'
        when career_radar_iso_date(c.deadline_raw) is not null then 'active'
        else 'no_deadline'
    end as listing_status,
    career_radar_iso_date(c.deadline_raw) - current_date as days_until_deadline,
    case
        when c.rule_relevance_label in ('strong_candidate', 'possible_candidate')
         and c.review_status in ('unreviewed', 'approved')
            then true
        else false
    end as is_actionable_candidate
from collected_job_candidates c;
