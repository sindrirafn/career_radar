"""Opt-in PostgreSQL tests; each test creates and removes only its own schema."""
import contextlib
import io
import os
import tempfile
import unittest
import uuid
from datetime import date
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from career_radar import candidates, ingestion, web_app
from career_radar.applications import print_due_applications, set_application
from career_radar.candidate_deduplication import apply_high_confidence_candidate_deduplication
from career_radar.db import connect, transaction
from career_radar.listings import JobListingInput
from career_radar.profile import set_profile_skill
from career_radar.sources import chatgpt_inbox as inbox
from scripts import apply_category_assignments, import_raw_jobs, setup_database
from tests.helpers import inbox_values, write_inbox


TEST_URL = os.environ.get("CAREER_RADAR_TEST_DATABASE_URL")


@unittest.skipUnless(TEST_URL, "Set CAREER_RADAR_TEST_DATABASE_URL for PostgreSQL tests.")
class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.schema = "career_radar_test_" + uuid.uuid4().hex
        with psycopg.connect(TEST_URL) as connection:
            connection.execute(sql.SQL("create schema {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.drop_schema)
        self.url_patch = patch("career_radar.db.get_database_url", return_value=make_conninfo(
            TEST_URL, options=f"-c search_path={self.schema}"))
        self.url_patch.start()
        self.addCleanup(self.url_patch.stop)
        self.output = io.StringIO()
        self.stdout = contextlib.redirect_stdout(self.output)
        self.stdout.__enter__()
        self.addCleanup(self.stdout.__exit__, None, None, None)
        setup_database.main()

    def drop_schema(self):
        with psycopg.connect(TEST_URL) as connection:
            connection.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(self.schema)))

    def query(self, statement, params=()):
        with connect() as connection:
            return connection.execute(statement, params).fetchall()

    def stage(self, number=1, **kwargs):
        listing = JobListingInput(title=f"Engineer {number}", company="Test",
                                  source="Test", url=f"https://example.com/{number}", **kwargs)
        candidates.save_collected_candidates([listing])
        return self.query("select id from collected_job_candidates where url=%s", (listing.url,))[0][0]

    def application(self, job_id, status="applied", **overrides):
        params = dict(job_listing_id=job_id, status=status, applied_on="2026-09-01",
                      application_url=None, cv_version="my-cv", cover_letter_version=None,
                      follow_up_date=None, next_action="My next action", notes="My private notes")
        params.update(overrides)
        return set_application(**params)

    def test_repeated_setup_import_preserves_user_state(self):
        import_raw_jobs.main()
        before = self.query("select id, canonical_url, first_seen_at from raw_job_listings order by id")
        self.assertEqual(len(before), 59)
        self.application(before[0][0])
        application = self.query("select * from job_applications")
        set_profile_skill("SQL", "strong", "Personal evidence", False)
        setup_database.main()
        import_raw_jobs.main()
        self.assertEqual(before, self.query("select id, canonical_url, first_seen_at from raw_job_listings order by id"))
        self.assertEqual(application, self.query("select * from job_applications"))
        self.assertEqual(self.query("select proficiency_level, evidence, include_in_gap_analysis from profile_skills join skills on skills.id=skill_id where normalized_name='sql'"),
                         [("strong", "Personal evidence", False)])

    def test_invalid_dates_remain_raw_and_all_dashboard_views_render(self):
        candidate_id = self.stage(deadline="2026-02-30", posted_date="2026-99-01")
        candidates.promote_candidate_to_listing(candidate_id)
        self.assertEqual(self.query("select deadline_raw, deadline, posted_date from job_listings_v1"),
                         [("2026-02-30", None, None)])
        self.assertEqual(self.query("select career_radar_iso_date('2024-02-29'), career_radar_iso_date('0000-01-01')"),
                         [(date(2024, 2, 29), None)])
        for view in ("home", "applications", "review", "research", "archive", "duplicates"):
            self.assertIn("<!doctype html>", web_app.render_dashboard({"view": [view]}))
        for view, in self.query("select table_name from information_schema.views where table_schema=current_schema()"):
            self.query(sql.SQL("select * from {} limit 1").format(sql.Identifier(view)))

    def test_promotion_rolls_back_ingestion_if_category_assignment_fails(self):
        candidate_id = self.stage(skills_mentioned="SQL")
        with patch.object(candidates, "assign_ai_categories", side_effect=RuntimeError("forced failure")):
            with self.assertRaisesRegex(RuntimeError, "forced failure"):
                candidates.promote_candidate_to_listing(candidate_id)
        self.assertEqual(self.query("select count(*) from raw_job_listings"), [(0,)])
        self.assertEqual(self.query("select review_status, promoted_job_listing_id from collected_job_candidates"),
                         [("unreviewed", None)])

    def test_inbox_failure_rolls_back_candidates_and_ai_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_inbox(directory, inbox_values(), inbox_values(2))
            original = inbox.save_ai_enrichment
            calls = 0
            def fail_second(**kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("forced AI save failure")
                return original(**kwargs)
            with patch.object(inbox, "save_ai_enrichment", side_effect=fail_second):
                with self.assertRaisesRegex(RuntimeError, "forced AI"):
                    inbox.import_chatgpt_inbox(file_paths=[str(path)])
        self.assertEqual(self.query("select count(*) from collected_job_candidates"), [(0,)])
        self.assertEqual(self.query("select count(*) from candidate_ai_enrichments"), [(0,)])

    def test_historical_repeat_import_preserves_application_and_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_inbox(directory, inbox_values(notes="APPLIED. Historical record."))
            first = inbox.import_chatgpt_inbox(file_paths=[str(path)])
            self.assertEqual(first.historical_applied_count, 1)
            job_id = self.query("select id from raw_job_listings")[0][0]
            self.application(job_id, status="interviewing")
            saved = self.query("select * from job_applications")
            second = inbox.import_chatgpt_inbox(file_paths=[str(path)])
        self.assertEqual(second.saved_ai_enrichment_count, 0)
        self.assertEqual(second.skipped_unchanged_ai_enrichment_count, 1)
        self.assertEqual(second.historical_applied_count, 0)
        self.assertEqual(self.query("select * from job_applications"), saved)

    def test_historical_import_does_not_override_rejected_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_inbox(directory)
            inbox.import_chatgpt_inbox(file_paths=[str(path)])
            candidate_id = self.query("select id from collected_job_candidates")[0][0]
            candidates.set_candidate_review_status(candidate_id, "rejected")
            write_inbox(directory, inbox_values(notes="APPLIED. Imported old note."))
            inbox.import_chatgpt_inbox(file_paths=[str(path)])
        self.assertEqual(self.query("select review_status from collected_job_candidates"), [("rejected",)])
        self.assertEqual(self.query("select count(*) from raw_job_listings"), [(0,)])

    def test_replayed_dashboard_action_preserves_later_application_decision(self):
        candidate_id = self.stage()
        web_app.CareerRadarHandler.apply_candidate_action(candidate_id, "add_to_applications")
        job_id = self.query("select id from raw_job_listings")[0][0]
        self.application(job_id, status="interviewing")
        saved = self.query("select * from job_applications")
        web_app.CareerRadarHandler.apply_candidate_action(candidate_id, "skip")
        self.assertEqual(self.query("select * from job_applications"), saved)

    def test_batch_review_rolls_back_when_one_candidate_is_missing(self):
        candidate_id = self.stage()
        handler = object.__new__(web_app.CareerRadarHandler)
        with self.assertRaisesRegex(RuntimeError, "Candidate not found"):
            handler.handle_candidate_batch_action({"candidate_id": [str(candidate_id), "999999"],
                                                  "action": ["add_to_applications"]})
        self.assertEqual(self.query("select count(*) from job_applications"), [(0,)])
        self.assertEqual(self.query("select count(*) from raw_job_listings"), [(0,)])

    def test_duplicate_merge_reattaches_promoted_candidates(self):
        first = self.stage()
        first_job = candidates.promote_candidate_to_listing(first).job_listing_id
        self.application(first_job)
        second = self.stage(2)
        second_job = candidates.promote_candidate_to_listing(second).job_listing_id
        with connect() as connection:
            connection.execute("update raw_job_listings set url='https://example.com/1?utm_source=old' where id=%s", (second_job,))
            self.assertEqual(ingestion.merge_duplicate_listings(connection.cursor()), 1)
        self.assertEqual(self.query("select promoted_job_listing_id from collected_job_candidates order by id"),
                         [(first_job,), (first_job,)])
        self.assertEqual(self.query("select job_listing_id from job_applications"), [(first_job,)])

    def test_missing_locations_are_not_duplicate_evidence(self):
        rows = [JobListingInput(title="Engineer", company="Test", source="Test",
                                url=f"https://example.com/duplicate/{i}", posted_date="2026-09-10") for i in range(2)]
        candidates.save_collected_candidates(rows)
        self.assertEqual(self.query("select count(*) from candidate_duplicate_reviews"), [(0,)])
        with connect() as connection:
            connection.execute("update collected_job_candidates set location='Reykjavík'")
        self.assertEqual(apply_high_confidence_candidate_deduplication().linked_count, 1)
        self.assertEqual(apply_high_confidence_candidate_deduplication().linked_count, 0)

    def test_category_seeds_use_canonical_urls_and_preserve_manual_provenance(self):
        import_raw_jobs.main()
        apply_category_assignments.main()
        self.assertEqual(self.query("select count(*) from job_listing_categories where source='seed'"), [(30,)])
        with connect() as connection:
            connection.execute("update job_listing_categories set source='manual'")
        apply_category_assignments.main()
        self.assertEqual(self.query("select count(*) from job_listing_categories where source='seed'"), [(0,)])

    def test_research_and_not_applying_roles_are_not_due(self):
        for number, status in enumerate(("tracking_only", "not_applying", "interested")):
            candidate_id = self.stage(number, deadline=date.today().isoformat())
            job_id = candidates.promote_candidate_to_listing(candidate_id).job_listing_id
            self.application(job_id, status=status, follow_up_date=date.today().isoformat())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            print_due_applications(7, 20, False)
        self.assertNotIn("Engineer 0", out.getvalue())
        self.assertNotIn("Engineer 1", out.getvalue())
        self.assertIn("Engineer 2", out.getvalue())

    def test_excluded_profile_skills_match_between_cli_and_powerbi(self):
        candidate_id = self.stage(skills_mentioned="SQL")
        candidates.promote_candidate_to_listing(candidate_id)
        set_profile_skill("SQL", "strong", "Private evidence", False)
        self.assertEqual(self.query("select profile_status from skill_gap_summary_v1 where normalized_name='sql'"), [("missing",)])
        self.assertEqual(self.query("select profile_status from powerbi_skill_gaps_v1 where normalized_name='sql'"), [("missing",)])

    def test_nested_failure_resets_transaction_context(self):
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with transaction():
                self.stage()
                raise RuntimeError("rollback")
        self.assertEqual(self.query("select count(*) from collected_job_candidates"), [(0,)])
        self.stage(2)
        self.assertEqual(self.query("select count(*) from collected_job_candidates"), [(1,)])

    def test_concurrent_review_actions_create_one_workflow(self):
        from concurrent.futures import ThreadPoolExecutor
        candidate_id = self.stage()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda _: web_app.CareerRadarHandler.apply_candidate_action(candidate_id, "add_to_applications"),
                range(2),
            ))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.query("select count(*) from raw_job_listings"), [(1,)])
        self.assertEqual(self.query("select count(*) from job_applications"), [(1,)])

    def test_merge_refuses_to_discard_two_application_records(self):
        for number in (1, 2):
            candidate_id = self.stage(number)
            job_id = candidates.promote_candidate_to_listing(candidate_id).job_listing_id
            self.application(job_id)
        before = self.query("select * from job_applications order by id")
        with connect() as connection:
            connection.execute("update raw_job_listings set url='https://example.com/same'")
        with self.assertRaisesRegex(RuntimeError, "manual merge"):
            with connect() as connection:
                ingestion.merge_duplicate_listings(connection.cursor())
        self.assertEqual(before, self.query("select * from job_applications order by id"))
        self.assertEqual(self.query("select count(*) from raw_job_listings"), [(2,)])
