"""Conservative duplicate handling for collected job candidates.

The staging area preserves every source URL. A duplicate decision only changes
which row is shown to the user; it never deletes a source record or invents a
single URL for an opportunity that was advertised in more than one place.
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg.rows import dict_row

from career_radar.db import connect, transaction


@dataclass(frozen=True, slots=True)
class CandidateDuplicateResult:
    """Summary of high-confidence duplicate links created in one pass."""

    linked_count: int


NORMALIZED_CANDIDATES_SQL = """
with normalized as (
    select
        c.id,
        c.company,
        c.title,
        c.url,
        c.location,
        c.posted_date_raw,
        c.deadline_raw,
        regexp_replace(lower(trim(c.company)), '[[:space:]]+', ' ', 'g') as company_key,
        regexp_replace(lower(trim(c.title)), '[[:space:]]+', ' ', 'g') as title_key,
        regexp_replace(lower(trim(coalesce(c.location, ''))), '[[:space:]]+', ' ', 'g') as location_key,
        career_radar_iso_date(c.posted_date_raw) as posted_date,
        career_radar_iso_date(c.deadline_raw) as deadline,
        regexp_replace(
            lower(trim(coalesce(c.description, '') || ' ' || coalesce(c.requirements, ''))),
            '[[:space:]]+', ' ', 'g'
        ) as summary_text
    from collected_job_candidates c
)
"""


@transaction()
def apply_high_confidence_candidate_deduplication() -> CandidateDuplicateResult:
    """Hide only candidate pairs with independently corroborating evidence.

    Equal company/title alone is deliberately *not* enough: an employer can
    advertise the same role again later. Automatic merging requires the same
    valid deadline, the same valid posting date and location, or identical
    substantive listing text. Everything else stays visible for human review.
    """

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                NORMALIZED_CANDIDATES_SQL
                + """
                , pairs as (
                    select
                        left_candidate.id as first_candidate_id,
                        right_candidate.id as second_candidate_id,
                        case
                            when (
                                length(left_candidate.summary_text),
                                length(left_candidate.url),
                                -left_candidate.id
                            ) >= (
                                length(right_candidate.summary_text),
                                length(right_candidate.url),
                                -right_candidate.id
                            ) then left_candidate.id
                            else right_candidate.id
                        end as canonical_candidate_id,
                        case
                            when left_candidate.deadline is not null
                             and left_candidate.deadline = right_candidate.deadline
                                then 'same company and title; same stated deadline'
                            when left_candidate.posted_date is not null
                             and left_candidate.posted_date = right_candidate.posted_date
                             and left_candidate.location_key = right_candidate.location_key
                                then 'same company and title; same posted date and location'
                            else 'same company and title; identical substantive listing text'
                        end as evidence
                    from normalized left_candidate
                    join normalized right_candidate
                      on left_candidate.id < right_candidate.id
                     and left_candidate.company_key = right_candidate.company_key
                     and left_candidate.title_key = right_candidate.title_key
                    left join candidate_duplicate_reviews existing
                      on existing.first_candidate_id = left_candidate.id
                     and existing.second_candidate_id = right_candidate.id
                    where existing.first_candidate_id is null
                      and (
                        (left_candidate.deadline is not null
                         and left_candidate.deadline = right_candidate.deadline)
                        or (
                            left_candidate.posted_date is not null
                            and left_candidate.posted_date = right_candidate.posted_date
                            and left_candidate.location_key <> ''
                            and left_candidate.location_key = right_candidate.location_key
                        )
                        or (
                            length(left_candidate.summary_text) >= 120
                            and left_candidate.summary_text = right_candidate.summary_text
                        )
                      )
                )
                insert into candidate_duplicate_reviews (
                    first_candidate_id,
                    second_candidate_id,
                    decision,
                    canonical_candidate_id,
                    evidence
                )
                select
                    first_candidate_id,
                    second_candidate_id,
                    'auto_merged',
                    canonical_candidate_id,
                    evidence
                from pairs
                on conflict (first_candidate_id, second_candidate_id) do nothing;
                """
            )
            return CandidateDuplicateResult(linked_count=cur.rowcount)


def get_candidate_duplicate_review_rows(limit: int = 20) -> list[dict[str, object]]:
    """Return uncertain same-role pairs that still need a human decision."""

    with connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                NORMALIZED_CANDIDATES_SQL
                + """
                select
                    left_candidate.id as first_candidate_id,
                    right_candidate.id as second_candidate_id,
                    left_candidate.company,
                    left_candidate.title,
                    left_candidate.url as first_url,
                    right_candidate.url as second_url,
                    left_candidate.location as first_location,
                    right_candidate.location as second_location,
                    left_candidate.posted_date as first_posted_date,
                    right_candidate.posted_date as second_posted_date,
                    left_candidate.deadline as first_deadline,
                    right_candidate.deadline as second_deadline,
                    concat_ws(
                        ', ',
                        'same company and title',
                        case when left_candidate.location_key = right_candidate.location_key
                            and left_candidate.location_key <> '' then 'same location' end,
                        case when left_candidate.deadline is not null
                              and right_candidate.deadline is not null then 'different deadlines' end,
                        case when left_candidate.posted_date is not null
                              and right_candidate.posted_date is not null then 'different posted dates' end
                    ) as evidence
                from normalized left_candidate
                join normalized right_candidate
                  on left_candidate.id < right_candidate.id
                 and left_candidate.company_key = right_candidate.company_key
                 and left_candidate.title_key = right_candidate.title_key
                left join candidate_duplicate_reviews reviewed
                  on reviewed.first_candidate_id = left_candidate.id
                 and reviewed.second_candidate_id = right_candidate.id
                where reviewed.first_candidate_id is null
                  and not exists (
                      select 1
                      from candidate_duplicate_reviews hidden_left
                      where hidden_left.decision in ('auto_merged', 'confirmed_duplicate')
                        and hidden_left.canonical_candidate_id <> left_candidate.id
                        and left_candidate.id in (
                            hidden_left.first_candidate_id,
                            hidden_left.second_candidate_id
                        )
                  )
                  and not exists (
                      select 1
                      from candidate_duplicate_reviews hidden_right
                      where hidden_right.decision in ('auto_merged', 'confirmed_duplicate')
                        and hidden_right.canonical_candidate_id <> right_candidate.id
                        and right_candidate.id in (
                            hidden_right.first_candidate_id,
                            hidden_right.second_candidate_id
                        )
                  )
                order by
                    coalesce(left_candidate.deadline, right_candidate.deadline) asc nulls last,
                    left_candidate.company,
                    left_candidate.title
                limit %s;
                """,
                (limit,),
            )
            return list(cur.fetchall())


@transaction()
def decide_candidate_duplicate(
    first_candidate_id: int,
    second_candidate_id: int,
    action: str,
) -> None:
    """Record a human duplicate decision without deleting either source row."""

    first_id, second_id = sorted((first_candidate_id, second_candidate_id))
    if first_id == second_id:
        raise RuntimeError("Choose two different candidate listings.")

    if action == "keep_first":
        decision = "confirmed_duplicate"
        canonical_id: int | None = first_candidate_id
        evidence = "Human review: keep the first listing."
    elif action == "keep_second":
        decision = "confirmed_duplicate"
        canonical_id = second_candidate_id
        evidence = "Human review: keep the second listing."
    elif action == "different_jobs":
        decision = "not_duplicate"
        canonical_id = None
        evidence = "Human review: these are separate opportunities."
    else:
        raise RuntimeError(f"Unsupported duplicate decision: {action}")

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into candidate_duplicate_reviews (
                    first_candidate_id,
                    second_candidate_id,
                    decision,
                    canonical_candidate_id,
                    evidence
                ) values (%s, %s, %s, %s, %s)
                on conflict (first_candidate_id, second_candidate_id) do update
                set
                    decision = excluded.decision,
                    canonical_candidate_id = excluded.canonical_candidate_id,
                    evidence = excluded.evidence,
                    decided_at = now();
                """,
                (first_id, second_id, decision, canonical_id, evidence),
            )
