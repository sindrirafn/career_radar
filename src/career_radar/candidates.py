from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from career_radar.candidate_deduplication import apply_high_confidence_candidate_deduplication
from career_radar.db import connect, transaction
from career_radar.ingestion import canonicalize_url, ingest_job_listings
from career_radar.listings import JobListingInput
from career_radar.normalization import normalize_skill_name
from career_radar.relevance import RelevanceResult, score_listing_relevance


CandidateLabelFilter = Literal[
    "actionable",
    "strong",
    "possible",
    "irrelevant",
    "hard-negative",
    "all",
]
CandidateReviewStatus = Literal[
    "unreviewed",
    "approved",
    "rejected",
    "promoted",
]

CANDIDATE_LABEL_FILTERS = (
    "actionable",
    "strong",
    "possible",
    "irrelevant",
    "hard-negative",
    "all",
)
CANDIDATE_REVIEW_STATUSES = (
    "unreviewed",
    "approved",
    "rejected",
    "promoted",
)

# These mappings are deliberately conservative. AI tracks describe strategic fit,
# while categories feed analytics and dashboards. Only the tracks that cleanly
# translate into controlled categories are assigned automatically during
# promotion; fuzzier tracks remain review evidence instead of polluting analysis.
AI_TRACK_CATEGORY_NAMES = {
    "engineering": ("engineering",),
    "biomedical_healthtech": ("biomedical", "healthcare"),
    "data_automation": ("data", "automation"),
}

LABEL_WHERE_SQL = {
    "actionable": """
        where rule_relevance_label in ('strong_candidate', 'possible_candidate')
          and review_status in ('unreviewed', 'approved')
    """,
    "strong": "where rule_relevance_label = 'strong_candidate'",
    "possible": "where rule_relevance_label = 'possible_candidate'",
    "irrelevant": "where rule_relevance_label = 'likely_irrelevant'",
    "hard-negative": "where rule_relevance_label = 'hard_negative'",
    "all": "",
}

INSERT_CANDIDATE_SQL = """
insert into collected_job_candidates (
    source,
    title,
    company,
    url,
    canonical_url,
    location,
    posted_date_raw,
    deadline_raw,
    category_guess,
    description,
    requirements,
    skills_mentioned,
    notes,
    rule_relevance_score,
    rule_relevance_label,
    matched_tracks,
    matched_keywords,
    hard_negative_flags,
    needs_ai_review
) values (
    %(source)s,
    %(title)s,
    %(company)s,
    %(url)s,
    %(canonical_url)s,
    %(location)s,
    %(posted_date_raw)s,
    %(deadline_raw)s,
    %(category_guess)s,
    %(description)s,
    %(requirements)s,
    %(skills_mentioned)s,
    %(notes)s,
    %(rule_relevance_score)s,
    %(rule_relevance_label)s,
    %(matched_tracks)s,
    %(matched_keywords)s,
    %(hard_negative_flags)s,
    %(needs_ai_review)s
)
on conflict (canonical_url) do update
set
    source = excluded.source,
    title = excluded.title,
    company = excluded.company,
    url = excluded.url,
    location = excluded.location,
    posted_date_raw = excluded.posted_date_raw,
    deadline_raw = excluded.deadline_raw,
    category_guess = excluded.category_guess,
    description = excluded.description,
    requirements = excluded.requirements,
    skills_mentioned = excluded.skills_mentioned,
    notes = excluded.notes,
    rule_relevance_score = excluded.rule_relevance_score,
    rule_relevance_label = excluded.rule_relevance_label,
    matched_tracks = excluded.matched_tracks,
    matched_keywords = excluded.matched_keywords,
    hard_negative_flags = excluded.hard_negative_flags,
    needs_ai_review = excluded.needs_ai_review,
    last_seen_at = now(),
    updated_at = now()
returning id, rule_relevance_label;
"""


def merge_skill_text(
    existing_skills_text: str | None,
    additional_skills: list[str] | None,
) -> str | None:
    """Merge semicolon-separated source skills with reviewed AI explicit skills."""

    skill_values = []
    if existing_skills_text:
        skill_values.extend(existing_skills_text.split(";"))
    if additional_skills:
        skill_values.extend(additional_skills)

    merged_skills = []
    seen_normalized_names = set()
    for raw_skill in skill_values:
        skill = raw_skill.strip()
        normalized_name = normalize_skill_name(skill)
        if not normalized_name or normalized_name in seen_normalized_names:
            continue

        merged_skills.append(skill)
        seen_normalized_names.add(normalized_name)

    if not merged_skills:
        return None

    return "; ".join(merged_skills)


def latest_candidate_ai_context(candidate_id: int) -> dict[str, object] | None:
    """Load the latest AI context for a staged candidate, if one exists."""

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    ai_relevance_label,
                    ai_fit_tier,
                    ai_fit_score,
                    ai_priority_score,
                    ai_tracks,
                    ai_explicit_skills,
                    recommended_action,
                    ai_evidence
                from collected_job_candidates_ai_v1
                where id = %s
                  and ai_enrichment_id is not null;
                """,
                (candidate_id,),
            )
            row = cur.fetchone()

    if row is None:
        return None

    (
        ai_relevance_label,
        ai_fit_tier,
        ai_fit_score,
        ai_priority_score,
        ai_tracks,
        ai_explicit_skills,
        recommended_action,
        ai_evidence,
    ) = row

    return {
        "ai_relevance_label": ai_relevance_label,
        "ai_fit_tier": ai_fit_tier,
        "ai_fit_score": ai_fit_score,
        "ai_priority_score": ai_priority_score,
        "ai_tracks": list(ai_tracks or []),
        "ai_explicit_skills": list(ai_explicit_skills or []),
        "recommended_action": recommended_action,
        "ai_evidence": ai_evidence,
    }


def ai_category_names(ai_context: dict[str, object] | None) -> list[str]:
    """Translate clean AI strategic tracks into controlled category names."""

    if ai_context is None:
        return []

    category_names = []
    seen_category_names = set()
    for track in ai_context.get("ai_tracks", []):
        if not isinstance(track, str):
            continue

        for category_name in AI_TRACK_CATEGORY_NAMES.get(track, ()):
            if category_name in seen_category_names:
                continue

            category_names.append(category_name)
            seen_category_names.add(category_name)

    return category_names


def assign_ai_categories(
    cursor,
    job_listing_id: int,
    ai_context: dict[str, object] | None,
) -> list[str]:
    """Add conservative model-supported categories to a promoted listing."""

    category_names = ai_category_names(ai_context)
    if not category_names:
        return []

    cursor.execute(
        """
        insert into job_listing_categories (
            job_listing_id,
            category_id,
            source
        )
        select
            %s,
            c.id,
            'model'
        from job_categories c
        where c.normalized_name = any(%s)
        on conflict (job_listing_id, category_id) do nothing;
        """,
        (job_listing_id, category_names),
    )

    return category_names


@dataclass(frozen=True, slots=True)
class CandidateSaveResult:
    """Summary returned after collected listings are saved to staging."""

    processed_count: int
    strong_count: int
    possible_count: int
    irrelevant_count: int
    hard_negative_count: int
    needs_ai_review_count: int


@dataclass(frozen=True, slots=True)
class CandidatePromotionResult:
    """Summary returned after one staged candidate is promoted."""

    candidate_id: int
    job_listing_id: int
    company: str
    title: str
    ai_context: dict[str, object] | None
    category_names: list[str]


def candidate_params(
    listing: JobListingInput,
    relevance: RelevanceResult,
) -> dict[str, object]:
    """Convert a collected listing and relevance result into SQL parameters."""

    return {
        "source": listing.source,
        "title": listing.title,
        "company": listing.company,
        "url": listing.url,
        "canonical_url": canonicalize_url(listing.url),
        "location": listing.location,
        "posted_date_raw": listing.posted_date,
        "deadline_raw": listing.deadline,
        "category_guess": listing.category_guess,
        "description": listing.description,
        "requirements": listing.requirements,
        "skills_mentioned": listing.skills_mentioned,
        "notes": listing.notes,
        "rule_relevance_score": relevance.score,
        "rule_relevance_label": relevance.label,
        "matched_tracks": relevance.matched_tracks,
        "matched_keywords": relevance.matched_keywords,
        "hard_negative_flags": relevance.hard_negative_flags,
        "needs_ai_review": relevance.needs_ai_review,
    }


@transaction()
def save_collected_candidates(listings: list[JobListingInput]) -> CandidateSaveResult:
    """Save collected listings to the staging table with relevance signals."""

    if not listings:
        raise RuntimeError("No collected listings were provided.")

    label_counts = {
        "strong_candidate": 0,
        "possible_candidate": 0,
        "likely_irrelevant": 0,
        "hard_negative": 0,
    }
    needs_ai_review_count = 0

    with connect() as conn:
        with conn.cursor() as cur:
            for listing in listings:
                relevance = score_listing_relevance(listing)
                cur.execute(INSERT_CANDIDATE_SQL, candidate_params(listing, relevance))
                _candidate_id, label = cur.fetchone()
                label_counts[label] += 1
                if relevance.needs_ai_review:
                    needs_ai_review_count += 1

    # URL duplicates have already been handled by the upsert above. This second
    # pass links only strongly corroborated cross-source copies of a vacancy.
    apply_high_confidence_candidate_deduplication()

    return CandidateSaveResult(
        processed_count=len(listings),
        strong_count=label_counts["strong_candidate"],
        possible_count=label_counts["possible_candidate"],
        irrelevant_count=label_counts["likely_irrelevant"],
        hard_negative_count=label_counts["hard_negative"],
        needs_ai_review_count=needs_ai_review_count,
    )


def print_candidate_save_result(result: CandidateSaveResult) -> None:
    """Print a compact summary after saving collected candidates."""

    print(f"Saved {result.processed_count} collected listings to candidate staging.")
    print(f"    strong candidates: {result.strong_count}")
    print(f"    possible candidates: {result.possible_count}")
    print(f"    likely irrelevant: {result.irrelevant_count}")
    print(f"    hard negatives: {result.hard_negative_count}")
    print(f"    need AI review: {result.needs_ai_review_count}")


def print_candidates(label: CandidateLabelFilter, limit: int) -> None:
    """Print staged candidates by rule label/review state."""

    where_sql = LABEL_WHERE_SQL[label]

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select
                    id,
                    rule_relevance_score,
                    rule_relevance_label,
                    review_status,
                    needs_ai_review,
                    matched_tracks,
                    matched_keywords,
                    hard_negative_flags,
                    company,
                    title,
                    deadline,
                    listing_status,
                    location,
                    url
                from collected_job_candidates_ranked_v1
                {where_sql}
                order by
                    case rule_relevance_label
                        when 'strong_candidate' then 1
                        when 'possible_candidate' then 2
                        when 'hard_negative' then 3
                        else 4
                    end,
                    rule_relevance_score desc,
                    deadline nulls last,
                    last_seen_at desc
                limit %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()

    if not rows:
        print(f"No collected candidates found for filter: {label}")
        return

    print(f"Collected candidates: {label}")
    print("=" * 100)

    for row in rows:
        (
            candidate_id,
            score,
            relevance_label,
            review_status,
            needs_ai_review,
            matched_tracks,
            matched_keywords,
            hard_negative_flags,
            company,
            title,
            deadline,
            listing_status,
            location,
            url,
        ) = row

        ai_text = "yes" if needs_ai_review else "no"
        print(f"[{candidate_id}] {company} - {title}")
        print(
            f"    rule score: {score} ({relevance_label}), "
            f"review: {review_status}, ai review: {ai_text}"
        )
        print(f"    tracks: {', '.join(matched_tracks) or '-'}")
        print(f"    keywords: {', '.join(matched_keywords[:10]) or '-'}")
        if hard_negative_flags:
            print(f"    flags: {', '.join(hard_negative_flags)}")
        print(
            f"    deadline: {deadline or 'no stated deadline'} "
            f"({listing_status})"
        )
        print(f"    location: {location or '-'}")
        print(f"    url: {url}")
        print()


@transaction()
def set_candidate_review_status(
    candidate_id: int,
    review_status: CandidateReviewStatus,
) -> None:
    """Update the human review status for one collected candidate."""

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update collected_job_candidates
                set
                    review_status = %s,
                    updated_at = now()
                where id = %s
                returning title, company;
                """,
                (review_status, candidate_id),
            )
            row = cur.fetchone()

    if row is None:
        print(f"Candidate not found: {candidate_id}")
        return

    title, company = row
    print(f"Candidate {candidate_id} set to {review_status}: {company} - {title}")


def candidate_listing_input(
    candidate_id: int,
    include_ai_explicit_skills: bool = False,
) -> JobListingInput | None:
    """Load one staged candidate as a standard listing input."""

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    title,
                    company,
                    source,
                    url,
                    location,
                    posted_date_raw,
                    deadline_raw,
                    category_guess,
                    description,
                    requirements,
                    skills_mentioned,
                    notes
                from collected_job_candidates
                where id = %s;
                """,
                (candidate_id,),
            )
            row = cur.fetchone()

    if row is None:
        return None

    (
        title,
        company,
        source,
        url,
        location,
        posted_date_raw,
        deadline_raw,
        category_guess,
        description,
        requirements,
        skills_mentioned,
        notes,
    ) = row

    ai_context = None
    if include_ai_explicit_skills:
        ai_context = latest_candidate_ai_context(candidate_id)

    ai_explicit_skills = None
    if ai_context is not None:
        ai_explicit_skills = ai_context["ai_explicit_skills"]

    return JobListingInput(
        title=title,
        company=company,
        source=source,
        url=url,
        location=location,
        posted_date=posted_date_raw,
        deadline=deadline_raw,
        category_guess=category_guess,
        description=description,
        requirements=requirements,
        skills_mentioned=merge_skill_text(skills_mentioned, ai_explicit_skills),
        notes=notes,
    )


@transaction()
def promote_candidate_to_listing(candidate_id: int) -> CandidatePromotionResult:
    """Promote one staged candidate and return the created/updated listing."""

    ai_context = latest_candidate_ai_context(candidate_id)
    listing = candidate_listing_input(
        candidate_id,
        include_ai_explicit_skills=True,
    )
    if listing is None:
        raise RuntimeError(f"Candidate not found: {candidate_id}")

    ingest_job_listings([listing])
    canonical_url = canonicalize_url(listing.url)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id
                from raw_job_listings
                where canonical_url = %s;
                """,
                (canonical_url,),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("Promoted listing was not found after ingestion.")

            job_listing_id = row[0]
            category_names = assign_ai_categories(cur, job_listing_id, ai_context)
            cur.execute(
                """
                update collected_job_candidates
                set
                    review_status = 'promoted',
                    promoted_job_listing_id = %s,
                    updated_at = now()
                where id = %s;
                """,
                (job_listing_id, candidate_id),
            )

    return CandidatePromotionResult(
        candidate_id=candidate_id,
        job_listing_id=job_listing_id,
        company=listing.company,
        title=listing.title,
        ai_context=ai_context,
        category_names=category_names,
    )


def promote_candidate(candidate_id: int) -> None:
    """Promote one staged candidate into the curated listing tables."""

    result = promote_candidate_to_listing(candidate_id)

    print(
        f"Promoted candidate {candidate_id} to raw job listing "
        f"{result.job_listing_id}: {result.company} - {result.title}"
    )
    if result.ai_context is not None:
        explicit_skills = result.ai_context["ai_explicit_skills"]
        recommended_action = result.ai_context["recommended_action"]
        print(
            f"    AI review: {result.ai_context['ai_relevance_label']} / "
            f"{result.ai_context['ai_fit_tier']} "
            f"fit={result.ai_context['ai_fit_score']} "
            f"priority={result.ai_context['ai_priority_score']}"
        )
        if recommended_action not in {"review_for_promotion", "manual_review"}:
            print(f"    Note: AI recommended action was {recommended_action}.")
        if explicit_skills:
            print(f"    Added explicit AI skills: {', '.join(explicit_skills)}")
        if result.category_names:
            print(
                "    Added AI-supported categories: "
                f"{', '.join(result.category_names)}"
            )
