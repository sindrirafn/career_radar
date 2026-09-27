from __future__ import annotations

import argparse
import json
import os
from datetime import date
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlencode, urlparse

from psycopg.rows import dict_row

from career_radar.applications import APPLICATION_STATUSES, set_application
from career_radar.candidate_deduplication import (
    decide_candidate_duplicate,
    get_candidate_duplicate_review_rows,
)
from career_radar.candidates import promote_candidate_to_listing
from career_radar.db import connect, transaction
from career_radar.sources.chatgpt_inbox import import_chatgpt_inbox


APP_TITLE = "Career Radar"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

# A merged duplicate remains stored for auditability, but only its chosen
# canonical candidate participates in the normal review workflow.
VISIBLE_CANDIDATE_SQL = """
not exists (
    select 1
    from candidate_duplicate_reviews duplicate_review
    where duplicate_review.decision in ('auto_merged', 'confirmed_duplicate')
      and duplicate_review.canonical_candidate_id <> collected_job_candidates_ai_v1.id
      and collected_job_candidates_ai_v1.id in (
          duplicate_review.first_candidate_id,
          duplicate_review.second_candidate_id
      )
)
"""
def rows_query(sql: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
    """Run a select query and return rows as dictionaries."""

    with connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def h(value: object) -> str:
    """Escape values before placing them in HTML."""

    if value is None:
        return ""
    return escape(str(value), quote=True)


def listing_href(value: object) -> str:
    """Make even historical imported URLs safe to render as clickable links."""
    try:
        parsed = urlparse(str(value or ""))
        if parsed.scheme.lower() in {"http", "https"} and parsed.hostname:
            return h(value)
    except ValueError:
        pass
    return "#"


def compact_list(values: object, limit: int = 5) -> str:
    """Format PostgreSQL arrays and Python lists for compact display."""

    if not values:
        return "-"

    if isinstance(values, str):
        return h(values)

    items = [str(value) for value in values if value]
    visible = items[:limit]
    suffix = f", +{len(items) - limit} more" if len(items) > limit else ""
    return h(", ".join(visible) + suffix)


def display_date(value: object) -> str:
    """Render stored ISO dates in a more natural day-month-year form."""

    if not value:
        return "No deadline"

    try:
        return date.fromisoformat(str(value)).strftime("%-d %b %Y")
    except ValueError:
        return str(value)


def skill_details(values: object, label: str) -> str:
    """Render every skill in a compact expandable list."""

    items = [str(value) for value in values or [] if value]
    if not items:
        return f'<span class="muted">No {h(label.lower())} recorded</span>'

    tags = "".join(f'<span class="tag">{h(item)}</span>' for item in items)
    return f"""
        <details class="skills-details">
            <summary>{h(label)} ({len(items)})</summary>
            <div class="tag-list">{tags}</div>
        </details>
    """


def blank_to_none(value: str | None) -> str | None:
    """Treat empty form fields as omitted values."""

    if value is None:
        return None

    cleaned_value = value.strip()
    return cleaned_value or None


def page_number(query: dict[str, list[str]], key: str) -> int:
    """Read a non-negative page number from a URL query string."""

    try:
        return max(0, int(query.get(key, ["0"])[0]))
    except ValueError:
        return 0


def get_dashboard_counts() -> dict[str, int]:
    """Return the human-facing counts shown at the top of the dashboard."""

    candidate_counts = rows_query(
        """
        select
            count(*) filter (
                where recommended_action = 'review_for_promotion'
                  and review_status in ('unreviewed', 'approved')
            ) as ready_candidates,
            count(*) filter (
                where recommended_action in ('manual_review', 'reject')
                  and review_status in ('unreviewed', 'approved')
            ) as secondary_review_candidates
        from collected_job_candidates_ai_v1
        where """
        + VISIBLE_CANDIDATE_SQL
        + ";"
    )[0]

    application_counts = rows_query(
        """
        select
            count(*) filter (
                where application_status in ('interested', 'preparing')
            ) as interested_applications,
            count(*) filter (
                where application_status in ('applied', 'interviewing', 'offer')
            ) as in_progress_applications,
            count(*) filter (
                where application_status in ('rejected', 'withdrawn')
            ) as past_applications,
            count(*) filter (
                where application_status = 'not_applying'
            ) as not_applying_applications
        from job_listings_enriched_v1;
        """
    )[0]

    return {
        "ready_candidates": int(candidate_counts["ready_candidates"] or 0),
        "secondary_review_candidates": int(
            candidate_counts["secondary_review_candidates"] or 0
        ),
        "interested_applications": int(
            application_counts["interested_applications"] or 0
        ),
        "in_progress_applications": int(
            application_counts["in_progress_applications"] or 0
        ),
        "past_applications": int(application_counts["past_applications"] or 0),
        "not_applying_applications": int(
            application_counts["not_applying_applications"] or 0
        ),
    }


def get_candidate_rows(
    action: str,
    limit: int = 20,
    include_undecided_promotions: bool = False,
    offset: int = 0,
) -> list[dict[str, object]]:
    """Return staged candidates for a review bucket."""

    return rows_query(
        """
        select
            id,
            company,
            title,
            url,
            location,
            deadline,
            listing_status,
            review_status,
            recommended_action,
            rule_relevance_label,
            rule_relevance_score,
            ai_relevance_label,
            ai_fit_tier,
            ai_fit_score,
            ai_priority_score,
            ai_explicit_skills,
            ai_skill_gaps,
            ai_evidence
        from collected_job_candidates_ai_v1
        where """
        + VISIBLE_CANDIDATE_SQL
        + """
          and (
                (recommended_action = %s
                and review_status in ('unreviewed', 'approved')
              )
           or (
                %s
                and review_status = 'promoted'
                and not exists (
                    select 1
                    from job_applications a
                    where a.job_listing_id = collected_job_candidates_ai_v1.promoted_job_listing_id
                )
              )
          )
        order by
            ai_priority_score desc nulls last,
            ai_fit_score desc nulls last,
            rule_relevance_score desc,
            deadline nulls last,
            company,
            title
        limit %s offset %s;
        """,
        (action, include_undecided_promotions, limit, offset),
    )


def get_candidate_count(
    action: str,
    include_undecided_promotions: bool = False,
) -> int:
    """Count candidates using the same rules as the visible New Matches list."""

    row = rows_query(
        """
        select count(*) as total
        from collected_job_candidates_ai_v1
        where """
        + VISIBLE_CANDIDATE_SQL
        + """
          and (
                (recommended_action = %s
                and review_status in ('unreviewed', 'approved')
              )
           or (
                %s
                and review_status = 'promoted'
                and not exists (
                    select 1
                    from job_applications a
                    where a.job_listing_id = collected_job_candidates_ai_v1.promoted_job_listing_id
                )
              )
          );
        """,
        (action, include_undecided_promotions),
    )[0]
    return int(row["total"] or 0)


def archive_filters(listing_filter: str, search: str) -> tuple[str, list[object]]:
    """Build the shared WHERE clause for the full historical listing archive."""

    clauses: list[str] = []
    params: list[object] = []

    deadline_sql = "career_radar_iso_date(r.deadline_raw)"
    is_active_sql = f"not ({deadline_sql} is not null and {deadline_sql} < current_date)"

    if listing_filter == "active":
        clauses.append(is_active_sql)
    elif listing_filter == "expired":
        clauses.append(f"not ({is_active_sql})")

    if search:
        clauses.append("(r.company || ' ' || r.title) ilike %s")
        params.append(f"%{search}%")

    where_clause = f"where {' and '.join(clauses)}" if clauses else ""
    return where_clause, params


def get_archive_rows(
    listing_filter: str,
    search: str,
    limit: int,
    offset: int,
) -> list[dict[str, object]]:
    """Return a searchable page of every curated listing, including old ones."""

    where_clause, params = archive_filters(listing_filter, search)
    deadline_sql = "career_radar_iso_date(r.deadline_raw)"
    is_active_sql = f"not ({deadline_sql} is not null and {deadline_sql} < current_date)"
    return rows_query(
        f"""
        select
            r.id as job_listing_id,
            r.company,
            r.title,
            r.url,
            r.location,
            {deadline_sql} as deadline,
            case when {is_active_sql} then 'active' else 'expired' end as listing_status,
            coalesce(a.status, 'not_started') as application_status,
            m.skill_coverage_score,
            coalesce(m.gap_skill_count, 0) as gap_skill_count,
            m.gap_skills,
            candidate.ai_fit_score,
            candidate.ai_fit_tier,
            candidate.ai_evidence
        from raw_job_listings r
        left join job_applications a on a.job_listing_id = r.id
        left join job_skill_match_v1 m on m.job_listing_id = r.id
        left join lateral (
            select c.ai_fit_score, c.ai_fit_tier, c.ai_evidence
            from collected_job_candidates_ai_v1 c
            where c.promoted_job_listing_id = r.id
            order by c.ai_enriched_at desc nulls last, c.id desc
            limit 1
        ) candidate on true
        {where_clause}
        order by
            {is_active_sql} desc,
            {deadline_sql} desc nulls last,
            r.company,
            r.title
        limit %s offset %s;
        """,
        tuple(params + [limit, offset]),
    )


def get_archive_count(listing_filter: str, search: str) -> int:
    """Count archive results so paging never hides unexplained rows."""

    where_clause, params = archive_filters(listing_filter, search)
    row = rows_query(
        f"select count(*) as total from raw_job_listings r {where_clause};",
        tuple(params),
    )[0]
    return int(row["total"] or 0)


def get_application_workflow_rows(
    statuses: tuple[str, ...],
    limit: int = 30,
    offset: int = 0,
) -> list[dict[str, object]]:
    """Return explicitly tracked applications without filtering by listing deadline."""

    return rows_query(
        """
        select
            a.job_listing_id,
            e.company,
            e.title,
            e.url,
            e.location,
            e.deadline,
            e.listing_status,
            a.status as application_status,
            a.applied_on,
            a.follow_up_date,
            a.next_action,
            a.cv_version,
            a.cover_letter_version,
            coalesce(m.data_engineering_automation_score, 0) as data_engineering_automation_score,
            coalesce(m.biomedical_engineering_score, 0) as biomedical_engineering_score,
            coalesce(m.it_support_infrastructure_score, 0) as it_support_infrastructure_score,
            coalesce(m.software_score, 0) as software_score,
            m.skill_coverage_score,
            coalesce(m.gap_skill_count, 0) as gap_skill_count,
            m.gap_skills,
            candidate.ai_fit_score,
            candidate.ai_fit_tier,
            candidate.ai_evidence
        from job_applications a
        join job_listings_enriched_v1 e on e.job_listing_id = a.job_listing_id
        left join job_skill_match_v1 m on m.job_listing_id = a.job_listing_id
        left join lateral (
            select c.ai_fit_score, c.ai_fit_tier, c.ai_evidence
            from collected_job_candidates_ai_v1 c
            where c.promoted_job_listing_id = a.job_listing_id
            order by c.ai_enriched_at desc nulls last, c.id desc
            limit 1
        ) candidate on true
        where a.status = any(%s)
        order by
            a.follow_up_date nulls last,
            e.deadline nulls last,
            e.company,
            e.title
        limit %s
        offset %s;
        """,
        (list(statuses), limit, offset),
    )


def get_application_queue(
    limit: int = 30, offset: int = 0
) -> list[dict[str, object]]:
    """Return jobs you are interested in or preparing to apply for."""

    return get_application_workflow_rows(("interested", "preparing"), limit, offset)


def get_active_applications(
    limit: int = 30, offset: int = 0
) -> list[dict[str, object]]:
    """Return submitted applications and interviews, even after deadlines close."""

    return get_application_workflow_rows(
        ("applied", "interviewing", "offer"), limit, offset
    )


def get_past_applications(
    limit: int = 30, offset: int = 0
) -> list[dict[str, object]]:
    """Return completed application outcomes for later reference."""

    return get_application_workflow_rows(("rejected", "withdrawn"), limit, offset)


def get_not_applying_jobs(
    limit: int = 30, offset: int = 0
) -> list[dict[str, object]]:
    """Return jobs you explicitly chose not to apply to after reviewing them."""

    return get_application_workflow_rows(("not_applying",), limit, offset)


def get_up_next_rows(limit: int = 8) -> list[dict[str, object]]:
    """Return the next active, explicitly interesting jobs to act on.

    A stated deadline comes first because it creates a real time constraint.
    Roles with no stated deadline follow, ranked by the latest AI fit assessment
    when it exists. This keeps rolling listings visible without letting them
    crowd out work that will close soon.
    """

    return rows_query(
        """
        select
            a.job_listing_id,
            e.company,
            e.title,
            e.url,
            e.location,
            e.deadline,
            e.days_until_deadline,
            a.status as application_status,
            a.next_action,
            candidate.ai_fit_score,
            candidate.ai_fit_tier
        from job_applications a
        join job_listings_enriched_v1 e on e.job_listing_id = a.job_listing_id
        left join lateral (
            select c.ai_fit_score, c.ai_fit_tier
            from collected_job_candidates_ai_v1 c
            where c.promoted_job_listing_id = a.job_listing_id
            order by c.ai_enriched_at desc nulls last, c.id desc
            limit 1
        ) candidate on true
        where a.status in ('interested', 'preparing')
          and e.is_active_listing
        order by
            case when e.deadline is null then 1 else 0 end,
            e.deadline asc nulls last,
            candidate.ai_fit_score desc nulls last,
            e.company,
            e.title
        limit %s;
        """,
        (limit,),
    )


def get_tracking_only_jobs(limit: int = 50) -> list[dict[str, object]]:
    """Return promoted jobs intentionally kept outside application work."""

    return rows_query(
        """
        select
            e.job_listing_id,
            e.company,
            e.title,
            e.url,
            e.location,
            e.deadline,
            e.listing_status,
            a.status as application_status,
            a.follow_up_date,
            a.next_action
        from job_applications a
        join job_listings_enriched_v1 e using (job_listing_id)
        where a.status = 'tracking_only'
        order by e.deadline desc nulls last, e.company, e.title
        limit %s;
        """,
        (limit,),
    )


def badge(text: object, tone: str = "neutral") -> str:
    """Render a small status badge."""

    return f'<span class="badge {tone}">{h(text)}</span>'


def status_tone(status: object) -> str:
    """Map workflow states to stable, meaningful badge colours."""

    return {
        "interested": "blue",
        "preparing": "amber",
        "applied": "violet",
        "interviewing": "purple",
        "offer": "green",
        "rejected": "red",
        "withdrawn": "red",
        "not_applying": "slate",
        "tracking_only": "teal",
        "active": "green",
        "expired": "red",
    }.get(str(status), "slate")


def score_tone(score: object) -> str:
    """Group a 0-100 fit score into an immediately readable visual tier."""

    value = int(score or 0)
    if value >= 75:
        return "strong"
    if value >= 50:
        return "medium"
    return "low"


def deadline_summary(days_until_deadline: object, deadline: object) -> str:
    """Describe urgency without hiding the actual deadline date."""

    if deadline is None:
        return "Rolling / no stated deadline"
    if days_until_deadline is None:
        return display_date(deadline)

    days = int(days_until_deadline)
    if days == 0:
        return "Due today"
    if days == 1:
        return "Due tomorrow"
    if days > 1:
        return f"Due in {days} days"
    return "Deadline passed"


STATUS_LABELS = {
    "interested": "Interested",
    "preparing": "Preparing",
    "applied": "Applied",
    "interviewing": "Interviewing",
    "offer": "Offer",
    "rejected": "Rejected",
    "withdrawn": "Withdrawn",
    "not_applying": "Not applying",
    "tracking_only": "Research only",
}


def render_notice(message: str | None, tone: str) -> str:
    """Render an optional notice or error banner."""

    if not message:
        return ""
    return f'<div class="notice {tone}">{h(message)}</div>'


def render_candidate_cards(
    rows: list[dict[str, object]],
    return_to: str,
    enable_batch_selection: bool,
) -> str:
    """Render candidate cards so the first and scrolled-in batches match."""

    batch_form_id = "new-matches-batch-form" if enable_batch_selection else ""
    cards = []
    for row in rows:
        fit_score = int(row["ai_fit_score"] or 0)
        fit_label = str(row["ai_fit_tier"] or "possible fit").replace("_", " ")
        selection_control = (
            f'''<label class="batch-select">
                <input type="checkbox" name="candidate_id" value="{h(row['id'])}"
                       form="{batch_form_id}" data-batch-candidate>
                <span>Select</span>
            </label>'''
            if enable_batch_selection
            else ""
        )
        cards.append(
            f"""
            <article class="match-card">
                {selection_control}
                <div class="fit-score {score_tone(fit_score)}"
                     aria-label="Fit score {fit_score} percent">
                    <strong>{fit_score}%</strong>
                    <small>{h(fit_label)}</small>
                </div>
                <div class="match-main">
                    <div class="item-title">{h(row['title'])}</div>
                    <div class="company">{h(row['company'])}</div>
                    <div class="muted">{h(row['location'])}</div>
                    <p class="evidence">{h(row['ai_evidence'])}</p>
                </div>
                <div class="match-details">
                    <div class="deadline-label">Deadline</div>
                    <div class="deadline">{h(display_date(row['deadline']))}</div>
                    {skill_details(row['ai_explicit_skills'], "Skills mentioned")}
                    {skill_details(row['ai_skill_gaps'], "Experience to build")}
                </div>
                <div class="match-actions">
                    <a class="link-button" href="{listing_href(row['url'])}"
                       target="_blank" rel="noreferrer">Open</a>
                    <form class="candidate-action" method="post"
                          action="/candidate-action">
                        <input type="hidden" name="candidate_id"
                               value="{h(row['id'])}">
                        <input type="hidden" name="return_to"
                               value="{h(return_to)}">
                        <button class="primary" name="action"
                                value="add_to_applications">Add to applications</button>
                        <button name="action" value="skip">Keep for research</button>
                    </form>
                </div>
            </article>
            """
        )
    return "".join(cards)


def render_candidate_section(
    title: str,
    rows: list[dict[str, object]],
    empty_text: str,
    description: str | None = None,
    return_to: str = "/?view=home",
    enable_batch_selection: bool = False,
    next_page: int | None = None,
) -> str:
    """Render one candidate-review section."""

    if not rows:
        body = f'<p class="empty">{h(empty_text)}</p>'
    else:
        body = f"""
        <div class="match-list"{' data-new-matches-list' if enable_batch_selection else ''}>
            {render_candidate_cards(rows, return_to, enable_batch_selection)}
        </div>
        {f'<div class="new-match-sentinel" data-new-match-sentinel data-next-page="{next_page}">Loading more matches…</div>' if next_page is not None else ''}
        """

    batch_controls = ""
    section_class = "band"
    if enable_batch_selection:
        section_class += " candidate-batch"
        batch_controls = f"""
            <div class="batch-toolbar">
                <button type="button" class="selection-toggle" data-selection-toggle
                        aria-pressed="false">Select</button>
                <form id="new-matches-batch-form" class="candidate-batch-form preserve-position"
                      method="post" action="/candidate-batch-action">
                    <input type="hidden" name="return_to" value="{h(return_to)}">
                    <span class="selection-count" data-selection-count>0 selected</span>
                    <button class="primary" name="action" value="add_to_applications"
                            disabled>Add selected to applications</button>
                    <button name="action" value="skip" disabled>Keep selected for research</button>
                </form>
            </div>
        """

    heading = f"<div><h2>{h(title)}</h2>{f'<p class=\"section-description\">{h(description)}</p>' if description else ''}</div>"

    return f"""
    <section class="{section_class}">
        <div class="section-heading">
            {heading}
            {batch_controls}
        </div>
        {body}
    </section>
    """


def render_new_match_fragment(page: int, page_size: int = 15) -> str:
    """Return one appendable New Matches batch for the scroll loader."""

    total = get_candidate_count(
        "review_for_promotion",
        include_undecided_promotions=True,
    )
    rows = get_candidate_rows(
        "review_for_promotion",
        limit=page_size,
        include_undecided_promotions=True,
        offset=page * page_size,
    )
    next_page = page + 1 if (page + 1) * page_size < total else ""
    return f'''<div data-new-match-fragment data-next-page="{next_page}">
        {render_candidate_cards(rows, "/?view=home", enable_batch_selection=True)}
    </div>'''


def get_today_api_payload() -> dict[str, object]:
    """Return the first read-only API surface for the React Today workspace.

    This intentionally reuses the dashboard query helpers.  The React client is
    therefore a new presentation layer, not a parallel interpretation of the
    PostgreSQL model or workflow rules.
    """

    return {
        "generated_at": date.today().isoformat(),
        "counts": get_dashboard_counts(),
        "decision_queue_count": get_candidate_count(
            "review_for_promotion", include_undecided_promotions=True
        ),
        "up_next": get_up_next_rows(limit=5),
        "new_matches": get_candidate_rows(
            "review_for_promotion",
            limit=6,
            include_undecided_promotions=True,
        ),
    }


def refresh_message(result: object) -> str:
    """Describe the existing inbox import result for either web surface."""

    return (
        f"Checked for updates. Processed {result.source_row_count} rows; "
        f"saved {result.saved_ai_enrichment_count} new task AI results; "
        f"skipped {result.skipped_unchanged_ai_enrichment_count} unchanged; "
        f"added {result.historical_applied_count} historical applications and "
        f"{result.historical_not_applying_count} historical skipped listings."
    )


def get_applications_api_payload() -> dict[str, object]:
    """Return the tracked application groups for the React workbench."""

    return {
        "interested": get_application_queue(limit=30),
        "active": get_active_applications(limit=30),
        "past": get_past_applications(limit=30),
    }


def get_workspace_api_payload() -> dict[str, object]:
    """Expose the remaining React read surfaces without changing domain rules."""

    return {
        "discover": get_archive_rows("active", "", 200, 0),
        "discover_count": get_archive_count("active", ""),
        "review": {
            "ready": get_candidate_rows("review_for_promotion", limit=30, include_undecided_promotions=True),
            "manual": get_candidate_rows("manual_review", limit=30),
            "duplicates": get_candidate_duplicate_review_rows(limit=30),
        },
        "insights": rows_query(
            """select normalized_name, jobs_requesting, demand_percent, profile_status,
                      proficiency_level, example_jobs
                 from skill_gap_summary_v1
                 order by case profile_status when 'missing' then 1 when 'learning' then 2 else 3 end,
                          jobs_requesting desc, normalized_name limit 24;"""
        ),
        "profile": rows_query(
            """select s.name, s.normalized_name, ps.proficiency_level,
                      ps.include_in_gap_analysis, ps.evidence
                 from profile_skills ps join skills s on s.id = ps.skill_id
                 order by case ps.proficiency_level when 'strong' then 1 when 'working' then 2 else 3 end,
                          s.normalized_name;"""
        ),
    }


def render_up_next(rows: list[dict[str, object]]) -> str:
    """Render a short, urgency-first shortlist for application work."""

    if not rows:
        return """
        <section class="next-panel">
            <div class="section-heading">
                <div><p class="eyebrow">Your application plan</p><h2>Up next</h2></div>
            </div>
            <p class="empty">Add an active role to your application list and it will appear here.</p>
        </section>
        """

    items = []
    for row in rows:
        fit_score = row["ai_fit_score"]
        fit = (
            f'<span class="mini-fit {score_tone(fit_score)}">{h(fit_score)}% fit</span>'
            if fit_score is not None
            else ""
        )
        items.append(
            f"""
            <article class="next-item">
                <div class="next-deadline">
                    <span>{h(deadline_summary(row['days_until_deadline'], row['deadline']))}</span>
                    <strong>{h(display_date(row['deadline']))}</strong>
                </div>
                <div class="next-role">
                    <div class="item-title">{h(row['title'])}</div>
                    <div class="company">{h(row['company'])}</div>
                    <div class="muted">{h(row['location'])}</div>
                </div>
                <div class="next-meta">
                    {badge(STATUS_LABELS.get(str(row['application_status']), row['application_status']), status_tone(row['application_status']))}
                    {fit}
                    <span class="muted">{h(row['next_action'] or 'Decide whether to apply.')}</span>
                </div>
                <a class="link-button" href="{listing_href(row['url'])}" target="_blank" rel="noreferrer">Open</a>
            </article>
            """
        )

    return f"""
    <section class="next-panel">
        <div class="section-heading">
            <div>
                <p class="eyebrow">Your application plan</p>
                <h2>Up next</h2>
            </div>
            <a class="text-link" href="/?view=applications">View all applications</a>
        </div>
        <div class="next-list">{''.join(items)}</div>
    </section>
    """


def render_application_status_select(current_status: object) -> str:
    """Render a compact application-status dropdown."""

    options = []
    current = str(current_status or "interested")
    for status in APPLICATION_STATUSES:
        selected = " selected" if status == current else ""
        options.append(
            f'<option value="{h(status)}"{selected}>{h(STATUS_LABELS.get(status, status))}</option>'
        )
    return f'<select name="status">{"".join(options)}</select>'


def render_archive_status_select() -> str:
    """Render a clear first status choice for a listing not yet tracked."""

    choices = (
        "interested",
        "preparing",
        "applied",
        "interviewing",
        "rejected",
        "withdrawn",
        "not_applying",
    )
    options = ['<option value="" selected disabled>Track as...</option>']
    options.extend(
        f'<option value="{h(status)}">{h(STATUS_LABELS[status])}</option>'
        for status in choices
    )
    return f'<select name="status" required>{"".join(options)}</select>'


def render_pagination(
    current_page: int,
    total: int,
    page_size: int,
    query: dict[str, str],
    page_key: str,
) -> str:
    """Render simple, honest pagination for a list that has more rows."""

    if total <= page_size:
        return ""

    start = min((current_page * page_size) + 1, total) if total else 0
    end = min((current_page + 1) * page_size, total)
    links = []
    if current_page > 0:
        previous_query = {**query, page_key: str(current_page - 1)}
        links.append(f'<a class="link-button" href="/?{h(urlencode(previous_query))}">Previous</a>')
    if end < total:
        next_query = {**query, page_key: str(current_page + 1)}
        links.append(f'<a class="link-button" href="/?{h(urlencode(next_query))}">Next</a>')

    return f"""
        <div class="pagination">
            <span>Showing {start}-{end} of {total}</span>
            <div>{''.join(links)}</div>
        </div>
    """


def render_archive_rows(
    rows: list[dict[str, object]],
    empty_text: str,
    return_to: str,
) -> str:
    """Render historical listings with a direct way to start tracking one."""

    if not rows:
        return f'<p class="empty">{h(empty_text)}</p>'

    cards = []
    for row in rows:
        status = str(row["application_status"] or "not_started")
        status_text = "Not tracked" if status == "not_started" else STATUS_LABELS.get(status, status)
        action = (
            f"""
            <form method="post" action="/application-action"
                  class="archive-action application-action">
                <input type="hidden" name="job_listing_id" value="{h(row['job_listing_id'])}">
                <input type="hidden" name="return_to" value="{h(return_to)}">
                {render_application_status_select(status) if status != 'not_started' else render_archive_status_select()}
                <button>Save</button>
            </form>
            """
        )
        cards.append(
            f"""
            <article class="archive-card">
                <div>
                    <div class="item-title">{h(row['title'])}</div>
                    <div class="company">{h(row['company'])}</div>
                    <div class="muted">{h(row['location'])}</div>
                </div>
                <div>
                    <div class="deadline-label">Deadline</div>
                    <div class="deadline">{h(display_date(row['deadline']))}</div>
                </div>
                <div>{badge(status_text, status_tone(status))}</div>
                <div class="archive-actions">
                    <a class="link-button" href="{listing_href(row['url'])}" target="_blank" rel="noreferrer">Open</a>
                    {action}
                </div>
            </article>
            """
        )
    return f'<div class="archive-list">{"".join(cards)}</div>'


def render_duplicate_review_rows(rows: list[dict[str, object]]) -> str:
    """Render only ambiguous cross-source listings for a human decision."""

    if not rows:
        return (
            '<p class="empty">No uncertain duplicate pairs are waiting for review. '
            'High-confidence copies are already grouped automatically.</p>'
        )

    cards = []
    for row in rows:
        cards.append(
            f"""
            <article class="duplicate-card">
                <div class="duplicate-heading">
                    <div>
                        <div class="company">{h(row['company'])}</div>
                        <div class="item-title">{h(row['title'])}</div>
                    </div>
                    {badge('Needs your call', 'amber')}
                </div>
                <p class="muted">{h(row['evidence'])}</p>
                <div class="duplicate-pair">
                    <section>
                        <span class="eyebrow">Listing A</span>
                        <div>{h(row['first_location'])}</div>
                        <div class="muted">Posted: {h(display_date(row['first_posted_date']))}</div>
                        <div class="muted">Deadline: {h(display_date(row['first_deadline']))}</div>
                        <a class="text-link" href="{listing_href(row['first_url'])}" target="_blank" rel="noreferrer">Open A</a>
                    </section>
                    <section>
                        <span class="eyebrow">Listing B</span>
                        <div>{h(row['second_location'])}</div>
                        <div class="muted">Posted: {h(display_date(row['second_posted_date']))}</div>
                        <div class="muted">Deadline: {h(display_date(row['second_deadline']))}</div>
                        <a class="text-link" href="{listing_href(row['second_url'])}" target="_blank" rel="noreferrer">Open B</a>
                    </section>
                </div>
                <form class="duplicate-action preserve-position" method="post" action="/candidate-duplicate-action">
                    <input type="hidden" name="first_candidate_id" value="{h(row['first_candidate_id'])}">
                    <input type="hidden" name="second_candidate_id" value="{h(row['second_candidate_id'])}">
                    <input type="hidden" name="return_to" value="/?view=duplicates">
                    <button class="primary" name="action" value="keep_first">Keep listing A</button>
                    <button class="primary" name="action" value="keep_second">Keep listing B</button>
                    <button name="action" value="different_jobs">Keep both listings</button>
                </form>
            </article>
            """
        )
    return f'<div class="duplicate-list">{"".join(cards)}</div>'


def render_application_rows(
    rows: list[dict[str, object]],
    empty_text: str,
    return_to: str = "/?view=applications",
) -> str:
    """Render application queue or active applications."""

    if not rows:
        return f'<p class="empty">{h(empty_text)}</p>'

    cards = []
    for row in rows:
        coverage = max(0, min(100, int(row.get("skill_coverage_score") or 0)))
        track_scores = (
            ("Data & automation", int(row.get("data_engineering_automation_score") or 0), 6),
            ("Biomedical engineering", int(row.get("biomedical_engineering_score") or 0), 9),
            ("IT systems", int(row.get("it_support_infrastructure_score") or 0), 6),
            ("Software", int(row.get("software_score") or 0), 5),
        )
        track_rows = "".join(
            f"""
            <div class="track-score">
                <span>{h(label)}</span><strong>{score}/{maximum}</strong>
                <i><b style="width: {round((score / maximum) * 100)}%"></b></i>
            </div>
            """
            for label, score, maximum in track_scores
        )

        cards.append(
            f"""
            <article class="application-card">
                <div class="application-summary">
                    <div class="application-title-row">
                        <div>
                            <div class="company">{h(row['company'])}</div>
                            <div class="item-title">{h(row['title'])}</div>
                            <div class="muted">{h(row.get('location'))}</div>
                        </div>
                        <div class="application-badges">
                            {badge(STATUS_LABELS.get(str(row['application_status']), row['application_status']), status_tone(row['application_status']))}
                            {badge(row['listing_status'], status_tone(row['listing_status']))}
                        </div>
                    </div>
                    <div class="deadline-block">
                        <span>Listing deadline</span>
                        <strong>{h(display_date(row['deadline']))}</strong>
                    </div>
                </div>
                <div class="application-insight">
                    <div class="coverage-label"><span>Skill coverage</span><strong>{coverage}%</strong></div>
                    <div class="coverage-bar"><b style="width: {coverage}%"></b></div>
                    <p class="muted">{h(row.get('gap_skill_count', 0))} skill gaps: {compact_list(row.get('gap_skills'), limit=3)}</p>
                    <div class="track-scores">{track_rows}</div>
                </div>
                <div class="application-controls">
                    <a class="link-button" href="{listing_href(row['url'])}"
                       target="_blank" rel="noreferrer">Open listing</a>
                    <form class="application-action" method="post" action="/application-action">
                        <input type="hidden" name="job_listing_id"
                               value="{h(row['job_listing_id'])}">
                        <input type="hidden" name="return_to" value="{h(return_to)}">
                        <label>Status{render_application_status_select(row['application_status'])}</label>
                        <label>Follow up<input type="date" name="follow_up_date"
                               value="{h(row.get('follow_up_date'))}"></label>
                        <label class="next-action-field">Next action<input type="text" name="next_action"
                               value="{h(row.get('next_action'))}"
                               placeholder="Add a next action"></label>
                        <button class="primary">Save changes</button>
                    </form>
                </div>
            </article>
            """
        )

    return f'<div class="application-list">{"".join(cards)}</div>'


def render_application_section(
    *,
    section_id: str,
    title: str,
    description: str,
    total: int,
    tone: str,
    rows: list[dict[str, object]],
    empty_text: str,
    return_to: str,
    current_page: int,
    page_size: int,
    page_query: dict[str, str],
    page_key: str,
) -> str:
    """Render one clearly identifiable application workflow section."""

    return f"""
        <section id="{h(section_id)}" class="band application-band application-band-{h(tone)}">
            <div class="section-heading application-heading">
                <div>
                    <p class="eyebrow">Application stage</p>
                    <h2>{h(title)}</h2>
                    <p class="section-description">{h(description)}</p>
                </div>
                <div class="application-heading-count">{badge(f"{total} total", tone)}</div>
            </div>
            {render_application_rows(rows, empty_text, return_to=return_to)}
            {render_pagination(current_page, total, page_size, page_query, page_key)}
        </section>
    """


def render_dashboard(query: dict[str, list[str]]) -> str:
    """Render the main dashboard page."""

    counts = get_dashboard_counts()
    view = query.get("view", ["home"])[0]
    if view not in {"home", "applications", "review", "research", "archive", "duplicates"}:
        view = "home"

    new_page_size = 15
    new_match_total = get_candidate_count(
        "review_for_promotion",
        include_undecided_promotions=True,
    )
    ready_candidates = get_candidate_rows(
        "review_for_promotion",
        limit=new_page_size,
        include_undecided_promotions=True,
        offset=0,
    )
    manual_candidates = get_candidate_rows("manual_review", limit=10)
    reject_candidates = get_candidate_rows("reject", limit=10)
    application_page_size = 15
    interested_page = page_number(query, "interested_page")
    active_page = page_number(query, "active_page")
    past_page = page_number(query, "past_page")
    not_applying_page = page_number(query, "not_applying_page")
    application_queue = get_application_queue(
        limit=application_page_size,
        offset=interested_page * application_page_size,
    )
    active_applications = get_active_applications(
        limit=application_page_size,
        offset=active_page * application_page_size,
    )
    past_applications = get_past_applications(
        limit=application_page_size,
        offset=past_page * application_page_size,
    )
    not_applying_jobs = get_not_applying_jobs(
        limit=application_page_size,
        offset=not_applying_page * application_page_size,
    )
    up_next = get_up_next_rows(limit=8)
    duplicate_reviews = get_candidate_duplicate_review_rows(limit=20)

    archive_filter = query.get("archive_filter", ["all"])[0]
    if archive_filter not in {"all", "active", "expired"}:
        archive_filter = "all"
    archive_search = blank_to_none(query.get("q", [""])[0]) or ""
    archive_page_size = 25
    archive_page = page_number(query, "archive_page")
    archive_total = get_archive_count(archive_filter, archive_search)
    archive_rows = get_archive_rows(
        archive_filter,
        archive_search,
        archive_page_size,
        archive_page * archive_page_size,
    )

    notice = query.get("notice", [None])[0]
    error = query.get("error", [None])[0]
    return_query = {
        key: values[0]
        for key, values in query.items()
        if values and key not in {"notice", "error"}
    }
    current_return_to = f"/?{urlencode(return_query)}" if return_query else "/?view=home"
    application_page_query = {
        "view": "applications",
        "interested_page": str(interested_page),
        "active_page": str(active_page),
        "past_page": str(past_page),
        "not_applying_page": str(not_applying_page),
    }

    stats = "".join(
        f"""
        <div class="stat" data-stat-key="{h(key)}">
            <span>{h(label)}</span>
            <strong>{h(value)}</strong>
        </div>
        """
        for key, label, value in (
            ("new-matches", "New matches", new_match_total),
            ("interested", "Interested", counts["interested_applications"]),
            ("in-progress", "Active", counts["in_progress_applications"]),
            ("past", "Past", counts["past_applications"]),
        )
    )

    nav = "".join(
        f'<a class="nav-link {"selected" if key == view else ""}" href="/?view={key}">{label}</a>'
        for key, label in (
            ("home", "Today"),
            ("applications", "My applications"),
            ("review", "Review later"),
            ("archive", "All listings"),
            ("duplicates", "Duplicates"),
            ("research", "Research library"),
        )
    )

    if view == "applications":
        content = f"""
            <section class="application-overview">
                <div>
                    <p class="eyebrow">Your job-search workflow</p>
                    <h2>My applications</h2>
                    <p class="section-description">Use the stages below to keep active work separate from your historical record.</p>
                </div>
                <nav class="application-jump-nav" aria-label="Jump to application stage">
                    <a href="#interested">Interested <span>{counts['interested_applications']}</span></a>
                    <a href="#active">Active <span>{counts['in_progress_applications']}</span></a>
                    <a href="#past">Past <span>{counts['past_applications']}</span></a>
                    <a href="#not-applying">Not applying <span>{counts['not_applying_applications']}</span></a>
                </nav>
            </section>
            {render_application_section(
                section_id="interested",
                title="Interested & preparing",
                description="Roles you are considering or actively preparing to apply for.",
                total=counts["interested_applications"],
                tone="blue",
                rows=application_queue,
                empty_text="No jobs are in your application list yet.",
                return_to=current_return_to,
                current_page=interested_page,
                page_size=application_page_size,
                page_query=application_page_query,
                page_key="interested_page",
            )}
            {render_application_section(
                section_id="active",
                title="Active applications",
                description="Applications already submitted, interviews, and offers.",
                total=counts["in_progress_applications"],
                tone="violet",
                rows=active_applications,
                empty_text="No active applications yet.",
                return_to=current_return_to,
                current_page=active_page,
                page_size=application_page_size,
                page_query=application_page_query,
                page_key="active_page",
            )}
            {render_application_section(
                section_id="past",
                title="Past applications",
                description="Roles that ended in a rejection or withdrawal.",
                total=counts["past_applications"],
                tone="red",
                rows=past_applications,
                empty_text="No past applications recorded yet.",
                return_to=current_return_to,
                current_page=past_page,
                page_size=application_page_size,
                page_query=application_page_query,
                page_key="past_page",
            )}
            {render_application_section(
                section_id="not-applying",
                title="Not applying",
                description="Historical or reviewed listings you deliberately chose not to pursue.",
                total=counts["not_applying_applications"],
                tone="slate",
                rows=not_applying_jobs,
                empty_text="No reviewed-but-not-applied listings yet.",
                return_to=current_return_to,
                current_page=not_applying_page,
                page_size=application_page_size,
                page_query=application_page_query,
                page_key="not_applying_page",
            )}
        """
    elif view == "review":
        content = (
            """
            <section class="view-intro">
                <p class="eyebrow">A little human judgement goes a long way</p>
                <h2>Review later</h2>
                <p class="section-description">These listings need a quick second look before they join your applications or research library.</p>
            </section>
            """
            + render_candidate_section(
                "Worth a second look",
                manual_candidates,
                "Nothing is waiting for a second look.",
                description="Promising roles where the automatic fit check was not fully certain.",
                return_to="/?view=review",
            )
            + render_candidate_section(
                "Other potential matches",
                reject_candidates,
                "No additional potential matches are waiting.",
                description="Lower-priority listings that may still be useful for market research.",
                return_to="/?view=review",
            )
        )
    elif view == "research":
        content = f"""
            <section class="view-intro">
                <p class="eyebrow">Patterns worth keeping</p>
                <h2>Research library</h2>
                <p class="section-description">Listings saved for market insight, without adding them to your application workflow.</p>
            </section>
            <section class="band">
                <div class="section-heading"><div><h2>Kept for market research</h2><p class="section-description">Use these to spot recurring skills, companies, and role types.</p></div></div>
                {render_application_rows(
                    get_tracking_only_jobs(),
                    "No jobs have been kept only for research yet.",
                    return_to="/?view=research",
                )}
            </section>
        """
    elif view == "archive":
        content = f"""
            <section class="view-intro">
                <p class="eyebrow">Your searchable history</p>
                <h2>All listings</h2>
                <p class="section-description">Search every collected role, then choose where it belongs in your workflow.</p>
            </section>
            <section class="band">
                <div class="section-heading"><h2>Results {badge(archive_total)}</h2></div>
                <form class="archive-filter" method="get" action="/">
                    <input type="hidden" name="view" value="archive">
                    <input type="search" name="q" value="{h(archive_search)}"
                           placeholder="Search company or role">
                    <select name="archive_filter">
                        <option value="all"{' selected' if archive_filter == 'all' else ''}>All listings</option>
                        <option value="active"{' selected' if archive_filter == 'active' else ''}>Active or no deadline</option>
                        <option value="expired"{' selected' if archive_filter == 'expired' else ''}>Expired</option>
                    </select>
                    <button>Filter</button>
                    <a class="link-button" href="/?view=archive">Clear</a>
                </form>
                {render_archive_rows(
                    archive_rows,
                    "No listings match that search.",
                    return_to=f"/?{urlencode({'view': 'archive', 'archive_filter': archive_filter, 'q': archive_search, 'archive_page': archive_page})}",
                )}
                {render_pagination(
                    archive_page,
                    archive_total,
                    archive_page_size,
                    {"view": "archive", "archive_filter": archive_filter, "q": archive_search},
                    "archive_page",
                )}
            </section>
        """
    elif view == "duplicates":
        content = f"""
            <section class="view-intro">
                <p class="eyebrow">Keep the database clean</p>
                <h2>Possible duplicates</h2>
                <p class="section-description">Choose the listing to keep, or confirm that both are separate jobs.</p>
            </section>
            <section class="band">
                <div class="section-heading">
                    <div>
                        <p class="eyebrow">Cross-source matching</p>
                        <h2>Possible duplicates</h2>
                    </div>
                </div>
                {render_duplicate_review_rows(duplicate_reviews)}
            </section>
        """
    else:
        content = f"""
            {render_up_next(up_next)}
            {render_candidate_section(
                f"New matches ({new_match_total})",
                ready_candidates,
                "No strong new matches are waiting for a decision.",
                description="Review your strongest new matches, add the good ones to applications, or keep them for market research.",
                return_to="/?view=home",
                enable_batch_selection=True,
                next_page=1 if new_match_total > new_page_size else None,
            )}
        """

    return render_page(
        f"""
        {render_notice(unquote(notice) if notice else None, "success")}
        {render_notice(unquote(error) if error else None, "error")}

        <section class="toolbar">
            <div>
                <h1>Career Radar</h1>
                <p>{h(display_date(date.today()))}</p>
            </div>
            <form class="preserve-position" method="post" action="/refresh">
                <input type="hidden" name="return_to" value="{h(current_return_to)}">
                <button class="primary">Check for updates</button>
            </form>
        </section>

        <nav class="nav">{nav}</nav>
        <section class="stats">{stats}</section>
        {content}
        """
    )


def render_page(content: str) -> str:
    """Wrap page content in the shared layout and CSS."""

    return f"""
    <!doctype html>
    <html lang="en">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{APP_TITLE}</title>
        <style>
            :root {{
                color-scheme: light;
                --bg: #f5f7f8;
                --ink: #172026;
                --muted: #60707b;
                --line: #d9e0e4;
                --panel: #ffffff;
                --accent: #116466;
                --accent-2: #d98e04;
                --danger: #b42318;
                --good-bg: #e7f5ef;
                --neutral-bg: #eef2f5;
                --shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
            }}
            * {{ box-sizing: border-box; }}
            body {{
                margin: 0;
                background: var(--bg);
                color: var(--ink);
                font-family:
                    Inter, ui-sans-serif, system-ui, -apple-system,
                    BlinkMacSystemFont, "Segoe UI", sans-serif;
                font-size: 14px;
                line-height: 1.45;
            }}
            main {{
                width: min(1480px, calc(100vw - 32px));
                margin: 0 auto;
                padding: 18px 0 42px;
            }}
            h1, h2, p {{ margin: 0; }}
            h1 {{ font-size: 24px; font-weight: 720; letter-spacing: 0; }}
            h2 {{ font-size: 16px; font-weight: 700; letter-spacing: 0; }}
            a {{ color: var(--accent); }}
            button, .link-button, select, input {{
                min-height: 34px;
                border: 1px solid var(--line);
                border-radius: 6px;
                background: #fff;
                color: var(--ink);
                font: inherit;
            }}
            button, .link-button {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                padding: 0 10px;
                cursor: pointer;
                text-decoration: none;
                white-space: nowrap;
            }}
            button:hover, .link-button:hover {{ border-color: var(--accent); }}
            button:focus-visible, .link-button:focus-visible, a:focus-visible,
            select:focus-visible, input:focus-visible {{
                outline: 3px solid rgba(17, 100, 102, 0.24);
                outline-offset: 2px;
            }}
            .primary {{
                background: var(--accent);
                border-color: var(--accent);
                color: #fff;
                font-weight: 700;
            }}
            .danger {{
                border-color: #f0c2bd;
                color: var(--danger);
            }}
            input, select {{ padding: 0 8px; }}
            .toolbar {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 16px;
                padding: 18px 0;
            }}
            .toolbar p {{ color: var(--muted); margin-top: 3px; }}
            .stats {{
                display: grid;
                grid-template-columns: repeat(4, minmax(140px, 1fr));
                gap: 10px;
                margin-bottom: 14px;
            }}
            .nav {{
                display: flex;
                gap: 4px;
                margin: 0 0 14px;
                border-bottom: 1px solid var(--line);
            }}
            .nav-link {{
                display: inline-flex;
                align-items: center;
                min-height: 36px;
                padding: 0 10px;
                color: var(--muted);
                text-decoration: none;
                border-bottom: 2px solid transparent;
            }}
            .nav-link:hover {{ color: var(--accent); }}
            .nav-link.selected {{
                color: var(--accent);
                font-weight: 700;
                border-bottom-color: var(--accent);
            }}
            .stat {{
                background: var(--panel);
                border: 1px solid var(--line);
                border-radius: 8px;
                box-shadow: var(--shadow);
                padding: 12px;
            }}
            .stat span {{
                display: block;
                color: var(--muted);
                font-size: 12px;
                margin-bottom: 4px;
            }}
            .stat strong {{
                display: block;
                font-size: 26px;
                line-height: 1;
            }}
            .band {{
                background: var(--panel);
                border: 1px solid var(--line);
                border-radius: 8px;
                box-shadow: var(--shadow);
                margin: 14px 0;
                overflow: hidden;
            }}
            .view-intro {{
                margin: 22px 0 16px;
                padding: 2px 0 0;
            }}
            .view-intro h2 {{ font-size: 21px; }}
            .section-heading {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                padding: 12px 14px;
                border-bottom: 1px solid var(--line);
                background: #fbfcfd;
            }}
            .section-description {{ color: var(--muted); font-size: 12px; margin-top: 4px; }}
            .application-overview {{
                display: flex;
                align-items: end;
                justify-content: space-between;
                gap: 20px;
                margin: 22px 0 18px;
                padding: 18px;
                border: 1px solid #c9d9d8;
                border-radius: 10px;
                background: linear-gradient(135deg, #f0f8f7, #ffffff 62%);
            }}
            .application-overview h2 {{ font-size: 21px; }}
            .application-jump-nav {{ display: flex; flex-wrap: wrap; gap: 7px; justify-content: flex-end; }}
            .application-jump-nav a {{
                display: inline-flex;
                align-items: center;
                gap: 7px;
                min-height: 34px;
                padding: 0 9px;
                border: 1px solid var(--line);
                border-radius: 999px;
                background: #fff;
                color: var(--ink);
                font-size: 12px;
                font-weight: 700;
                text-decoration: none;
            }}
            .application-jump-nav a:hover {{ border-color: var(--accent); color: var(--accent); }}
            .application-jump-nav span {{
                display: inline-grid;
                place-items: center;
                min-width: 20px;
                height: 20px;
                padding: 0 5px;
                border-radius: 99px;
                background: var(--neutral-bg);
                color: #40515c;
            }}
            .application-band {{
                scroll-margin-top: 16px;
                margin: 24px 0;
                border-left: 5px solid var(--line);
            }}
            .application-band-blue {{ border-left-color: #6c9de4; }}
            .application-band-violet {{ border-left-color: #9685db; }}
            .application-band-red {{ border-left-color: #df8a7f; }}
            .application-band-slate {{ border-left-color: #93a1ad; }}
            .application-heading {{ padding: 15px 16px; }}
            .application-band-blue .application-heading {{ background: #f6f9ff; }}
            .application-band-violet .application-heading {{ background: #faf8ff; }}
            .application-band-red .application-heading {{ background: #fff9f8; }}
            .application-band-slate .application-heading {{ background: #fafbfc; }}
            .application-heading h2 {{ font-size: 18px; }}
            .application-heading-count {{ align-self: flex-start; padding-top: 2px; }}
            .match-list {{ padding: 10px; }}
            .new-match-sentinel {{
                padding: 14px;
                color: var(--muted);
                font-size: 13px;
                text-align: center;
            }}
            .match-card {{
                position: relative;
                display: grid;
                grid-template-columns: 94px minmax(260px, 1.4fr) minmax(230px, 1fr) auto;
                gap: 18px;
                align-items: start;
                padding: 16px;
                border: 1px solid var(--line);
                border-radius: 8px;
                background: #fff;
                transition: border-color 160ms ease, box-shadow 160ms ease, transform 160ms ease;
            }}
            .match-card:hover {{
                border-color: #b8cfcc;
                box-shadow: 0 5px 16px rgba(15, 23, 42, 0.07);
                transform: translateY(-1px);
            }}
            .match-card + .match-card {{ margin-top: 10px; }}
            .batch-toolbar {{ display: flex; align-items: center; gap: 8px; }}
            .candidate-batch-form, .batch-select {{ display: none; }}
            .candidate-batch.selection-active .candidate-batch-form {{
                display: flex;
                align-items: center;
                gap: 6px;
            }}
            .candidate-batch.selection-active .batch-select {{
                display: inline-flex;
                align-items: center;
                gap: 5px;
                position: absolute;
                top: 10px;
                right: 12px;
                color: var(--muted);
                font-size: 12px;
                font-weight: 700;
            }}
            .candidate-batch.selection-active .match-card {{ padding-top: 42px; }}
            .selection-count {{ color: var(--muted); font-size: 12px; white-space: nowrap; }}
            .fit-score {{
                display: grid;
                place-items: center;
                align-content: center;
                width: 82px;
                min-height: 82px;
                border: 5px solid #9ccfca;
                border-radius: 50%;
                background: #f1fbf9;
                color: var(--accent);
                line-height: 1;
            }}
            .fit-score strong {{ font-size: 25px; letter-spacing: 0; }}
            .fit-score span {{ margin-top: 2px; font-size: 11px; color: var(--muted); }}
            .fit-score small {{
                margin-top: 6px;
                color: #0b5c3e;
                font-size: 10px;
                font-weight: 700;
                text-align: center;
                line-height: 1.15;
            }}
            .company {{ margin-top: 3px; color: var(--accent); font-weight: 650; }}
            .match-main .evidence {{ margin: 10px 0 0; max-width: 62ch; }}
            .match-details {{ display: grid; gap: 8px; }}
            .deadline-label {{ color: var(--muted); font-size: 11px; font-weight: 700; text-transform: uppercase; }}
            .deadline {{ font-weight: 700; }}
            .skills-details {{ color: var(--ink); }}
            .skills-details summary {{ cursor: pointer; color: var(--accent); font-weight: 650; }}
            .tag-list {{ display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; }}
            .tag {{
                display: inline-flex;
                align-items: center;
                min-height: 23px;
                padding: 2px 7px;
                border: 1px solid #d3e7e4;
                border-radius: 999px;
                background: #f3faf9;
                color: #24585a;
                font-size: 12px;
            }}
            .match-actions {{ display: flex; flex-direction: column; gap: 8px; min-width: 120px; }}
            .match-actions form {{ display: grid; gap: 6px; }}
            .pagination {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 12px;
                margin: 12px 0 18px;
                color: var(--muted);
                font-size: 13px;
            }}
            .pagination > div {{ display: flex; gap: 6px; }}
            .archive-filter {{
                display: flex;
                gap: 8px;
                padding: 12px;
                border-bottom: 1px solid var(--line);
                background: #fbfcfd;
            }}
            .archive-filter input {{ flex: 1 1 260px; }}
            .archive-list {{ padding: 10px; }}
            .archive-card {{
                display: grid;
                grid-template-columns: minmax(240px, 1.4fr) minmax(130px, 0.55fr) minmax(100px, 0.5fr) minmax(220px, 0.8fr);
                gap: 14px;
                align-items: center;
                padding: 13px;
                border: 1px solid var(--line);
                border-radius: 8px;
                background: #fff;
                transition: border-color 160ms ease, box-shadow 160ms ease;
            }}
            .archive-card:hover {{ border-color: #b8cfcc; box-shadow: 0 3px 12px rgba(15, 23, 42, 0.06); }}
            .archive-card + .archive-card {{ margin-top: 8px; }}
            .archive-actions {{ display: flex; align-items: center; gap: 6px; justify-content: flex-end; }}
            .archive-action {{ display: flex; gap: 6px; }}
            .table-wrap {{ overflow-x: auto; }}
            table {{
                width: 100%;
                border-collapse: collapse;
                table-layout: fixed;
            }}
            th, td {{
                border-bottom: 1px solid var(--line);
                padding: 10px;
                text-align: left;
                vertical-align: top;
            }}
            th {{
                color: var(--muted);
                font-size: 12px;
                font-weight: 700;
                background: #fbfcfd;
            }}
            .item-title {{ font-weight: 720; }}
            .muted {{ color: var(--muted); font-size: 12px; margin-top: 4px; }}
            .evidence {{ color: #33424c; }}
            .badge {{
                display: inline-flex;
                align-items: center;
                min-height: 24px;
                border-radius: 999px;
                padding: 2px 8px;
                font-size: 12px;
                font-weight: 700;
            }}
            .badge.good {{ background: var(--good-bg); color: #0b5c3e; }}
            .badge.neutral {{ background: var(--neutral-bg); color: #40515c; }}
            .badge.blue {{ background: #e4efff; color: #1c4e89; }}
            .badge.amber {{ background: #fff2d6; color: #8a5100; }}
            .badge.violet {{ background: #eee9ff; color: #5541a3; }}
            .badge.purple {{ background: #f4e6fb; color: #73388a; }}
            .badge.green {{ background: #e4f5eb; color: #17643d; }}
            .badge.red {{ background: #fde9e7; color: #9a2b20; }}
            .badge.slate {{ background: #eef1f4; color: #46535e; }}
            .badge.teal {{ background: #dff4f1; color: #116466; }}
            .eyebrow {{
                margin-bottom: 3px;
                color: var(--accent);
                font-size: 11px;
                font-weight: 760;
                letter-spacing: 0;
                text-transform: uppercase;
            }}
            .text-link {{ font-weight: 700; text-decoration: none; }}
            .text-link:hover {{ text-decoration: underline; }}
            .fit-score.strong {{
                border-color: #65b985;
                background: #edf9f1;
                color: #17643d;
            }}
            .fit-score.strong small {{ color: #17643d; }}
            .fit-score.medium {{
                border-color: #e7ae44;
                background: #fff8e9;
                color: #925d08;
            }}
            .fit-score.medium small {{ color: #925d08; }}
            .fit-score.low {{
                border-color: #dc8276;
                background: #fff0ee;
                color: #a2382d;
            }}
            .fit-score.low small {{ color: #a2382d; }}
            .next-panel {{
                margin: 14px 0;
                border: 1px solid #c9d9d8;
                border-radius: 8px;
                background: #fff;
                box-shadow: var(--shadow);
                overflow: hidden;
            }}
            .next-list {{ display: grid; grid-template-columns: 1fr; }}
            .next-item {{
                display: grid;
                grid-template-columns: 118px minmax(0, 1fr) minmax(145px, 0.8fr) auto;
                gap: 14px;
                align-items: center;
                padding: 14px;
                border-top: 1px solid var(--line);
            }}
            .next-item:nth-child(odd) {{ background: #fbfdfd; }}
            .next-deadline {{ display: grid; gap: 3px; }}
            .next-deadline span {{ color: #925d08; font-size: 12px; font-weight: 750; }}
            .next-deadline strong {{ font-size: 13px; }}
            .next-role {{ min-width: 0; }}
            .next-meta {{ display: grid; gap: 6px; justify-items: start; min-width: 0; }}
            .mini-fit {{ font-size: 12px; font-weight: 750; }}
            .mini-fit.strong {{ color: #17643d; }}
            .mini-fit.medium {{ color: #925d08; }}
            .mini-fit.low {{ color: #a2382d; }}
            .application-list {{ display: grid; gap: 10px; padding: 10px; }}
            .application-card {{
                display: grid;
                grid-template-columns: minmax(230px, 1.2fr) minmax(260px, 1fr) minmax(290px, 1.2fr);
                gap: 18px;
                padding: 16px;
                border: 1px solid var(--line);
                border-radius: 7px;
                background: #fff;
                transition: border-color 160ms ease, box-shadow 160ms ease;
            }}
            .application-card:hover {{ box-shadow: 0 4px 14px rgba(15, 23, 42, 0.06); }}
            .application-band-blue .application-card {{ border-color: #d9e5f8; }}
            .application-band-violet .application-card {{ border-color: #e2dcf5; }}
            .application-band-red .application-card {{ border-color: #f2ddda; }}
            .application-band-slate .application-card {{ border-color: #e1e5e8; }}
            .application-summary, .application-insight, .application-controls {{ min-width: 0; }}
            .application-title-row {{ display: flex; justify-content: space-between; gap: 10px; }}
            .application-title-row .company {{ margin: 0 0 4px; }}
            .application-title-row .item-title {{ font-size: 16px; line-height: 1.25; }}
            .application-badges {{ display: grid; align-content: start; justify-items: end; gap: 5px; }}
            .deadline-block {{
                display: grid;
                gap: 2px;
                margin-top: 18px;
                padding-top: 11px;
                border-top: 1px solid var(--line);
            }}
            .deadline-block span, .coverage-label span {{ color: var(--muted); font-size: 11px; font-weight: 700; text-transform: uppercase; }}
            .deadline-block strong {{ font-size: 14px; }}
            .coverage-label {{ display: flex; align-items: baseline; justify-content: space-between; }}
            .coverage-label strong {{ font-size: 22px; color: var(--accent); }}
            .coverage-bar, .track-score i {{ display: block; height: 7px; overflow: hidden; border-radius: 99px; background: #e7ecee; }}
            .coverage-bar b, .track-score i b {{ display: block; height: 100%; border-radius: inherit; background: #1c8585; }}
            .application-insight .muted {{ min-height: 33px; }}
            .track-scores {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px 13px; margin-top: 12px; }}
            .track-score {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; column-gap: 6px; color: var(--muted); font-size: 11px; }}
            .track-score strong {{ color: var(--ink); font-size: 11px; }}
            .track-score i {{ grid-column: 1 / -1; margin-top: 4px; height: 5px; }}
            .application-controls > .link-button {{ margin-bottom: 10px; }}
            .application-controls form {{ display: grid; grid-template-columns: minmax(120px, 0.8fr) minmax(132px, 0.8fr); gap: 9px; }}
            .application-controls label {{ display: grid; gap: 4px; color: var(--muted); font-size: 11px; font-weight: 700; }}
            .application-controls label select, .application-controls label input {{ width: 100%; }}
            .application-controls .next-action-field {{ grid-column: 1 / -1; }}
            .application-controls button {{ justify-self: start; }}
            .duplicate-list {{ display: grid; gap: 10px; padding: 10px; }}
            .duplicate-card {{
                padding: 15px;
                border: 1px solid var(--line);
                border-radius: 7px;
                background: #fff;
            }}
            .duplicate-card .eyebrow {{ margin-bottom: 2px; }}
            .duplicate-heading {{ display: flex; justify-content: space-between; gap: 12px; }}
            .duplicate-heading .company {{ margin: 0 0 3px; }}
            .duplicate-pair {{
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 12px;
                margin: 13px 0;
            }}
            .duplicate-pair section {{
                display: grid;
                align-content: start;
                gap: 4px;
                padding: 11px;
                border: 1px solid var(--line);
                border-radius: 6px;
                background: #fbfcfd;
            }}
            .duplicate-action {{ display: flex; flex-wrap: wrap; gap: 7px; }}
            .actions {{
                display: flex;
                flex-direction: column;
                gap: 8px;
            }}
            .actions form {{
                display: flex;
                flex-wrap: wrap;
                gap: 6px;
            }}
            .actions input[type="text"] {{
                min-width: 180px;
                flex: 1 1 180px;
            }}
            .empty {{ padding: 14px; color: var(--muted); }}
            .notice {{
                border-radius: 8px;
                padding: 12px 14px;
                margin: 10px 0;
                border: 1px solid var(--line);
                background: #fff;
            }}
            .notice.success {{
                border-color: #b8dfcd;
                background: #eefaf4;
                color: #0f5132;
            }}
            .notice.error {{
                border-color: #f3b6b0;
                background: #fff1f0;
                color: var(--danger);
            }}
            .toast {{
                position: fixed;
                right: 18px;
                bottom: 18px;
                z-index: 10;
                max-width: min(420px, calc(100vw - 36px));
                padding: 12px 14px;
                border: 1px solid #b8dfcd;
                border-radius: 8px;
                background: #eefaf4;
                box-shadow: 0 8px 24px rgba(15, 23, 42, 0.15);
                color: #0f5132;
                opacity: 0;
                pointer-events: none;
                transform: translateY(8px);
                transition: opacity 180ms ease, transform 180ms ease;
            }}
            .toast.visible {{ opacity: 1; transform: translateY(0); }}
            .toast.error {{
                border-color: #f3b6b0;
                background: #fff1f0;
                color: var(--danger);
            }}
            @media (max-width: 900px) {{
                main {{ width: min(100vw - 20px, 1480px); }}
                .toolbar {{ align-items: flex-start; flex-direction: column; }}
                .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
                .nav {{ overflow-x: auto; width: 100%; }}
                .match-card {{ grid-template-columns: 76px 1fr; gap: 12px; }}
                .batch-toolbar {{ align-items: flex-end; flex-direction: column; }}
                .candidate-batch.selection-active .candidate-batch-form {{
                    flex-wrap: wrap;
                    justify-content: flex-end;
                }}
                .fit-score {{ width: 68px; min-height: 68px; border-width: 4px; }}
                .fit-score strong {{ font-size: 21px; }}
                .match-details, .match-actions {{ grid-column: 2; }}
                .next-item {{ grid-template-columns: 1fr auto; }}
                .next-deadline, .next-role, .next-meta {{ grid-column: 1; }}
                .next-item > .link-button {{ grid-column: 2; grid-row: 1; }}
                .application-card {{ grid-template-columns: 1fr; gap: 14px; }}
                .application-overview {{ align-items: stretch; flex-direction: column; }}
                .application-jump-nav {{ justify-content: flex-start; }}
                .duplicate-pair {{ grid-template-columns: 1fr; }}
                .pagination, .archive-filter {{ align-items: stretch; flex-direction: column; }}
                .archive-card {{ grid-template-columns: 1fr; }}
                .archive-actions {{ justify-content: flex-start; flex-wrap: wrap; }}
                table {{ min-width: 980px; }}
            }}
        </style>
    </head>
    <body>
        <main>{content}</main>
        <script>
            (() => {{
                document.querySelectorAll('.notice').forEach((notice) => {{
                    setTimeout(() => notice.remove(), 3200);
                }});
                const savedScroll = sessionStorage.getItem('career-radar-scroll-y');
                if (savedScroll !== null) {{
                    sessionStorage.removeItem('career-radar-scroll-y');
                    window.scrollTo(0, Number(savedScroll));
                }}
                document.addEventListener('submit', (event) => {{
                    if (event.target.matches('form.candidate-action, form.application-action, form.preserve-position')) {{
                        sessionStorage.setItem('career-radar-scroll-y', String(window.scrollY));
                    }}
                }});
                document.querySelectorAll('.candidate-batch').forEach((section) => {{
                    const toggle = section.querySelector('[data-selection-toggle]');
                    const count = section.querySelector('[data-selection-count]');
                    const actionButtons = [...section.querySelectorAll('.candidate-batch-form button[name="action"]')];
                    const updateSelection = () => {{
                        const checkboxes = [...section.querySelectorAll('[data-batch-candidate]')];
                        const selected = checkboxes.filter((checkbox) => checkbox.checked).length;
                        count.textContent = `${{selected}} selected`;
                        actionButtons.forEach((button) => {{ button.disabled = selected === 0; }});
                    }};
                    toggle.addEventListener('click', () => {{
                        const active = section.classList.toggle('selection-active');
                        toggle.setAttribute('aria-pressed', String(active));
                        toggle.textContent = active ? 'Cancel selection' : 'Select';
                        if (!active) section.querySelectorAll('[data-batch-candidate]').forEach((checkbox) => {{ checkbox.checked = false; }});
                        updateSelection();
                    }});
                    section.addEventListener('change', (event) => {{
                        if (event.target.matches('[data-batch-candidate]')) updateSelection();
                    }});
                    updateSelection();
                }});
                const sentinel = document.querySelector('[data-new-match-sentinel]');
                if (sentinel) {{
                    const list = document.querySelector('[data-new-matches-list]');
                    let loading = false;
                    const loadMore = async () => {{
                        if (loading || !sentinel.dataset.nextPage) return;
                        loading = true;
                        sentinel.textContent = 'Loading more matches…';
                        try {{
                            const response = await fetch(`/new-matches?page=${{encodeURIComponent(sentinel.dataset.nextPage)}}`);
                            if (!response.ok) throw new Error('Unable to load more matches.');
                            const template = document.createElement('template');
                            template.innerHTML = await response.text();
                            const fragment = template.content.querySelector('[data-new-match-fragment]');
                            if (!fragment) throw new Error('Invalid match response.');
                            list.insertAdjacentHTML('beforeend', fragment.innerHTML);
                            sentinel.dataset.nextPage = fragment.dataset.nextPage;
                            if (!fragment.dataset.nextPage) {{
                                observer.disconnect();
                                sentinel.remove();
                            }} else {{
                                sentinel.textContent = 'Scroll for more matches…';
                            }}
                        }} catch (error) {{
                            sentinel.textContent = 'Could not load more matches. Scroll to retry.';
                        }} finally {{
                            loading = false;
                        }}
                    }};
                    const observer = new IntersectionObserver((entries) => {{
                        if (entries.some((entry) => entry.isIntersecting)) loadMore();
                    }}, {{ rootMargin: '480px 0px' }});
                    observer.observe(sentinel);
                }}
            }})();
        </script>
    </body>
    </html>
    """


def redirect(handler: BaseHTTPRequestHandler, location: str) -> None:
    """Send a browser redirect after a form submission."""

    handler.send_response(HTTPStatus.SEE_OTHER)
    handler.send_header("Location", location)
    handler.end_headers()


def send_json(handler: BaseHTTPRequestHandler, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
    """Write a small same-origin JSON response for the React application."""

    body = json.dumps(payload, default=str, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def message_location(
    message: str,
    is_error: bool = False,
    return_to: str = "/",
) -> str:
    """Build a same-page redirect URL carrying a short notice."""

    key = "error" if is_error else "notice"
    parsed = urlparse(return_to)
    if (
        parsed.scheme or parsed.netloc
        or not parsed.path.startswith("/") or parsed.path.startswith("//")
        or "\\" in return_to or any(ord(char) < 32 for char in return_to)
    ):
        parsed = urlparse("/")
    query = parse_qs(parsed.query)
    query.pop("notice", None)
    query.pop("error", None)
    query[key] = [message]
    return parsed._replace(query=urlencode(query, doseq=True)).geturl()


class CareerRadarHandler(BaseHTTPRequestHandler):
    """HTTP handler for the local Career Radar dashboard."""

    def log_message(self, format: str, *args: object) -> None:
        """Keep the terminal quiet unless there is a real server error."""

        return

    def valid_request_host(self) -> bool:
        """Reject unexpected hosts, including browser DNS-rebinding requests."""
        host, port = self.server.server_address[:2]
        allowed = {f"{host}:{port}", f"{self.server.server_name}:{port}"}
        if host in {"127.0.0.1", "localhost"}:
            allowed.update({f"localhost:{port}", f"127.0.0.1:{port}"})
        return self.headers.get("Host", "").lower() in allowed

    def api_origin_is_allowed(self) -> bool:
        """Allow API writes only from this origin or an explicit local dev origin.

        The optional environment value exists solely for Vite's separate local
        development port. Production must keep the API and React build
        same-origin and leave it unset.
        """

        expected_origin = f"http://{self.headers.get('Host')}"
        configured_origins = {
            value.strip()
            for value in os.getenv("CAREER_RADAR_DEV_ORIGINS", "").split(",")
            if value.strip()
        }
        # localhost and 127.0.0.1 are the same local machine, but browsers
        # treat them as separate origins. When one has been explicitly
        # approved for Vite development, approve its loopback alias on that
        # exact port too. Production leaves this setting empty.
        loopback_aliases: set[str] = set()
        for configured_origin in configured_origins:
            parsed_origin = urlparse(configured_origin)
            if parsed_origin.scheme != "http" or parsed_origin.hostname not in {"localhost", "127.0.0.1"}:
                continue
            port = f":{parsed_origin.port}" if parsed_origin.port is not None else ""
            alias_host = "localhost" if parsed_origin.hostname == "127.0.0.1" else "127.0.0.1"
            loopback_aliases.add(f"http://{alias_host}{port}")
        origin = self.headers.get("Origin")
        return (
            origin is not None
            and origin in ({expected_origin} | configured_origins | loopback_aliases)
            and self.headers.get("Sec-Fetch-Site") != "cross-site"
        )

    def handle_api_post(self, parsed_path) -> None:
        """Handle the narrowly scoped JSON mutations used by React."""

        if not self.api_origin_is_allowed():
            self.send_error(HTTPStatus.FORBIDDEN, "Cross-site API request rejected.")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16384 or self.headers.get_content_type() != "application/json":
                raise ValueError("Expected a small JSON request.")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object.")
        except (ValueError, UnicodeError, json.JSONDecodeError):
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid API request.")
            return

        if parsed_path.path == "/api/v1/refresh":
            try:
                result = import_chatgpt_inbox()
                send_json(
                    self,
                    {
                        "refreshed": True,
                        "message": refresh_message(result),
                        "processed_rows": result.source_row_count,
                    },
                )
            except Exception as error:
                send_json(self, {"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed_path.path == "/api/v1/candidate-action":
            try:
                candidate_id = payload.get("candidate_id")
                decision = payload.get("decision")
                if not isinstance(candidate_id, int) or decision not in {"interested", "not_interested"}:
                    raise RuntimeError("A candidate and decision are required.")
                action = "add_to_applications" if decision == "interested" else "not_interested"
                company, title = self.apply_candidate_action(candidate_id, action)
                send_json(
                    self,
                    {
                        "saved": True,
                        "candidate_id": candidate_id,
                        "message": f"Added {company} - {title} to your application list."
                        if decision == "interested"
                        else f"Marked {company} - {title} as not interested.",
                    },
                )
            except Exception as error:
                send_json(self, {"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed_path.path != "/api/v1/application-action":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        try:
            job_listing_id = payload.get("job_listing_id")
            status = payload.get("status")
            if not isinstance(job_listing_id, int) or not isinstance(status, str):
                raise RuntimeError("A job listing ID and status are required.")

            def optional_text(name: str) -> str | None:
                value = payload.get(name)
                if value is None:
                    return None
                if not isinstance(value, str):
                    raise RuntimeError(f"{name} must be text.")
                return value.strip() or None

            set_application(
                job_listing_id=job_listing_id,
                status=status,
                applied_on=None,
                application_url=None,
                cv_version=None,
                cover_letter_version=None,
                follow_up_date=optional_text("follow_up_date"),
                next_action=optional_text("next_action"),
                notes=None,
            )
            send_json(self, {"saved": True, "job_listing_id": job_listing_id})
        except Exception as error:
            send_json(self, {"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def do_GET(self) -> None:
        """Render the dashboard."""

        if not self.valid_request_host():
            self.send_error(HTTPStatus.FORBIDDEN, "Unexpected request host.")
            return
        parsed_path = urlparse(self.path)
        if parsed_path.path == "/api/v1/today":
            try:
                send_json(self, get_today_api_payload())
            except Exception as error:
                send_json(self, {"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed_path.path == "/api/v1/applications":
            try:
                send_json(self, get_applications_api_payload())
            except Exception as error:
                send_json(self, {"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed_path.path == "/api/v1/workspace":
            try:
                send_json(self, get_workspace_api_payload())
            except Exception as error:
                send_json(self, {"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed_path.path == "/api/v1/health":
            send_json(self, {"status": "ok"})
            return
        if parsed_path.path == "/new-matches":
            try:
                fragment = render_new_match_fragment(page_number(parse_qs(parsed_path.query), "page"))
                body = fragment.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as error:
                body = str(error).encode("utf-8")
                self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            return
        if parsed_path.path != "/":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        try:
            page = render_dashboard(parse_qs(parsed_path.query))
            body = page.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as error:
            page = render_page(render_notice(str(error), "error"))
            body = page.encode("utf-8")
            self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_HEAD(self) -> None:
        """Return a lightweight health response for local checks."""

        if not self.valid_request_host():
            self.send_error(HTTPStatus.FORBIDDEN, "Unexpected request host.")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_POST(self) -> None:
        """Handle dashboard form actions."""

        if not self.valid_request_host():
            self.send_error(HTTPStatus.FORBIDDEN, "Unexpected request host.")
            return
        parsed_path = urlparse(self.path)
        if parsed_path.path.startswith("/api/v1/"):
            self.handle_api_post(parsed_path)
            return

        origin = self.headers.get("Origin")
        expected_origin = f"http://{self.headers.get('Host')}"
        if (origin is not None and origin != expected_origin) or self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.send_error(HTTPStatus.FORBIDDEN, "Cross-site form submission rejected.")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise ValueError("Invalid form size.")
            if self.headers.get_content_type() != "application/x-www-form-urlencoded":
                raise ValueError("Expected a URL-encoded form.")
            form = parse_qs(
                self.rfile.read(length).decode("utf-8"),
                keep_blank_values=True,
                max_num_fields=1000,
            )
        except (ValueError, UnicodeError):
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid form submission.")
            return

        try:
            if parsed_path.path == "/refresh":
                self.handle_refresh(form)
            elif parsed_path.path == "/candidate-action":
                self.handle_candidate_action(form)
            elif parsed_path.path == "/candidate-batch-action":
                self.handle_candidate_batch_action(form)
            elif parsed_path.path == "/candidate-duplicate-action":
                self.handle_candidate_duplicate_action(form)
            elif parsed_path.path == "/application-action":
                self.handle_application_action(form)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as error:
            return_to = form.get("return_to", ["/"])[0]
            redirect(
                self,
                message_location(str(error), is_error=True, return_to=return_to),
            )

    def handle_refresh(self, form: dict[str, list[str]]) -> None:
        """Import the configured ChatGPT/Sheet inbox."""

        result = import_chatgpt_inbox()
        return_to = form.get("return_to", ["/?view=home"])[0]
        redirect(self, message_location(refresh_message(result), return_to=return_to))

    def handle_candidate_action(self, form: dict[str, list[str]]) -> None:
        """Promote, track, or reject one staged candidate."""

        candidate_id = int(form["candidate_id"][0])
        action = form["action"][0]
        return_to = form.get("return_to", ["/?view=home"])[0]

        company, title = self.apply_candidate_action(candidate_id, action)
        if action in {"promote", "add_to_applications"}:
            message = f"Added {company} - {title} to your application list."
        else:
            message = f"Kept {company} - {title} for market research."
        redirect(self, message_location(message, return_to=return_to))

    @staticmethod
    @transaction()
    def apply_candidate_action(candidate_id: int, action: str) -> tuple[str, str]:
        """Apply one review decision and return the listing identity."""

        if action in {"promote", "add_to_applications"}:
            result = promote_candidate_to_listing(candidate_id)
            set_application(
                job_listing_id=result.job_listing_id,
                status="interested",
                applied_on=None,
                application_url=None,
                cv_version=None,
                cover_letter_version=None,
                follow_up_date=None,
                next_action="Review the listing and decide whether to apply.",
                notes="Added from the job-match inbox.",
                overwrite_existing=False,
            )
            return result.company, result.title

        if action in {"track_only", "skip"}:
            result = promote_candidate_to_listing(candidate_id)
            set_application(
                job_listing_id=result.job_listing_id,
                status="tracking_only",
                applied_on=None,
                application_url=None,
                cv_version=None,
                cover_letter_version=None,
                follow_up_date=None,
                next_action="Keep for market analysis.",
                notes="Promoted from candidate staging as market-tracking data.",
                overwrite_existing=False,
            )
            return result.company, result.title

        if action == "not_interested":
            result = promote_candidate_to_listing(candidate_id)
            set_application(
                job_listing_id=result.job_listing_id,
                status="not_applying",
                applied_on=None,
                application_url=None,
                cv_version=None,
                cover_letter_version=None,
                follow_up_date=None,
                next_action=None,
                notes="Marked not interested from the job-match inbox.",
                overwrite_existing=False,
            )
            return result.company, result.title

        raise RuntimeError(f"Unsupported candidate action: {action}")

    def handle_candidate_batch_action(self, form: dict[str, list[str]]) -> None:
        """Apply the same review decision to the selected visible candidates."""

        action = form.get("action", [""])[0]
        return_to = form.get("return_to", ["/?view=home"])[0]
        if action not in {"add_to_applications", "skip"}:
            raise RuntimeError("Choose whether to add or skip the selected listings.")

        candidate_ids = []
        for raw_id in form.get("candidate_id", []):
            candidate_id = int(raw_id)
            if candidate_id not in candidate_ids:
                candidate_ids.append(candidate_id)
        if not candidate_ids:
            raise RuntimeError("Select at least one listing first.")

        with transaction():
            for candidate_id in candidate_ids:
                self.apply_candidate_action(candidate_id, action)

        verb = "Added" if action == "add_to_applications" else "Skipped"
        destination = "your application list" if action == "add_to_applications" else "market research"
        message = f"{verb} {len(candidate_ids)} listing(s) to {destination}."
        redirect(self, message_location(message, return_to=return_to))

    def handle_application_action(self, form: dict[str, list[str]]) -> None:
        """Update one application workflow row from the dashboard."""

        job_listing_id = int(form["job_listing_id"][0])
        status = form["status"][0]
        return_to = form.get("return_to", ["/?view=applications"])[0]
        if status == "not_started":
            raise RuntimeError(
                "Use another status to track this job. Removing application rows "
                "from the web UI can come later."
            )

        set_application(
            job_listing_id=job_listing_id,
            status=status,
            applied_on=None,
            application_url=None,
            cv_version=None,
            cover_letter_version=None,
            follow_up_date=blank_to_none(form.get("follow_up_date", [""])[0]),
            next_action=blank_to_none(form.get("next_action", [""])[0]),
            notes=None,
        )
        redirect(
            self,
            message_location(f"Updated job {job_listing_id}.", return_to=return_to),
        )

    def handle_candidate_duplicate_action(self, form: dict[str, list[str]]) -> None:
        """Save a manual decision for one ambiguous candidate pair."""

        first_candidate_id = int(form["first_candidate_id"][0])
        second_candidate_id = int(form["second_candidate_id"][0])
        action = form["action"][0]
        return_to = form.get("return_to", ["/?view=duplicates"])[0]

        decide_candidate_duplicate(
            first_candidate_id,
            second_candidate_id,
            action,
        )
        message = (
            "Marked the listings as separate jobs."
            if action == "different_jobs"
            else "Marked one listing as the duplicate copy."
        )
        redirect(self, message_location(message, return_to=return_to))


def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Start the local Career Radar web app."""

    server = ThreadingHTTPServer((host, port), CareerRadarHandler)
    print(f"Career Radar running at http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()


def main() -> None:
    """Parse web-server arguments and start the app."""

    parser = argparse.ArgumentParser(description="Run the Career Radar web app.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    run_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
