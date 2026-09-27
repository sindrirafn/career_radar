-- Jobs with their current application status.
--
-- left join keeps jobs even when you have not created an application row yet.
-- coalesce turns missing application rows into a readable "not_started" status.
select
    j.id as job_listing_id,
    j.title,
    j.company,
    j.deadline,
    coalesce(a.status, 'not_started') as application_status,
    a.applied_on,
    a.follow_up_date,
    a.next_action
from job_listings_v1 j
left join job_applications a on a.job_listing_id = j.id
order by j.deadline nulls last, j.company, j.title;

-- Urgent jobs that are not applied/interviewing/offer yet.
--
-- This is the practical "what should I look at next?" query.
select
    j.id as job_listing_id,
    j.title,
    j.company,
    j.deadline,
    coalesce(a.status, 'not_started') as application_status,
    a.next_action
from job_listings_v1 j
left join job_applications a on a.job_listing_id = j.id
where j.deadline is not null
  and j.deadline <= date '2026-08-21' + interval '7 days'
  and coalesce(a.status, 'not_started') not in ('applied', 'interviewing', 'offer')
order by j.deadline, j.company, j.title;

-- Application pipeline count by status.
--
-- This is a simple progress dashboard query.
select
    coalesce(a.status, 'not_started') as application_status,
    count(*) as job_count
from job_listings_v1 j
left join job_applications a on a.job_listing_id = j.id
group by coalesce(a.status, 'not_started')
order by job_count desc, application_status;

-- Example insert/update pattern for tracking one application.
--
-- Keep this commented until you intentionally want to create or update an
-- application workflow row.
--
-- insert into job_applications (
--     job_listing_id,
--     status,
--     applied_on,
--     cv_version,
--     cover_letter_version,
--     next_action,
--     follow_up_date,
--     notes
-- )
-- values (
--     1,
--     'preparing',
--     null,
--     'cv-v1',
--     'cover-letter-v1',
--     'finish tailored application',
--     date '2026-08-22',
--     'Prioritize because deadline is 2026-08-23.'
-- )
-- on conflict (job_listing_id) do update
-- set
--     status = excluded.status,
--     applied_on = excluded.applied_on,
--     cv_version = excluded.cv_version,
--     cover_letter_version = excluded.cover_letter_version,
--     next_action = excluded.next_action,
--     follow_up_date = excluded.follow_up_date,
--     notes = excluded.notes,
--     updated_at = now();
