-- Controlled category vocabulary for job-market analysis.
--
-- This table is intentionally separate from raw_job_listings.category_guess.
-- category_guess is the original human note from the CSV. job_categories is
-- the cleaned vocabulary we want to use for analysis, scoring, and dashboards.
create table if not exists job_categories (
    id bigserial primary key,
    name text not null,
    normalized_name text not null unique,
    description text,
    created_at timestamptz not null default now()
);

-- Many-to-many link between listings and cleaned categories.
--
-- A job can belong to multiple categories, and each category can apply to many
-- jobs. For example, a Nox Medical role can be both biomedical and project
-- management; a hosting role can be both infrastructure and IT support.
create table if not exists job_listing_categories (
    job_listing_id bigint not null references raw_job_listings(id) on delete cascade,
    category_id bigint not null references job_categories(id) on delete cascade,

    -- source records how the assignment was made. That matters later when some
    -- assignments are manual and others may come from rules or a classifier.
    source text not null default 'manual' check (
        source in ('manual', 'seed', 'rule', 'model')
    ),

    assigned_at timestamptz not null default now(),

    primary key (job_listing_id, category_id)
);

-- Common lookup paths:
-- 1. find all jobs in one category
-- 2. find all categories for one job
create index if not exists idx_job_listing_categories_category_id
    on job_listing_categories (category_id);

create index if not exists idx_job_listing_categories_job_listing_id
    on job_listing_categories (job_listing_id);

