from __future__ import annotations

from datetime import date
from typing import Literal

from career_radar.db import connect, transaction
from career_radar.reports import ScoreTrack, TRACK_SCORE_COLUMNS, format_skill_list, pluralize


ApplicationStatus = Literal[
    "interested",
    "preparing",
    "applied",
    "interviewing",
    "rejected",
    "tracking_only",
    "not_applying",
    "withdrawn",
    "offer",
]
ApplicationListFilter = Literal[
    "tracked",
    "active",
    "all",
    "not_started",
    ApplicationStatus,
]

APPLICATION_STATUSES = (
    "interested",
    "preparing",
    "applied",
    "interviewing",
    "rejected",
    "tracking_only",
    "not_applying",
    "withdrawn",
    "offer",
)
APPLICATION_LIST_FILTERS = (
    "tracked",
    "active",
    "all",
    "not_started",
    *APPLICATION_STATUSES,
)
ACTIVE_APPLICATION_STATUSES = (
    "interested",
    "preparing",
    "applied",
    "interviewing",
    "offer",
)


def parse_optional_date(value: str | None, field_name: str) -> date | None:
    """Parse an optional ISO date string used by application commands."""

    if not value:
        return None

    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise RuntimeError(
            f"{field_name} must use YYYY-MM-DD format, got: {value}"
        ) from error


def print_applications(status: ApplicationListFilter, limit: int) -> None:
    """Print application workflow rows or all listings by application status."""

    where_sql = ""
    params: list[object] = []

    if status == "tracked":
        where_sql = "where application_status <> 'not_started'"
    elif status == "active":
        where_sql = "where application_status = any(%s)"
        params.append(list(ACTIVE_APPLICATION_STATUSES))
    elif status == "all":
        where_sql = ""
    else:
        where_sql = "where application_status = %s"
        params.append(status)

    params.append(limit)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select
                    job_listing_id,
                    company,
                    title,
                    deadline,
                    application_status,
                    applied_on,
                    follow_up_date,
                    next_action,
                    cv_version,
                    cover_letter_version
                from job_listings_enriched_v1
                {where_sql}
                order by
                    case application_status
                        when 'interested' then 1
                        when 'preparing' then 2
                        when 'applied' then 3
                        when 'interviewing' then 4
                        when 'offer' then 5
                        when 'not_started' then 6
                        when 'tracking_only' then 7
                        when 'rejected' then 8
                        when 'withdrawn' then 9
                        else 10
                    end,
                    coalesce(follow_up_date, deadline) nulls last,
                    company,
                    title
                limit %s;
                """,
                params,
            )
            rows = cur.fetchall()

    if not rows:
        print(f"No applications found for status: {status}")
        return

    print(f"Applications: {status}")
    print("=" * 100)
    for row in rows:
        (
            job_listing_id,
            company,
            title,
            deadline,
            application_status,
            applied_on,
            follow_up_date,
            next_action,
            cv_version,
            cover_letter_version,
        ) = row

        print(f"[{job_listing_id}] {company} - {title}")
        print(f"    status: {application_status}")
        print(f"    deadline: {deadline or 'no stated deadline'}")
        print(f"    applied on: {applied_on or '-'}")
        print(f"    follow up: {follow_up_date or '-'}")
        print(f"    next action: {next_action or '-'}")
        print(f"    cv / cover letter: {cv_version or '-'} / {cover_letter_version or '-'}")
        print()


def print_application_queue(
    track: ScoreTrack,
    limit: int,
    include_expired: bool,
) -> None:
    """Print ranked jobs to consider for application work."""

    score_column = TRACK_SCORE_COLUMNS[track]
    expired_filter_sql = "" if include_expired else "and is_active_listing"

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select
                    job_listing_id,
                    company,
                    title,
                    deadline,
                    listing_status,
                    application_status,
                    next_action,
                    {score_column} as track_score,
                    skill_coverage_score,
                    gap_skill_count,
                    gap_skills,
                    (
                        ({score_column} * 10)
                        + (skill_coverage_score * 0.4)
                        + case
                            when days_until_deadline is null then 0
                            when days_until_deadline < 0 then -30
                            when days_until_deadline <= 1 then 25
                            when days_until_deadline <= 3 then 18
                            when days_until_deadline <= 7 then 12
                            when days_until_deadline <= 14 then 6
                            else 0
                          end
                        + case application_status
                            when 'preparing' then 12
                            when 'interested' then 8
                            else 0
                          end
                    ) as queue_score
                from job_skill_match_v1
                where application_status in ('not_started', 'interested', 'preparing')
                  {expired_filter_sql}
                order by
                    queue_score desc,
                    deadline nulls last,
                    company,
                    title
                limit %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()

    if not rows:
        print(f"No application queue items found for track: {track}")
        return

    suffix = "including expired listings" if include_expired else "active listings only"
    print(f"Application queue for track: {track} ({suffix})")
    print("=" * 100)
    for row in rows:
        (
            job_listing_id,
            company,
            title,
            deadline,
            listing_status,
            application_status,
            next_action,
            track_score,
            skill_coverage_score,
            gap_skill_count,
            gap_skills,
            queue_score,
        ) = row

        print(f"[{job_listing_id}] {company} - {title}")
        print(f"    queue score: {round(queue_score, 1)}")
        print(f"    track score: {track_score}")
        print(f"    skill coverage: {skill_coverage_score}%")
        print(f"    gaps: {gap_skill_count} ({format_skill_list(gap_skills, max_items=5)})")
        print(f"    listing: {listing_status}, deadline {deadline or 'no stated deadline'}")
        print(f"    application: {application_status}")
        print(f"    next action: {next_action or '-'}")
        print()


@transaction()
def set_application(
    job_listing_id: int,
    status: ApplicationStatus,
    applied_on: str | None,
    application_url: str | None,
    cv_version: str | None,
    cover_letter_version: str | None,
    follow_up_date: str | None,
    next_action: str | None,
    notes: str | None,
    *,
    overwrite_existing: bool = True,
) -> bool:
    """Save workflow fields, or only create a missing row; return whether saved."""

    if status not in APPLICATION_STATUSES:
        raise RuntimeError(f"Unsupported application status: {status}")

    parsed_applied_on = parse_optional_date(applied_on, "applied_on")
    parsed_follow_up_date = parse_optional_date(follow_up_date, "follow_up_date")

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select company, title
                from job_listings_enriched_v1
                where job_listing_id = %s;
                """,
                (job_listing_id,),
            )
            job_row = cur.fetchone()
            if job_row is None:
                raise RuntimeError(f"No job listing found with id {job_listing_id}.")

            company, title = job_row

            cur.execute(
                """
                insert into job_applications (
                    job_listing_id,
                    status,
                    applied_on,
                    application_url,
                    cv_version,
                    cover_letter_version,
                    follow_up_date,
                    next_action,
                    notes
                ) values (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                on conflict (job_listing_id) do update
                set
                    status = excluded.status,
                    applied_on = coalesce(excluded.applied_on, job_applications.applied_on),
                    application_url = coalesce(excluded.application_url, job_applications.application_url),
                    cv_version = coalesce(excluded.cv_version, job_applications.cv_version),
                    cover_letter_version = coalesce(excluded.cover_letter_version, job_applications.cover_letter_version),
                    follow_up_date = coalesce(excluded.follow_up_date, job_applications.follow_up_date),
                    next_action = coalesce(excluded.next_action, job_applications.next_action),
                    notes = coalesce(excluded.notes, job_applications.notes),
                    updated_at = now()
                where %s;
                """,
                (
                    job_listing_id,
                    status,
                    parsed_applied_on,
                    application_url,
                    cv_version,
                    cover_letter_version,
                    parsed_follow_up_date,
                    next_action,
                    notes,
                    overwrite_existing,
                ),
            )
            saved = cur.rowcount > 0

    if saved:
        print(f"Application saved: [{job_listing_id}] {company} - {title} ({status})")
    return saved


def remove_application(job_listing_id: int) -> None:
    """Remove the workflow row for one job without deleting the job listing."""

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                delete from job_applications
                where job_listing_id = %s;
                """,
                (job_listing_id,),
            )
            removed_count = cur.rowcount

    if removed_count:
        print(f"Application workflow row removed for job {job_listing_id}.")
    else:
        print(f"No application workflow row existed for job {job_listing_id}.")


def print_due_applications(days: int, limit: int, include_overdue: bool) -> None:
    """Print application actions and deadlines due within the given window."""

    if days < 0:
        raise RuntimeError("days must be zero or greater.")

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    job_listing_id,
                    company,
                    title,
                    deadline,
                    application_status,
                    follow_up_date,
                    next_action,
                    case
                        when follow_up_date is not null
                         and follow_up_date <= current_date + (%s::int * interval '1 day')
                            then 'follow_up'
                        when deadline is not null
                         and deadline <= current_date + (%s::int * interval '1 day')
                            then 'deadline'
                        else 'review'
                    end as due_reason
                from job_listings_enriched_v1
                where (
                    follow_up_date is not null
                    and follow_up_date <= current_date + (%s::int * interval '1 day')
                    and application_status not in (
                        'rejected', 'withdrawn', 'offer', 'tracking_only', 'not_applying'
                    )
                )
                or (
                    deadline is not null
                    and (%s or deadline >= current_date)
                    and deadline <= current_date + (%s::int * interval '1 day')
                    and application_status not in (
                        'applied',
                        'interviewing',
                        'rejected',
                        'withdrawn',
                        'offer',
                        'tracking_only',
                        'not_applying'
                    )
                )
                order by
                    least(
                        coalesce(follow_up_date, date '9999-12-31'),
                        coalesce(deadline, date '9999-12-31')
                    ),
                    case application_status
                        when 'preparing' then 1
                        when 'interested' then 2
                        when 'not_started' then 3
                        else 4
                    end,
                    company,
                    title
                limit %s;
                """,
                (days, days, days, include_overdue, days, limit),
            )
            rows = cur.fetchall()

    if not rows:
        print(f"No application actions due in the next {days} days.")
        return

    day_word = pluralize(days, "day")
    if include_overdue:
        print(f"Application actions due or overdue within {days} {day_word}")
    else:
        print(f"Application actions due within {days} {day_word}")
    print("=" * 100)
    for row in rows:
        (
            job_listing_id,
            company,
            title,
            deadline,
            application_status,
            follow_up_date,
            next_action,
            due_reason,
        ) = row

        print(f"[{job_listing_id}] {company} - {title}")
        print(f"    reason: {due_reason}")
        print(f"    status: {application_status}")
        print(f"    deadline: {deadline or 'no stated deadline'}")
        print(f"    follow up: {follow_up_date or '-'}")
        print(f"    next action: {next_action or '-'}")
        print()
