with job_skill_flags as (
    select
        jls.job_listing_id,
        bool_or(s.normalized_name = 'automation') as has_automation,
        bool_or(s.normalized_name = 'python') as has_python,
        bool_or(s.normalized_name = 'power bi') as has_power_bi,
        bool_or(s.normalized_name in ('databases', 'data')) as has_database_skill
    from job_listing_skills jls
    join skills s on s.id = jls.skill_id
    group by jls.job_listing_id
)
select
    j.id,
    j.title,
    j.company,
    j.category_guess,
    j.deadline,

    case when lower(j.category_guess) like '%engineering%' then 2 else 0 end
    + case when lower(j.category_guess) like '%data%' then 2 else 0 end
    + case when lower(j.category_guess) ~ '(medical|device|biomedical)' then 2 else 0 end
    + case when coalesce(f.has_automation, false) then 2 else 0 end
    + case when coalesce(f.has_python, false) then 1 else 0 end
    + case when coalesce(f.has_power_bi, false) then 1 else 0 end
    + case when coalesce(f.has_database_skill, false) then 1 else 0 end
    as fit_score

from job_listings_v1 j
left join job_skill_flags f on f.job_listing_id = j.id
order by fit_score desc, j.deadline nulls last;