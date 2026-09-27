-- 1. How many roles are in each manually guessed category?
--
-- This checks whether our first category labels are useful or too granular.
select
    category_guess,
    count(*) as listing_count
from job_listings_v1
group by category_guess
order by listing_count desc, category_guess;

-- 2. Which deadlines are urgent relative to the collection date?
--
-- The fixed collection date makes this query reproducible. If we used
-- current_date, the result would change just because time passed.
with settings as (
    select date '2026-08-21' as collection_date
)
select
    j.deadline,
    j.title,
    j.company,
    j.category_guess
from job_listings_v1 j
cross join settings s
where j.deadline is not null
  and j.deadline <= s.collection_date + interval '7 days'
order by j.deadline, j.company;

-- 3. Which skills are mentioned most often?
--
-- This now uses the normalized skills tables instead of parsing semicolon text
-- at query time. That is the more durable structure for trend analysis.
select
    s.normalized_name as skill,
    count(distinct jls.job_listing_id) as listing_count
from skills s
join job_listing_skills jls on jls.skill_id = s.id
group by s.normalized_name
order by listing_count desc, skill;

-- 4. Which roles are most relevant to the data + engineering + automation positioning?
--
-- This is still a simple baseline search, not a classifier. It is deliberately
-- transparent so we can inspect why a row matched.
select
    title,
    company,
    category_guess,
    skills_mentioned
from job_listings_v1
where lower(concat_ws(' ', category_guess, description, requirements, skills_mentioned))
      ~ '(data|database|automation|control|engineering|medical|device|r&d|python|sql|power bi)'
order by company, title;

-- 5. What needs cleanup before stronger analysis?
--
-- Data-quality queries are part of the work. They show which fields would
-- weaken dashboards, scoring, or historical tracking if left unhandled.
select
    title,
    company,
    posted_date,
    deadline,
    deadline_raw,
    case when posted_date is null then 'missing posted_date' end as posted_date_issue,
    case when deadline is null then 'missing or non-date deadline' end as deadline_issue
from job_listings_v1
where posted_date is null
   or deadline is null
order by company, title;
