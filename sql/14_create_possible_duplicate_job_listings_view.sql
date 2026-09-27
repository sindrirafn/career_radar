-- Possible opportunity-level duplicates.
--
-- canonical_url deduplication is safe and automatic because it handles one
-- source page appearing in slightly different URL forms. This view handles the
-- less certain case: different URLs that may still represent the same real job
-- opportunity. These rows are review candidates, not automatic merges.
drop view if exists possible_duplicate_job_listings_v1;

create view possible_duplicate_job_listings_v1 as
with normalized as (
    select
        id as job_listing_id,
        company,
        title,
        source,
        url,
        canonical_url,
        location,
        posted_date,
        deadline,
        source_file,
        source_row_number,
        first_seen_at,
        last_seen_at,
        regexp_replace(lower(trim(company)), '[[:space:]]+', ' ', 'g') as normalized_company,
        regexp_replace(lower(trim(title)), '[[:space:]]+', ' ', 'g') as normalized_title,
        regexp_replace(
            lower(trim(coalesce(description, '') || ' ' || coalesce(requirements, ''))),
            '[[:space:]]+',
            ' ',
            'g'
        ) as normalized_summary_text
    from job_listings_v1
),
candidate_pairs as (
    select
        first_listing.job_listing_id as first_job_listing_id,
        second_listing.job_listing_id as second_job_listing_id,
        first_listing.company,
        first_listing.title,
        first_listing.normalized_company,
        first_listing.normalized_title,
        first_listing.source as first_source,
        second_listing.source as second_source,
        first_listing.url as first_url,
        second_listing.url as second_url,
        first_listing.canonical_url as first_canonical_url,
        second_listing.canonical_url as second_canonical_url,
        first_listing.location as first_location,
        second_listing.location as second_location,
        first_listing.posted_date as first_posted_date,
        second_listing.posted_date as second_posted_date,
        first_listing.deadline as first_deadline,
        second_listing.deadline as second_deadline,
        first_listing.source_file as first_source_file,
        second_listing.source_file as second_source_file,
        first_listing.source_row_number as first_source_row_number,
        second_listing.source_row_number as second_source_row_number,
        first_listing.first_seen_at as first_seen_at,
        second_listing.first_seen_at as second_seen_at,
        case
            when first_listing.posted_date is not null
             and second_listing.posted_date is not null
                then abs(first_listing.posted_date - second_listing.posted_date)
            else null
        end as posted_date_gap_days,
        case
            when first_listing.deadline is not null
             and second_listing.deadline is not null
                then abs(first_listing.deadline - second_listing.deadline)
            else null
        end as deadline_gap_days,
        first_listing.deadline is not null
            and second_listing.deadline is not null
            and first_listing.deadline = second_listing.deadline
            as same_deadline,
        first_listing.posted_date is not null
            and second_listing.posted_date is not null
            and first_listing.posted_date = second_listing.posted_date
            as same_posted_date,
        first_listing.location is not null
            and second_listing.location is not null
            and regexp_replace(lower(trim(first_listing.location)), '[[:space:]]+', ' ', 'g')
              = regexp_replace(lower(trim(second_listing.location)), '[[:space:]]+', ' ', 'g')
            as same_location,
        length(first_listing.normalized_summary_text) >= 80
            and first_listing.normalized_summary_text = second_listing.normalized_summary_text
            as same_summary_text
    from normalized first_listing
    join normalized second_listing
      on first_listing.job_listing_id < second_listing.job_listing_id
     and first_listing.normalized_company = second_listing.normalized_company
     and first_listing.normalized_title = second_listing.normalized_title
     and first_listing.canonical_url <> second_listing.canonical_url
),
scored as (
    select
        *,
        deadline_gap_days is not null and deadline_gap_days <= 14 as close_deadline,
        posted_date_gap_days is not null and posted_date_gap_days <= 14 as close_posted_date,
        first_deadline is null
            and second_deadline is null
            and first_posted_date is null
            and second_posted_date is null
            as missing_date_evidence
    from candidate_pairs
),
final_scored as (
    select
        *,
        2
        + case
            when same_deadline then 4
            when close_deadline then 2
            else 0
          end
        + case
            when same_posted_date then 4
            when close_posted_date then 2
            else 0
          end
        + case when same_location then 2 else 0 end
        + case when same_summary_text then 4 else 0 end
        + case when missing_date_evidence then 1 else 0 end
            as candidate_score
    from scored
)
select
    first_job_listing_id,
    second_job_listing_id,
    company,
    title,
    normalized_company,
    normalized_title,
    first_source,
    second_source,
    first_location,
    second_location,
    first_posted_date,
    second_posted_date,
    posted_date_gap_days,
    first_deadline,
    second_deadline,
    deadline_gap_days,
    first_url,
    second_url,
    first_canonical_url,
    second_canonical_url,
    first_source_file,
    second_source_file,
    first_source_row_number,
    second_source_row_number,
    first_seen_at,
    second_seen_at,
    same_deadline,
    same_posted_date,
    same_location,
    same_summary_text,
    close_deadline,
    close_posted_date,
    missing_date_evidence,
    candidate_score,
    case
        when candidate_score >= 9 then 'high'
        when candidate_score >= 5 then 'medium'
        else 'review'
    end as candidate_confidence,
    concat_ws(
        ', ',
        'same company/title',
        case when same_deadline then 'same deadline' end,
        case when close_deadline and not same_deadline then 'close deadlines' end,
        case when same_posted_date then 'same posted date' end,
        case when close_posted_date and not same_posted_date then 'close posted dates' end,
        case when same_location then 'same location' end,
        case when same_summary_text then 'same description/requirements text' end,
        case when missing_date_evidence then 'both missing dates' end
    ) as evidence
from final_scored
where
    same_deadline
    or close_deadline
    or same_posted_date
    or close_posted_date
    or same_summary_text
    or missing_date_evidence;
