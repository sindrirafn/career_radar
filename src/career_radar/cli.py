import argparse
import sys
from pathlib import Path

from career_radar.ai_enrichment import (
    AI_BULK_REVIEW_STATUSES,
    AI_REVIEW_ACTION_FILTERS,
    AI_STATUS_ACTION_FILTERS,
    apply_ai_review_status,
    enrich_candidates,
    import_bulk_manual_ai_enrichments,
    import_manual_ai_enrichment,
    print_ai_enrichments,
    print_ai_review_queue,
    print_bulk_manual_ai_prompt,
    print_manual_ai_prompt,
)
from career_radar.applications import (
    APPLICATION_LIST_FILTERS,
    APPLICATION_STATUSES,
    print_application_queue,
    print_applications,
    print_due_applications,
    remove_application,
    set_application,
)
from career_radar.candidates import (
    CANDIDATE_LABEL_FILTERS,
    CANDIDATE_REVIEW_STATUSES,
    print_candidate_save_result,
    print_candidates,
    promote_candidate,
    save_collected_candidates,
    set_candidate_review_status,
)
from career_radar.profile import (
    PROFILE_LEVELS,
    print_profile_skills,
    remove_profile_skill,
    set_profile_skill,
)
from career_radar.reports import (
    TRACK_SCORE_COLUMNS,
    print_duplicate_candidates,
    print_job_skill_matches,
    print_skill_gaps,
    print_top_jobs,
)
from career_radar.sources.alfred import (
    collect_alfred_jobs,
    diagnose_alfred_jobs_page,
    fetch_alfred_jobs_page,
)
from career_radar.sources.chatgpt_inbox import (
    DEFAULT_CSV_URL_ENV_VAR,
    import_chatgpt_inbox,
    print_inbox_import_result,
)
from scripts import (
    apply_category_assignments,
    check_db_connection,
    import_raw_jobs,
    run_analysis,
    setup_database,
)


def print_collected_listings_preview(listings, limit: int = 10) -> None:
    """Print a compact preview of collected listings before optional import."""

    print(f"Collected {len(listings)} listings.")
    print()

    if limit <= 0:
        print("Preview skipped.")
        print()
        return

    for index, listing in enumerate(listings[:limit], start=1):
        deadline = listing.deadline or "No stated deadline"
        location = listing.location or "No location"
        print(f"{index}. {listing.title}")
        print(f"   Company: {listing.company}")
        print(f"   Deadline: {deadline}")
        print(f"   Location: {location}")
        print(f"   URL: {listing.url}")
        print()

    if len(listings) > limit:
        print(f"...and {len(listings) - limit} more.")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="career-radar",
        description="Career Radar command-line tools",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check-db")
    subparsers.add_parser("setup")
    subparsers.add_parser("import-raw")
    subparsers.add_parser("apply-categories")
    subparsers.add_parser("analyze")
    subparsers.add_parser("refresh")

    web_parser = subparsers.add_parser(
        "web",
        help="Run the local Career Radar web app.",
    )
    web_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for the local web app.",
    )
    web_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for the local web app.",
    )

    import_chatgpt_inbox_parser = subparsers.add_parser(
        "import-chatgpt-inbox",
        help="Stage CSV rows exported by the scheduled ChatGPT job task.",
    )
    import_chatgpt_inbox_parser.add_argument(
        "--file",
        dest="files",
        action="append",
        help=(
            "Inbox CSV file to import. Omit files and URLs to import every CSV "
            "in data/inbox."
        ),
    )
    import_chatgpt_inbox_parser.add_argument(
        "--url",
        dest="urls",
        action="append",
        help=(
            "Live CSV URL to import, such as a published Google Sheet CSV link. "
            f"Can also be set in {DEFAULT_CSV_URL_ENV_VAR}."
        ),
    )
    import_chatgpt_inbox_parser.add_argument(
        "--skip-ai-results",
        action="store_true",
        help="Stage rows without saving the task's AI/relevance fields.",
    )
    import_chatgpt_inbox_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the inbox CSV without saving candidates or AI results.",
    )
    import_chatgpt_inbox_parser.add_argument(
        "--model-label",
        default="chatgpt-scheduled-task",
        help="Label describing the scheduled ChatGPT task/model source.",
    )

    collect_alfred_parser = subparsers.add_parser(
        "collect-alfred",
        help="Preview or stage listings from the public Alfreð jobs page.",
    )
    collect_alfred_parser.add_argument(
        "--preview-limit",
        type=int,
        default=10,
        help="Maximum number of collected listings to print.",
    )
    collect_alfred_parser.add_argument(
        "--save-candidates",
        dest="should_save_candidates",
        action="store_true",
        help="Save collected listings to candidate staging with relevance signals.",
    )
    collect_alfred_parser.add_argument(
        "--import",
        dest="should_save_candidates",
        action="store_true",
        help="Alias for --save-candidates; saves to staging, not main analytics.",
    )

    diagnose_alfred_parser = subparsers.add_parser(
        "diagnose-alfred",
        help="Check whether one public Alfreð page contains the full job inventory.",
    )
    diagnose_alfred_parser.add_argument(
        "--url",
        help="Optional saved HTML page to diagnose instead of fetching live.",
    )

    candidates_parser = subparsers.add_parser(
        "candidates",
        help="View or promote collected job candidates.",
    )
    candidates_subparsers = candidates_parser.add_subparsers(
        dest="candidates_command",
        required=True,
    )

    candidates_list_parser = candidates_subparsers.add_parser(
        "list",
        help="List collected candidates by rule relevance.",
    )
    candidates_list_parser.add_argument(
        "--label",
        choices=CANDIDATE_LABEL_FILTERS,
        default="actionable",
        help="Which candidate group to show.",
    )
    candidates_list_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of candidates to show.",
    )

    candidates_status_parser = candidates_subparsers.add_parser(
        "set-status",
        help="Set the human review status for one candidate.",
    )
    candidates_status_parser.add_argument(
        "--candidate-id",
        type=int,
        required=True,
        help="Collected candidate ID to update.",
    )
    candidates_status_parser.add_argument(
        "--status",
        choices=CANDIDATE_REVIEW_STATUSES,
        required=True,
        help="Review status to save.",
    )

    candidates_promote_parser = candidates_subparsers.add_parser(
        "promote",
        help="Promote one staged candidate into the curated listing tables.",
    )
    candidates_promote_parser.add_argument(
        "--candidate-id",
        type=int,
        required=True,
        help="Collected candidate ID to promote.",
    )

    candidates_enrich_parser = candidates_subparsers.add_parser(
        "enrich",
        help="Run AI enrichment for staged candidates.",
    )
    candidates_enrich_parser.add_argument(
        "--label",
        choices=CANDIDATE_LABEL_FILTERS,
        default="actionable",
        help="Which candidate group to enrich.",
    )
    candidates_enrich_parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of candidates to enrich.",
    )
    candidates_enrich_parser.add_argument(
        "--candidate-id",
        type=int,
        help="Enrich one specific candidate ID.",
    )
    candidates_enrich_parser.add_argument(
        "--force",
        action="store_true",
        help="Create a new enrichment even if this candidate already has one.",
    )
    candidates_enrich_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be sent to AI without calling the API.",
    )

    candidates_ai_results_parser = candidates_subparsers.add_parser(
        "ai-results",
        help="List the latest AI enrichment results.",
    )
    candidates_ai_results_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of AI results to show.",
    )

    candidates_ai_review_parser = candidates_subparsers.add_parser(
        "ai-review",
        help="Show candidates grouped by AI recommended action.",
    )
    candidates_ai_review_parser.add_argument(
        "--action",
        choices=AI_REVIEW_ACTION_FILTERS,
        default="all",
        help="Which AI review action to show.",
    )
    candidates_ai_review_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of candidates to show.",
    )

    candidates_apply_ai_status_parser = candidates_subparsers.add_parser(
        "apply-ai-status",
        help="Preview or apply review-status changes from AI recommendations.",
    )
    candidates_apply_ai_status_parser.add_argument(
        "--action",
        choices=AI_STATUS_ACTION_FILTERS,
        required=True,
        help="Which AI recommended action to update.",
    )
    candidates_apply_ai_status_parser.add_argument(
        "--status",
        choices=AI_BULK_REVIEW_STATUSES,
        required=True,
        help="Review status to apply to matching candidates.",
    )
    candidates_apply_ai_status_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of candidates to update.",
    )
    candidates_apply_ai_status_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually save the changes. Omit this for a dry run.",
    )

    candidates_ai_prompt_parser = candidates_subparsers.add_parser(
        "ai-prompt",
        help="Print a no-cost ChatGPT prompt for one staged candidate.",
    )
    candidates_ai_prompt_parser.add_argument(
        "--candidate-id",
        type=int,
        required=True,
        help="Candidate ID to turn into a manual AI prompt.",
    )
    candidates_ai_prompt_parser.add_argument(
        "--output-file",
        help="Optional file path to write the prompt instead of printing it.",
    )

    candidates_import_ai_result_parser = candidates_subparsers.add_parser(
        "import-ai-result",
        help="Import a pasted ChatGPT JSON enrichment result.",
    )
    candidates_import_ai_result_parser.add_argument(
        "--candidate-id",
        type=int,
        required=True,
        help="Candidate ID the enrichment result belongs to.",
    )
    candidates_import_ai_result_parser.add_argument(
        "--file",
        required=True,
        help="JSON result file to import. Use - to read from stdin.",
    )
    candidates_import_ai_result_parser.add_argument(
        "--model-label",
        default="chatgpt-manual",
        help="Label describing the manual ChatGPT/model source.",
    )

    candidates_ai_bulk_prompt_parser = candidates_subparsers.add_parser(
        "ai-bulk-prompt",
        help="Print a no-cost ChatGPT prompt for several staged candidates.",
    )
    candidates_ai_bulk_prompt_parser.add_argument(
        "--label",
        choices=CANDIDATE_LABEL_FILTERS,
        default="actionable",
        help="Which candidate group to include.",
    )
    candidates_ai_bulk_prompt_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of candidates to include.",
    )
    candidates_ai_bulk_prompt_parser.add_argument(
        "--output-file",
        help="Optional file path to write the prompt instead of printing it.",
    )
    candidates_ai_bulk_prompt_parser.add_argument(
        "--force",
        action="store_true",
        help="Include candidates that already have AI results.",
    )

    candidates_import_ai_results_parser = candidates_subparsers.add_parser(
        "import-ai-results",
        help="Import pasted ChatGPT JSON enrichment results for several candidates.",
    )
    candidates_import_ai_results_parser.add_argument(
        "--file",
        required=True,
        help="Bulk JSON result file to import. Use - to read from stdin.",
    )
    candidates_import_ai_results_parser.add_argument(
        "--model-label",
        default="chatgpt-manual",
        help="Label describing the manual ChatGPT/model source.",
    )

    applications_parser = subparsers.add_parser(
        "applications",
        help="View or edit application workflow rows.",
    )
    applications_subparsers = applications_parser.add_subparsers(
        dest="applications_command",
        required=True,
    )

    applications_list_parser = applications_subparsers.add_parser(
        "list",
        help="List application workflow rows.",
    )
    applications_list_parser.add_argument(
        "--status",
        choices=APPLICATION_LIST_FILTERS,
        default="tracked",
        help="Which application status group to show.",
    )
    applications_list_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of rows to show.",
    )

    applications_queue_parser = applications_subparsers.add_parser(
        "queue",
        help="Rank actionable jobs for application work.",
    )
    applications_queue_parser.add_argument(
        "--track",
        choices=sorted(TRACK_SCORE_COLUMNS),
        default="data",
        help="Scoring track to prioritize.",
    )
    applications_queue_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of rows to show.",
    )
    applications_queue_parser.add_argument(
        "--include-expired",
        action="store_true",
        help="Include listings whose deadline has already passed.",
    )

    applications_set_parser = applications_subparsers.add_parser(
        "set",
        help="Create or update one application workflow row.",
    )
    applications_set_parser.add_argument(
        "--job-id",
        type=int,
        required=True,
        help="Job listing ID to update.",
    )
    applications_set_parser.add_argument(
        "--status",
        choices=APPLICATION_STATUSES,
        required=True,
        help="Application status to save.",
    )
    applications_set_parser.add_argument(
        "--applied-on",
        help="Application date in YYYY-MM-DD format.",
    )
    applications_set_parser.add_argument(
        "--application-url",
        help="Application URL, if different from the listing URL.",
    )
    applications_set_parser.add_argument(
        "--cv-version",
        help="CV version used or planned.",
    )
    applications_set_parser.add_argument(
        "--cover-letter-version",
        help="Cover letter version used or planned.",
    )
    applications_set_parser.add_argument(
        "--follow-up-date",
        help="Follow-up date in YYYY-MM-DD format.",
    )
    applications_set_parser.add_argument(
        "--next-action",
        help="Next practical action.",
    )
    applications_set_parser.add_argument(
        "--notes",
        help="Application notes.",
    )

    applications_due_parser = applications_subparsers.add_parser(
        "due",
        help="Show application actions and deadlines due soon.",
    )
    applications_due_parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days ahead to include.",
    )
    applications_due_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of rows to show.",
    )
    applications_due_parser.add_argument(
        "--include-overdue",
        action="store_true",
        help="Include job deadlines that are already past.",
    )

    applications_remove_parser = applications_subparsers.add_parser(
        "remove",
        help="Remove one application workflow row.",
    )
    applications_remove_parser.add_argument(
        "--job-id",
        type=int,
        required=True,
        help="Job listing ID to remove from application tracking.",
    )

    profile_skill_parser = subparsers.add_parser(
        "profile-skill",
        help="View or edit the skill profile used by gap analysis.",
    )
    profile_skill_subparsers = profile_skill_parser.add_subparsers(
        dest="profile_skill_command",
        required=True,
    )

    profile_skill_list_parser = profile_skill_subparsers.add_parser(
        "list",
        help="List skills currently in your profile.",
    )
    profile_skill_list_parser.add_argument(
        "--level",
        choices=["all", *PROFILE_LEVELS],
        default="all",
        help="Only show profile skills at this level.",
    )
    profile_skill_list_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of profile skills to show.",
    )

    profile_skill_set_parser = profile_skill_subparsers.add_parser(
        "set",
        help="Create or update one profile skill.",
    )
    profile_skill_set_parser.add_argument(
        "--skill",
        required=True,
        help="Skill name to create or update.",
    )
    profile_skill_set_parser.add_argument(
        "--level",
        choices=PROFILE_LEVELS,
        required=True,
        help="Current level for this skill.",
    )
    profile_skill_set_parser.add_argument(
        "--evidence",
        help="Short explanation for why this skill belongs in the profile.",
    )
    profile_skill_set_parser.add_argument(
        "--exclude-from-gaps",
        action="store_true",
        help="Keep the skill in the profile but exclude it from gap analysis.",
    )

    profile_skill_remove_parser = profile_skill_subparsers.add_parser(
        "remove",
        help="Remove one skill from your profile.",
    )
    profile_skill_remove_parser.add_argument(
        "--skill",
        required=True,
        help="Skill name to remove from the profile.",
    )

    duplicate_candidates_parser = subparsers.add_parser(
        "duplicate-candidates",
        help="Show possible duplicate listings that need human review.",
    )
    duplicate_candidates_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of candidate pairs to show.",
    )

    skill_gaps_parser = subparsers.add_parser(
        "skill-gaps",
        help="Show recurring requested skills by profile status.",
    )
    skill_gaps_parser.add_argument(
        "--status",
        choices=["missing", "learning", "covered", "all"],
        default="missing",
        help="Which skill status to show.",
    )
    skill_gaps_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of skills to show.",
    )

    job_skill_match_parser = subparsers.add_parser(
        "job-skill-match",
        help="Show top jobs with profile skill coverage.",
    )
    job_skill_match_parser.add_argument(
        "--track",
        choices=sorted(TRACK_SCORE_COLUMNS),
        default="data",
        help="Scoring track to rank by.",
    )
    job_skill_match_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of jobs to show.",
    )

    top_jobs_parser = subparsers.add_parser(
        "top-jobs",
        help="Show prioritized jobs from the enriched view.",
    )
    top_jobs_parser.add_argument(
        "--track",
        choices=sorted(TRACK_SCORE_COLUMNS),
        default="data",
        help="Scoring track to rank by.",
    )
    top_jobs_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of jobs to show.",
    )

    args = parser.parse_args()

    if args.command == "check-db":
        check_db_connection.main()
    elif args.command == "setup":
        setup_database.main()
    elif args.command == "import-raw":
        import_raw_jobs.main()
    elif args.command == "apply-categories":
        apply_category_assignments.main()
    elif args.command == "analyze":
        run_analysis.main()
    elif args.command == "refresh":
        setup_database.main()
        import_raw_jobs.main()
        apply_category_assignments.main()
        run_analysis.main()
    elif args.command == "web":
        from career_radar.web_app import run_server

        run_server(host=args.host, port=args.port)
    elif args.command == "import-chatgpt-inbox":
        result = import_chatgpt_inbox(
            file_paths=args.files,
            csv_urls=args.urls,
            save_ai_results=not args.skip_ai_results,
            model_label=args.model_label,
            dry_run=args.dry_run,
        )
        print_inbox_import_result(result)
    elif args.command == "collect-alfred":
        listings = collect_alfred_jobs()
        print_collected_listings_preview(
            listings,
            limit=args.preview_limit,
        )
        if args.should_save_candidates:
            result = save_collected_candidates(listings)
            print_candidate_save_result(result)
        else:
            print("Preview only. Add --save-candidates to stage these listings.")
    elif args.command == "diagnose-alfred":
        html_text = (
            open(args.url, encoding="utf-8").read()
            if args.url
            else fetch_alfred_jobs_page()
        )
        diagnostic = diagnose_alfred_jobs_page(html_text)
        print(f"Displayed jobs: {diagnostic.displayed_job_count or 'unknown'}")
        print(f"Payload total jobs: {diagnostic.payload_total_count or 'unknown'}")
        print(f"Payload total pages: {diagnostic.payload_total_pages or 'unknown'}")
        print(f"Embedded jobs: {diagnostic.embedded_job_count}")
        print(f"Unique listing slugs: {diagnostic.unique_slug_count}")
        print(f"Duplicate slugs: {diagnostic.duplicate_slug_count}")
        print(f"Jobs missing slugs: {diagnostic.jobs_missing_slug_count}")
        print(
            "One-request inventory appears complete: "
            f"{'yes' if diagnostic.appears_complete else 'no'}"
        )
    elif args.command == "candidates":
        if args.candidates_command == "list":
            print_candidates(label=args.label, limit=args.limit)
        elif args.candidates_command == "set-status":
            set_candidate_review_status(
                candidate_id=args.candidate_id,
                review_status=args.status,
            )
        elif args.candidates_command == "promote":
            promote_candidate(candidate_id=args.candidate_id)
        elif args.candidates_command == "enrich":
            enrich_candidates(
                label=args.label,
                limit=args.limit,
                candidate_id=args.candidate_id,
                force=args.force,
                dry_run=args.dry_run,
            )
        elif args.candidates_command == "ai-results":
            print_ai_enrichments(limit=args.limit)
        elif args.candidates_command == "ai-review":
            print_ai_review_queue(action=args.action, limit=args.limit)
        elif args.candidates_command == "apply-ai-status":
            apply_ai_review_status(
                action=args.action,
                status=args.status,
                limit=args.limit,
                apply=args.apply,
            )
        elif args.candidates_command == "ai-prompt":
            print_manual_ai_prompt(
                candidate_id=args.candidate_id,
                output_file=args.output_file,
            )
        elif args.candidates_command == "import-ai-result":
            import_manual_ai_enrichment(
                candidate_id=args.candidate_id,
                result_file=args.file,
                model_label=args.model_label,
            )
        elif args.candidates_command == "ai-bulk-prompt":
            print_bulk_manual_ai_prompt(
                label=args.label,
                limit=args.limit,
                output_file=args.output_file,
                force=args.force,
            )
        elif args.candidates_command == "import-ai-results":
            import_bulk_manual_ai_enrichments(
                result_file=args.file,
                model_label=args.model_label,
            )
    elif args.command == "applications":
        if args.applications_command == "list":
            print_applications(status=args.status, limit=args.limit)
        elif args.applications_command == "queue":
            print_application_queue(
                track=args.track,
                limit=args.limit,
                include_expired=args.include_expired,
            )
        elif args.applications_command == "set":
            set_application(
                job_listing_id=args.job_id,
                status=args.status,
                applied_on=args.applied_on,
                application_url=args.application_url,
                cv_version=args.cv_version,
                cover_letter_version=args.cover_letter_version,
                follow_up_date=args.follow_up_date,
                next_action=args.next_action,
                notes=args.notes,
            )
        elif args.applications_command == "due":
            print_due_applications(
                days=args.days,
                limit=args.limit,
                include_overdue=args.include_overdue,
            )
        elif args.applications_command == "remove":
            remove_application(job_listing_id=args.job_id)
    elif args.command == "profile-skill":
        if args.profile_skill_command == "list":
            print_profile_skills(level=args.level, limit=args.limit)
        elif args.profile_skill_command == "set":
            set_profile_skill(
                skill_name=args.skill,
                level=args.level,
                evidence=args.evidence,
                include_in_gap_analysis=not args.exclude_from_gaps,
            )
        elif args.profile_skill_command == "remove":
            remove_profile_skill(skill_name=args.skill)
    elif args.command == "duplicate-candidates":
        print_duplicate_candidates(limit=args.limit)
    elif args.command == "skill-gaps":
        print_skill_gaps(status=args.status, limit=args.limit)
    elif args.command == "job-skill-match":
        print_job_skill_matches(track=args.track, limit=args.limit)
    elif args.command == "top-jobs":
        print_top_jobs(track=args.track, limit=args.limit)


def run() -> None:
    """Run the CLI with readable setup/validation errors."""

    try:
        main()
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    run()
