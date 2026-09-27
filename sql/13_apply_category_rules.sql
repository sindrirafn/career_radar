-- Rule-based category assignments for newly imported listings.
--
-- Manual/seeded assignments are still preferred when available. These rules
-- provide a transparent baseline for larger CSV imports so new jobs are not
-- left uncategorized.
with listing_text as (
    select
        id as job_listing_id,
        lower(
            coalesce(title, '') || ' ' ||
            coalesce(company, '') || ' ' ||
            coalesce(category_guess, '') || ' ' ||
            coalesce(description, '') || ' ' ||
            coalesce(requirements, '') || ' ' ||
            coalesce(skills_mentioned, '') || ' ' ||
            coalesce(notes, '')
        ) as search_text
    from raw_job_listings
),
rule_matches as (
    select
        lt.job_listing_id,
        rules.category_name
    from listing_text lt
    cross join lateral (
        values
            ('biomedical', lt.search_text ~ '(biomedical|medical[- ]?device|medical devices|health[- ]?tech|life[- ]?science|kerecis|nox medical)'),
            ('healthcare', lt.search_text ~ '(healthcare|health sector|clinical|pharma|pharmaceutical|vistor)'),
            ('engineering', lt.search_text ~ '(engineering|engineer|verkfræð|tæknifræð|technical engineering)'),
            ('data', lt.search_text ~ '(data|analytics|analysis|greining|database|databases|sql|power bi|business intelligence|reporting)'),
            ('automation', lt.search_text ~ '(automation|automated|sjálfvirk|scripting|workflow|process improvement|control systems|controls|powershell)'),
            ('software', lt.search_text ~ '(software|developer|programmer|programming|forrit|api|apis|\\.net|c#|git|testing)'),
            ('it_support', lt.search_text ~ '(it support|frontline|help desk|helpdesk|endpoint|user support|user service|support role|framlínuþjónustu|notendalausn)'),
            ('infrastructure', lt.search_text ~ '(infrastructure|hosting|network|networking|cloud|sre|systems administration|system administrator|hýsing|microsoft 365|telecommunications)'),
            ('project_management', lt.search_text ~ '(project management|project coordination|project manager|verkefnastj|coordination|delivery planning)'),
            ('manufacturing', lt.search_text ~ '(manufacturing|production|production introduction|process engineering|framleið|process improvement)'),
            ('quality', lt.search_text ~ '(quality|validation|verification|qms|iso 13485|capa|regulated)'),
            ('telecommunications', lt.search_text ~ '(telecommunications|fjarskipt|radio|radíó|connectivity)'),
            ('r_and_d', lt.search_text ~ '(r&d|research and development|research|vöruþróun|product development|prototype|prototyping|experiments)'),
            ('business_analysis', lt.search_text ~ '(business analysis|business development|viðskiptaþróun|requirements|process analysis)')
    ) as rules(category_name, is_match)
    where rules.is_match
)
insert into job_listing_categories (
    job_listing_id,
    category_id,
    source
)
select
    rm.job_listing_id,
    c.id,
    'rule'
from rule_matches rm
join job_categories c on c.normalized_name = rm.category_name
on conflict (job_listing_id, category_id) do nothing;

