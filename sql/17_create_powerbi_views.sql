-- Power BI-ready reporting views.
--
-- These views are intentionally denormalized or dashboard-shaped. The internal
-- storage model remains normalized, but BI tools usually work best with stable
-- reporting surfaces that already include the fields users want to slice by.
--
-- Important: expired listings are retained. Operational reports can filter to
-- active listings, but market analysis and trend reporting need historical
-- rows.
drop view if exists powerbi_category_trends_v1;
drop view if exists powerbi_skill_gaps_v1;
drop view if exists powerbi_skill_demand_v1;
drop view if exists powerbi_applications_v1;
drop view if exists powerbi_jobs_v1;

create view powerbi_jobs_v1 as
select
    e.job_listing_id,
    e.title,
    e.company,
    e.source,
    e.url,
    e.location,
    e.posted_date,
    e.deadline,
    e.deadline_raw,
    e.listing_status,
    e.is_active_listing,
    e.days_until_deadline,
    e.category_guess,
    e.cleaned_categories,
    e.skills,
    e.application_status,
    e.applied_on,
    e.follow_up_date,
    e.next_action,
    e.cv_version,
    e.cover_letter_version,
    e.data_engineering_automation_score,
    e.biomedical_engineering_score,
    e.it_support_infrastructure_score,
    e.software_score,
    m.requested_skill_count,
    m.covered_skill_count,
    m.learning_skill_count,
    m.gap_skill_count,
    m.skill_coverage_score,
    array_to_string(m.covered_skills, ', ') as covered_skills,
    array_to_string(m.learning_skills, ', ') as learning_skills,
    array_to_string(m.gap_skills, ', ') as gap_skills,
    e.source_file,
    e.source_row_number,
    e.first_seen_at,
    e.last_seen_at
from job_listings_enriched_v1 e
left join job_skill_match_v1 m on m.job_listing_id = e.job_listing_id;

create view powerbi_applications_v1 as
select
    e.job_listing_id,
    e.company,
    e.title,
    e.deadline,
    e.listing_status,
    e.is_active_listing,
    e.application_status,
    e.applied_on,
    e.follow_up_date,
    e.next_action,
    e.cv_version,
    e.cover_letter_version,
    e.application_notes,
    e.application_status <> 'not_started' as is_tracked_application,
    e.application_status in ('interested', 'preparing', 'applied', 'interviewing')
        as is_active_application,
    case
        when e.follow_up_date is not null
         and e.follow_up_date <= current_date then true
        else false
    end as follow_up_due,
    e.data_engineering_automation_score,
    e.biomedical_engineering_score,
    e.it_support_infrastructure_score,
    e.software_score
from job_listings_enriched_v1 e;

create view powerbi_skill_demand_v1 as
with totals as (
    select
        count(*) as total_jobs,
        count(*) filter (where is_active_listing) as active_jobs
    from job_listings_enriched_v1
)
select
    s.id as skill_id,
    s.name,
    s.normalized_name,
    count(distinct e.job_listing_id) as jobs_requesting,
    count(distinct e.job_listing_id) filter (where e.is_active_listing)
        as active_jobs_requesting,
    count(distinct e.job_listing_id) filter (where e.listing_status = 'expired')
        as expired_jobs_requesting,
    count(distinct e.job_listing_id) filter (where e.listing_status = 'no_deadline')
        as no_deadline_jobs_requesting,
    round(
        100.0 * count(distinct e.job_listing_id)
        / nullif((select total_jobs from totals), 0),
        1
    ) as demand_percent_all_listings,
    round(
        100.0 * count(distinct e.job_listing_id) filter (where e.is_active_listing)
        / nullif((select active_jobs from totals), 0),
        1
    ) as demand_percent_active_listings
from skills s
join job_listing_skills jls on jls.skill_id = s.id
join job_listings_enriched_v1 e on e.job_listing_id = jls.job_listing_id
group by s.id, s.name, s.normalized_name;

create view powerbi_skill_gaps_v1 as
select
    d.skill_id,
    d.name,
    d.normalized_name,
    d.jobs_requesting,
    d.active_jobs_requesting,
    d.expired_jobs_requesting,
    d.no_deadline_jobs_requesting,
    d.demand_percent_all_listings,
    d.demand_percent_active_listings,
    ps.proficiency_level,
    case
        when ps.skill_id is null or not ps.include_in_gap_analysis then 'missing'
        when ps.proficiency_level = 'learning' then 'learning'
        else 'covered'
    end as profile_status,
    ps.evidence,
    ps.include_in_gap_analysis
from powerbi_skill_demand_v1 d
left join profile_skills ps on ps.skill_id = d.skill_id;

create view powerbi_category_trends_v1 as
select
    c.id as category_id,
    c.name,
    c.normalized_name,
    date_trunc('month', e.first_seen_at)::date as first_seen_month,
    e.listing_status,
    count(distinct e.job_listing_id) as job_count,
    count(distinct e.job_listing_id) filter (where e.is_active_listing)
        as active_job_count,
    count(distinct e.job_listing_id) filter (where e.listing_status = 'expired')
        as expired_job_count
from job_categories c
join job_listing_categories jlc on jlc.category_id = c.id
join job_listings_enriched_v1 e on e.job_listing_id = jlc.job_listing_id
group by
    c.id,
    c.name,
    c.normalized_name,
    date_trunc('month', e.first_seen_at)::date,
    e.listing_status;
