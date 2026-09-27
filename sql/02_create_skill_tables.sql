-- Canonical list of skills observed in job listings.
--
-- name keeps a readable display form from the source data.
-- normalized_name is the comparison form used for grouping and deduplication.
-- For example, "Power BI" and " power bi " should count as the same skill.
create table if not exists skills (
    id bigserial primary key,
    name text not null,
    normalized_name text not null unique,
    created_at timestamptz not null default now()
);

-- Many-to-many link between job listings and skills.
--
-- A job can mention many skills, and one skill can appear in many jobs. This
-- table represents that relationship without duplicating either full job rows
-- or full skill rows.
create table if not exists job_listing_skills (
    job_listing_id bigint not null references raw_job_listings(id) on delete cascade,
    skill_id bigint not null references skills(id) on delete cascade,
    source_text text not null,
    created_at timestamptz not null default now(),

    primary key (job_listing_id, skill_id)
);

-- These indexes keep the common analysis paths fast as the dataset grows:
-- "show me all jobs for this skill" and "show me all skills for this job".
create index if not exists idx_skills_normalized_name
    on skills (normalized_name);

create index if not exists idx_job_listing_skills_skill_id
    on job_listing_skills (skill_id);
