-- Raw import table for the manually collected job-listing CSV.
--
-- This table intentionally keeps messy source fields as text. For example,
-- deadline_raw can contain either a real date string or "No stated deadline".
-- Cleaner typed fields are exposed in job_listings_v1 instead of overwriting
-- the original imported values.
create table if not exists raw_job_listings (
    id bigserial primary key,
    title text not null,
    company text not null,
    source text not null,
    url text not null,
    canonical_url text,
    location text,
    posted_date_raw text,
    deadline_raw text,
    category_guess text,
    description text,
    requirements text,
    skills_mentioned text,
    notes text,
    source_file text,
    source_row_number integer,

    -- timestamptz stores a real moment in time. That matters once imports are
    -- scheduled, because "2026-08-22 09:00" should mean one exact instant even
    -- if a dashboard, server, or teammate later views it in another timezone.
    imported_at timestamptz not null default now(),

    -- Later imports should update the same listing instead of creating a new
    -- row. first_seen_at records when we first observed it; last_seen_at records
    -- when an import most recently observed or refreshed it.
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now()
);

-- Existing databases may have been created before these columns existed.
alter table raw_job_listings
    add column if not exists canonical_url text;

alter table raw_job_listings
    add column if not exists first_seen_at timestamptz not null default now();

alter table raw_job_listings
    add column if not exists last_seen_at timestamptz not null default now();

alter table raw_job_listings
    add column if not exists source_file text;

alter table raw_job_listings
    add column if not exists source_row_number integer;

-- Backfill canonical_url for rows imported before the column existed.
update raw_job_listings
set canonical_url = replace(replace(url, '?utm_source=chatgpt.com', ''), '&utm_source=chatgpt.com', '')
where canonical_url is null;

alter table raw_job_listings
    alter column canonical_url set not null;

-- Stable listing identity. This is what lets applications safely point at a
-- listing row while repeated imports update that row instead of deleting it.
create unique index if not exists idx_raw_job_listings_canonical_url
    on raw_job_listings (canonical_url);

-- The Python import script is the normal project workflow. It reads every CSV
-- in data/raw, derives canonical_url, and records source_file/source_row_number
-- for traceability.
