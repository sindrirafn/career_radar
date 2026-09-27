-- Cleaned view over the raw imported job listings.
--
-- A view lets us keep the original source values untouched while still giving
-- analysis queries cleaner fields such as typed dates and canonical URLs.
drop view if exists powerbi_category_trends_v1;
drop view if exists powerbi_skill_gaps_v1;
drop view if exists powerbi_skill_demand_v1;
drop view if exists powerbi_applications_v1;
drop view if exists powerbi_jobs_v1;
drop view if exists skill_gap_summary_v1;
drop view if exists job_skill_match_v1;
drop view if exists possible_duplicate_job_listings_v1;
drop view if exists job_listings_enriched_v1;
drop view if exists job_listings_v1;

create view job_listings_v1 as
select
    id,
    title,
    company,
    source,
    url,
    canonical_url,

    location,

    -- Validate date-like text before casting. Without this guard, values such
    -- as "No stated deadline" would crash the view.
    career_radar_iso_date(posted_date_raw) as posted_date,
    cleaned_dates.deadline,
    case
        when cleaned_dates.deadline is null then 'no_deadline'
        when cleaned_dates.deadline < current_date then 'expired'
        else 'active'
    end as listing_status,
    cleaned_dates.deadline is null
        or cleaned_dates.deadline >= current_date
        as is_active_listing,
    case
        when cleaned_dates.deadline is not null then cleaned_dates.deadline - current_date
        else null
    end as days_until_deadline,

    -- Keep the raw deadline beside the cleaned one so data-quality issues stay
    -- explainable instead of disappearing.
    deadline_raw,
    category_guess,
    description,
    requirements,
    skills_mentioned,
    string_to_array(skills_mentioned, ';') as skills_array,
    notes,
    source_file,
    source_row_number,
    imported_at,
    first_seen_at,
    last_seen_at
from raw_job_listings
cross join lateral (
    select
        career_radar_iso_date(deadline_raw) as deadline
) cleaned_dates;
