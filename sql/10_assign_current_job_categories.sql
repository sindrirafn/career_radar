-- Manual category assignments for the first 10-job CSV shortlist.
--
-- This file deliberately uses canonical_url instead of job IDs. IDs are stable
-- now, but URLs are easier to audit when reading the file and safer if a local
-- database ever gets rebuilt from scratch.
with assignments (canonical_url, category_name) as (
    values
        ('https://alfred.is/starf/associate-project-manager-5', 'biomedical'),
        ('https://alfred.is/starf/associate-project-manager-5', 'project_management'),
        ('https://alfred.is/starf/associate-project-manager-5', 'r_and_d'),

        ('https://alfred.is/starf/taeknimanneskja-i-framlinuthjonustu', 'it_support'),
        ('https://alfred.is/starf/taeknimanneskja-i-framlinuthjonustu', 'infrastructure'),

        ('https://alfred.is/starf/verkfraedingur-eda-taeknifraedingur-i-idnadardeild', 'engineering'),
        ('https://alfred.is/starf/verkfraedingur-eda-taeknifraedingur-i-idnadardeild', 'automation'),

        ('https://alfred.is/starf/manufacturing-engineer-4', 'engineering'),
        ('https://alfred.is/starf/manufacturing-engineer-4', 'biomedical'),
        ('https://alfred.is/starf/manufacturing-engineer-4', 'manufacturing'),
        ('https://alfred.is/starf/manufacturing-engineer-4', 'quality'),

        ('https://alfred.is/starf/serfraedingur-i-throun-notendalausna-1', 'it_support'),
        ('https://alfred.is/starf/serfraedingur-i-throun-notendalausna-1', 'infrastructure'),
        ('https://alfred.is/starf/serfraedingur-i-throun-notendalausna-1', 'automation'),

        ('https://alfred.is/starf/serfraedingur-i-radiomaelingum-og-fjarskiptainnvidum', 'engineering'),
        ('https://alfred.is/starf/serfraedingur-i-radiomaelingum-og-fjarskiptainnvidum', 'infrastructure'),
        ('https://alfred.is/starf/serfraedingur-i-radiomaelingum-og-fjarskiptainnvidum', 'telecommunications'),
        ('https://alfred.is/starf/serfraedingur-i-radiomaelingum-og-fjarskiptainnvidum', 'data'),

        ('https://alfred.is/starf/serfraedingur-i-vidskiptathroun-og-greiningum', 'data'),
        ('https://alfred.is/starf/serfraedingur-i-vidskiptathroun-og-greiningum', 'business_analysis'),
        ('https://alfred.is/starf/serfraedingur-i-vidskiptathroun-og-greiningum', 'healthcare'),
        ('https://alfred.is/starf/serfraedingur-i-vidskiptathroun-og-greiningum', 'automation'),

        ('https://alfred.is/starf/research-and-development-engineer', 'engineering'),
        ('https://alfred.is/starf/research-and-development-engineer', 'r_and_d'),
        ('https://alfred.is/starf/research-and-development-engineer', 'data'),

        ('https://alfred.is/starf/junior-software-engineer-5', 'software'),
        ('https://alfred.is/starf/junior-software-engineer-5', 'infrastructure'),

        ('https://alfred.is/starf/kjarnakerfisstjori-i-hysingarteymi', 'infrastructure'),
        ('https://alfred.is/starf/kjarnakerfisstjori-i-hysingarteymi', 'it_support'),
        ('https://alfred.is/starf/kjarnakerfisstjori-i-hysingarteymi', 'automation')
)
insert into job_listing_categories (
    job_listing_id,
    category_id,
    source
)
select
    j.id,
    c.id,
    'seed'
from assignments a
join raw_job_listings j on j.canonical_url = a.canonical_url
join job_categories c on c.normalized_name = a.category_name
on conflict (job_listing_id, category_id) do nothing;

