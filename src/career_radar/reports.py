from __future__ import annotations

from typing import Literal

from career_radar.db import connect


ScoreTrack = Literal[
    "data",
    "biomedical",
    "it",
    "software",
]
GapStatus = Literal[
    "missing",
    "learning",
    "covered",
    "all",
]


TRACK_SCORE_COLUMNS: dict[ScoreTrack, str] = {
    "data": "data_engineering_automation_score",
    "biomedical": "biomedical_engineering_score",
    "it": "it_support_infrastructure_score",
    "software": "software_score",
}


def format_skill_list(skills: list[str] | None, max_items: int = 8) -> str:
    """Format a short skill list for terminal reports."""

    if not skills:
        return "-"

    visible_skills = skills[:max_items]
    remaining_count = len(skills) - len(visible_skills)
    text = ", ".join(visible_skills)

    if remaining_count:
        text += f", +{remaining_count} more"

    return text


def pluralize(count: int, singular: str, plural: str | None = None) -> str:
    """Return a readable singular/plural label for report text."""

    if count == 1:
        return singular

    return plural or f"{singular}s"


def print_duplicate_candidates(limit: int) -> None:
    """Print possible opportunity-level duplicates for human review."""

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    candidate_score,
                    candidate_confidence,
                    evidence,
                    company,
                    title,
                    first_job_listing_id,
                    second_job_listing_id,
                    first_deadline,
                    second_deadline,
                    first_location,
                    second_location,
                    first_url,
                    second_url
                from possible_duplicate_job_listings_v1
                order by
                    candidate_score desc,
                    company,
                    title
                limit %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()

    if not rows:
        print("No possible duplicate job listings found.")
        return

    print(f"Top {len(rows)} possible duplicate job-listing pairs")
    print("=" * 100)

    for row in rows:
        (
            candidate_score,
            candidate_confidence,
            evidence,
            company,
            title,
            first_job_listing_id,
            second_job_listing_id,
            first_deadline,
            second_deadline,
            first_location,
            second_location,
            first_url,
            second_url,
        ) = row

        print(f"{company} - {title}")
        print(f"    confidence: {candidate_confidence} ({candidate_score})")
        print(f"    evidence: {evidence}")
        print(
            f"    first: [{first_job_listing_id}] "
            f"{first_location or 'location missing'}, "
            f"deadline {first_deadline or 'missing'}"
        )
        print(
            f"    second: [{second_job_listing_id}] "
            f"{second_location or 'location missing'}, "
            f"deadline {second_deadline or 'missing'}"
        )
        print(f"    urls: {first_url} | {second_url}")
        print()


def print_job_skill_matches(track: ScoreTrack, limit: int) -> None:
    """Print top jobs with skill-profile coverage details."""

    score_column = TRACK_SCORE_COLUMNS[track]

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select
                    job_listing_id,
                    company,
                    title,
                    deadline,
                    {score_column} as track_score,
                    skill_coverage_score,
                    requested_skill_count,
                    covered_skill_count,
                    learning_skill_count,
                    gap_skill_count,
                    covered_skills,
                    learning_skills,
                    gap_skills
                from job_skill_match_v1
                order by
                    {score_column} desc,
                    skill_coverage_score desc,
                    deadline nulls last,
                    company,
                    title
                limit %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()

    print(f"Top {len(rows)} job skill matches for track: {track}")
    print("=" * 100)

    for row in rows:
        (
            job_listing_id,
            company,
            title,
            deadline,
            track_score,
            skill_coverage_score,
            requested_skill_count,
            covered_skill_count,
            learning_skill_count,
            gap_skill_count,
            covered_skills,
            learning_skills,
            gap_skills,
        ) = row

        print(f"[{job_listing_id}] {company} - {title}")
        print(f"    track score: {track_score}")
        print(f"    skill coverage: {skill_coverage_score}%")
        print(
            "    counts: "
            f"{covered_skill_count} covered, "
            f"{learning_skill_count} learning, "
            f"{gap_skill_count} gaps, "
            f"{requested_skill_count} requested"
        )
        print(f"    deadline: {deadline or 'no stated deadline'}")
        print(f"    covered: {format_skill_list(covered_skills)}")
        print(f"    learning: {format_skill_list(learning_skills)}")
        print(f"    gaps: {format_skill_list(gap_skills)}")
        print()


def print_skill_gaps(status: GapStatus, limit: int) -> None:
    """Print recurring requested skills by profile status."""

    status_filter_sql = ""
    params: list[object] = []
    if status != "all":
        status_filter_sql = "where profile_status = %s"
        params.append(status)

    params.append(limit)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select
                    normalized_name,
                    jobs_requesting,
                    demand_percent,
                    profile_status,
                    proficiency_level,
                    example_jobs
                from skill_gap_summary_v1
                {status_filter_sql}
                order by
                    case profile_status
                        when 'missing' then 1
                        when 'learning' then 2
                        else 3
                    end,
                    jobs_requesting desc,
                    normalized_name
                limit %s;
                """,
                params,
            )
            rows = cur.fetchall()

    if not rows:
        print(f"No skill gaps found for status: {status}")
        return

    print(f"Top {len(rows)} skills by demand for status: {status}")
    print("=" * 100)

    for row in rows:
        (
            normalized_name,
            jobs_requesting,
            demand_percent,
            profile_status,
            proficiency_level,
            example_jobs,
        ) = row

        level_text = proficiency_level or "-"
        job_word = pluralize(jobs_requesting, "job")
        print(f"{normalized_name}")
        print(
            f"    demand: {jobs_requesting} {job_word} "
            f"({demand_percent}% of current listings)"
        )
        print(f"    profile status: {profile_status} / {level_text}")
        print(f"    examples: {format_skill_list(example_jobs, max_items=3)}")
        print()


def print_top_jobs(track: ScoreTrack, limit: int) -> None:
    """Print the highest-priority jobs for one scoring track.

    The enriched view keeps this report simple. It already contains one row per
    job with categories, skills, application status, and score columns.
    """

    score_column = TRACK_SCORE_COLUMNS[track]

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
                    next_action,
                    cleaned_categories,
                    {score_column} as score
                from job_listings_enriched_v1
                order by
                    {score_column} desc,
                    deadline nulls last,
                    company,
                    title
                limit %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()

    print(f"Top {len(rows)} jobs for track: {track}")
    print("=" * 100)

    for row in rows:
        (
            job_listing_id,
            company,
            title,
            deadline,
            application_status,
            next_action,
            cleaned_categories,
            score,
        ) = row

        print(f"[{job_listing_id}] {company} - {title}")
        print(f"    score: {score}")
        print(f"    deadline: {deadline or 'no stated deadline'}")
        print(f"    status: {application_status}")
        print(f"    next action: {next_action or '-'}")
        print(f"    categories: {cleaned_categories or '-'}")
        print()
