-- Jobs with their cleaned categories.
--
-- This is the main sanity-check query for the category layer.
select
    j.id as job_listing_id,
    j.title,
    j.company,
    j.category_guess,
    string_agg(c.normalized_name, ', ' order by c.normalized_name) as cleaned_categories
from job_listings_v1 j
left join job_listing_categories jlc on jlc.job_listing_id = j.id
left join job_categories c on c.id = jlc.category_id
group by j.id, j.title, j.company, j.category_guess
order by j.company, j.title;

-- Category counts.
--
-- This answers: what types of opportunities are present in the current sample?
select
    c.normalized_name as category,
    count(distinct jlc.job_listing_id) as listing_count
from job_categories c
join job_listing_categories jlc on jlc.category_id = c.id
group by c.normalized_name
order by listing_count desc, category;

-- Jobs that still need category assignment.
--
-- New imported jobs should appear here until you classify them manually or
-- build a rule/model that assigns categories.
select
    j.id as job_listing_id,
    j.title,
    j.company,
    j.category_guess
from job_listings_v1 j
left join job_listing_categories jlc on jlc.job_listing_id = j.id
where jlc.job_listing_id is null
order by j.company, j.title;

-- Skills by cleaned category.
--
-- This combines the category layer with the skills layer. It is the beginning
-- of "what skills are common in each career path?"
select
    c.normalized_name as category,
    s.normalized_name as skill,
    count(distinct j.id) as listing_count
from job_listings_v1 j
join job_listing_categories jlc on jlc.job_listing_id = j.id
join job_categories c on c.id = jlc.category_id
join job_listing_skills jls on jls.job_listing_id = j.id
join skills s on s.id = jls.skill_id
group by c.normalized_name, s.normalized_name
order by c.normalized_name, listing_count desc, skill;

-- Category-based multi-track scoring.
--
-- This is intentionally transparent: it produces separate scores for different
-- career angles instead of pretending one fit_score can represent everything.
with category_flags as (
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
        bool_or(c.normalized_name = 'quality') as is_quality,
        bool_or(c.normalized_name = 'r_and_d') as is_r_and_d
    from job_listing_categories jlc
    join job_categories c on c.id = jlc.category_id
    group by jlc.job_listing_id
)
select
    j.id as job_listing_id,
    j.title,
    j.company,
    j.deadline,

    case when coalesce(f.is_data, false) then 2 else 0 end
    + case when coalesce(f.is_engineering, false) then 2 else 0 end
    + case when coalesce(f.is_automation, false) then 2 else 0 end
        as data_engineering_automation_score,

    case when coalesce(f.is_biomedical, false) then 3 else 0 end
    + case when coalesce(f.is_healthcare, false) then 1 else 0 end
    + case when coalesce(f.is_engineering, false) then 2 else 0 end
    + case when coalesce(f.is_quality, false) then 1 else 0 end
    + case when coalesce(f.is_r_and_d, false) then 1 else 0 end
        as biomedical_engineering_score,

    case when coalesce(f.is_it_support, false) then 3 else 0 end
    + case when coalesce(f.is_infrastructure, false) then 2 else 0 end
    + case when coalesce(f.is_automation, false) then 1 else 0 end
        as it_support_infrastructure_score,

    case when coalesce(f.is_software, false) then 3 else 0 end
    + case when coalesce(f.is_infrastructure, false) then 1 else 0 end
    + case when coalesce(f.is_data, false) then 1 else 0 end
        as software_score
from job_listings_v1 j
left join category_flags f on f.job_listing_id = j.id
order by
    data_engineering_automation_score desc,
    biomedical_engineering_score desc,
    it_support_infrastructure_score desc,
    j.deadline nulls last,
    j.company;

