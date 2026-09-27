-- Skill profile comparison views.
--
-- These views compare employer-demanded skills against profile_skills. They do
-- not decide whether you can apply to a job. They are decision-support tools:
-- useful for prioritizing learning, CV emphasis, and interview prep.
drop view if exists skill_gap_summary_v1;
drop view if exists job_skill_match_v1;

create view job_skill_match_v1 as
with skill_counts as (
    select
        jls.job_listing_id,
        count(*) as requested_skill_count,
        count(*) filter (
            where ps.proficiency_level in ('working', 'strong')
        ) as covered_skill_count,
        count(*) filter (
            where ps.proficiency_level = 'learning'
        ) as learning_skill_count,
        count(*) filter (
            where ps.skill_id is null
        ) as gap_skill_count,
        coalesce(
            array_agg(s.normalized_name order by s.normalized_name),
            array[]::text[]
        ) as requested_skills,
        coalesce(
            array_agg(s.normalized_name order by s.normalized_name)
                filter (where ps.proficiency_level in ('working', 'strong')),
            array[]::text[]
        ) as covered_skills,
        coalesce(
            array_agg(s.normalized_name order by s.normalized_name)
                filter (where ps.proficiency_level = 'learning'),
            array[]::text[]
        ) as learning_skills,
        coalesce(
            array_agg(s.normalized_name order by s.normalized_name)
                filter (where ps.skill_id is null),
            array[]::text[]
        ) as gap_skills
    from job_listing_skills jls
    join skills s on s.id = jls.skill_id
    left join profile_skills ps
      on ps.skill_id = s.id
     and ps.include_in_gap_analysis
    group by jls.job_listing_id
),
scored as (
    select
        *,
        round(
            100.0
            * (covered_skill_count + (learning_skill_count * 0.5))
            / nullif(requested_skill_count, 0),
            1
        ) as skill_coverage_score
    from skill_counts
)
select
    e.job_listing_id,
    e.company,
    e.title,
    e.deadline,
    e.listing_status,
    e.is_active_listing,
    e.days_until_deadline,
    e.application_status,
    e.next_action,
    e.cleaned_categories,
    e.data_engineering_automation_score,
    e.biomedical_engineering_score,
    e.it_support_infrastructure_score,
    e.software_score,
    coalesce(s.requested_skill_count, 0) as requested_skill_count,
    coalesce(s.covered_skill_count, 0) as covered_skill_count,
    coalesce(s.learning_skill_count, 0) as learning_skill_count,
    coalesce(s.gap_skill_count, 0) as gap_skill_count,
    coalesce(s.skill_coverage_score, 0) as skill_coverage_score,
    coalesce(s.requested_skills, array[]::text[]) as requested_skills,
    coalesce(s.covered_skills, array[]::text[]) as covered_skills,
    coalesce(s.learning_skills, array[]::text[]) as learning_skills,
    coalesce(s.gap_skills, array[]::text[]) as gap_skills
from job_listings_enriched_v1 e
left join scored s on s.job_listing_id = e.job_listing_id;

create view skill_gap_summary_v1 as
with skill_demand as (
    select
        s.id as skill_id,
        s.name,
        s.normalized_name,
        count(distinct jls.job_listing_id) as jobs_requesting,
        (array_agg(
            e.company || ' - ' || e.title
            order by e.company, e.title
        ))[1:5] as example_jobs
    from skills s
    join job_listing_skills jls on jls.skill_id = s.id
    join job_listings_enriched_v1 e on e.job_listing_id = jls.job_listing_id
    group by s.id, s.name, s.normalized_name
)
select
    sd.skill_id,
    sd.name,
    sd.normalized_name,
    sd.jobs_requesting,
    round(
        100.0 * sd.jobs_requesting / nullif((select count(*) from raw_job_listings), 0),
        1
    ) as demand_percent,
    ps.proficiency_level,
    case
        when ps.skill_id is null then 'missing'
        when ps.proficiency_level = 'learning' then 'learning'
        else 'covered'
    end as profile_status,
    ps.evidence,
    sd.example_jobs
from skill_demand sd
left join profile_skills ps
  on ps.skill_id = sd.skill_id
 and ps.include_in_gap_analysis;
