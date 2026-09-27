-- One-row-per-job analytical view for reporting and prioritization.
--
-- The storage model is normalized: jobs, skills, categories, and applications
-- live in separate tables. That is good for data integrity. This view is the
-- convenient reporting layer that pulls those pieces back together.
drop view if exists job_listings_enriched_v1;

create view job_listings_enriched_v1 as
with category_list as (
    -- Collapse many category rows into one category summary per job.
    select
        jlc.job_listing_id,
        string_agg(c.normalized_name, ', ' order by c.normalized_name) as cleaned_categories,
        array_agg(c.normalized_name order by c.normalized_name) as category_array
    from job_listing_categories jlc
    join job_categories c on c.id = jlc.category_id
    group by jlc.job_listing_id
),
skill_list as (
    -- Collapse many skill rows into one skill summary per job.
    select
        jls.job_listing_id,
        string_agg(s.normalized_name, ', ' order by s.normalized_name) as skills,
        array_agg(s.normalized_name order by s.normalized_name) as skill_array
    from job_listing_skills jls
    join skills s on s.id = jls.skill_id
    group by jls.job_listing_id
),
category_flags as (
    -- Convert category membership into true/false flags used by scoring.
    select
        jlc.job_listing_id,
        bool_or(c.normalized_name = 'biomedical') as is_biomedical,
        bool_or(c.normalized_name = 'healthcare') as is_healthcare,
        bool_or(c.normalized_name = 'engineering') as is_engineering,
        bool_or(c.normalized_name = 'data') as is_data,
        bool_or(c.normalized_name = 'automation') as is_automation,
        bool_or(c.normalized_name = 'software') as is_software,
        bool_or(c.normalized_name = 'it_support') as is_it_support,
        bool_or(c.normalized_name = 'infrastructure') as is_infrastructure,
        bool_or(c.normalized_name = 'project_management') as is_project_management,
        bool_or(c.normalized_name = 'manufacturing') as is_manufacturing,
        bool_or(c.normalized_name = 'quality') as is_quality,
        bool_or(c.normalized_name = 'r_and_d') as is_r_and_d,
        bool_or(c.normalized_name = 'business_analysis') as is_business_analysis,
        bool_or(c.normalized_name = 'telecommunications') as is_telecommunications
    from job_listing_categories jlc
    join job_categories c on c.id = jlc.category_id
    group by jlc.job_listing_id
)
select
    j.id as job_listing_id,
    j.title,
    j.company,
    j.source,
    j.url,
    j.canonical_url,
    j.location,
    j.posted_date,
    j.deadline,
    j.listing_status,
    j.is_active_listing,
    j.days_until_deadline,
    j.deadline_raw,
    j.category_guess,
    j.source_file,
    j.source_row_number,

    coalesce(cl.cleaned_categories, '') as cleaned_categories,
    coalesce(cl.category_array, array[]::text[]) as category_array,
    coalesce(sl.skills, '') as skills,
    coalesce(sl.skill_array, array[]::text[]) as skill_array,

    coalesce(a.status, 'not_started') as application_status,
    a.applied_on,
    a.follow_up_date,
    a.next_action,
    a.cv_version,
    a.cover_letter_version,
    a.notes as application_notes,

    case when coalesce(f.is_data, false) then 2 else 0 end
    + case when coalesce(f.is_engineering, false) then 2 else 0 end
    + case when coalesce(f.is_automation, false) then 2 else 0 end
        as data_engineering_automation_score,

    case when coalesce(f.is_biomedical, false) then 3 else 0 end
    + case when coalesce(f.is_healthcare, false) then 1 else 0 end
    + case when coalesce(f.is_engineering, false) then 2 else 0 end
    + case when coalesce(f.is_quality, false) then 1 else 0 end
    + case when coalesce(f.is_r_and_d, false) then 1 else 0 end
    + case when coalesce(f.is_manufacturing, false) then 1 else 0 end
        as biomedical_engineering_score,

    case when coalesce(f.is_it_support, false) then 3 else 0 end
    + case when coalesce(f.is_infrastructure, false) then 2 else 0 end
    + case when coalesce(f.is_automation, false) then 1 else 0 end
        as it_support_infrastructure_score,

    case when coalesce(f.is_software, false) then 3 else 0 end
    + case when coalesce(f.is_infrastructure, false) then 1 else 0 end
    + case when coalesce(f.is_data, false) then 1 else 0 end
        as software_score,

    j.first_seen_at,
    j.last_seen_at
from job_listings_v1 j
left join category_list cl on cl.job_listing_id = j.id
left join skill_list sl on sl.job_listing_id = j.id
left join job_applications a on a.job_listing_id = j.id
left join category_flags f on f.job_listing_id = j.id;
