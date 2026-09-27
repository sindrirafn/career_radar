import copy
import csv
import io
import os
import tempfile
import unittest
from unittest.mock import patch

from career_radar.ai_enrichment import normalize_ai_payload
from career_radar.ingestion import canonicalize_url, split_skills
from career_radar.sources import chatgpt_inbox as inbox
from career_radar.sources.alfred import diagnose_alfred_jobs_page, parse_alfred_jobs_page
from career_radar.sql import statements_from
from career_radar.web_app import listing_href, message_location
from scripts.import_raw_jobs import validate_columns, REQUIRED_COLUMNS
from tests.helpers import inbox_values, write_inbox


class ValidationTests(unittest.TestCase):
    def test_canonical_urls_keep_meaningful_query_parameters(self):
        self.assertEqual(
            canonicalize_url("https://www.alfred.is/en/starf/test/?utm_source=x&id=2#top"),
            "https://alfred.is/starf/test?id=2",
        )
        self.assertNotEqual(canonicalize_url("https://example.com/?id=1"),
                            canonicalize_url("https://example.com/?id=2"))

    def test_skill_deduplication(self):
        self.assertEqual(split_skills("SQL; sql ; Power  BI; power bi;;"), ["SQL", "Power  BI"])

    def test_sql_splitter_keeps_quoted_function_body(self):
        self.assertEqual(len(statements_from("select ';'; -- ignored;\nselect 'it''s;ok';")), 2)
        from scripts.setup_database import SQL_FILES
        self.assertEqual(len(statements_from(SQL_FILES[0].read_text())), 1)

    def test_ai_payload_validates_all_stored_shapes(self):
        valid = inbox.ai_payload_from_inbox_row(inbox.InboxRow("test", 2, inbox_values()))
        self.assertEqual(normalize_ai_payload(valid), valid)
        for field, value in (
            ("fit_score", True), ("priority_score", 101), ("fit_tier", "great"),
            ("relevance_label", "maybe"), ("tracks", ["unknown"]),
            ("explicit_skills", [3]), ("evidence", None),
            ("inferred_skills", ["Python"]),
            ("inferred_skills", [{"skill": "Python", "confidence": "certain", "evidence": "text"}]),
            ("inferred_skills", [{"skill": "Python", "confidence": "high"}]),
        ):
            with self.subTest(field=field, value=value):
                payload = copy.deepcopy(valid)
                payload[field] = value
                with self.assertRaises(RuntimeError):
                    normalize_ai_payload(payload)

    def test_explicit_file_dry_run_never_connects_or_fetches_configured_url(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_inbox(directory)
            with patch.dict(os.environ, {inbox.DEFAULT_CSV_URL_ENV_VAR: "https://example.com/private.csv"}), \
                 patch.object(inbox, "fetch_inbox_csv_url") as fetch, \
                 patch("career_radar.db.connect") as connect:
                result = inbox.import_chatgpt_inbox(file_paths=[str(path)], dry_run=True)
            self.assertEqual(result.source_row_count, 1)
            fetch.assert_not_called()
            connect.assert_not_called()

    def test_duplicate_urls_across_files_count_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_inbox(directory)
            result = inbox.import_chatgpt_inbox(file_paths=[str(path), str(path)], dry_run=True)
        self.assertEqual(result.source_row_count, 1)
        self.assertEqual(result.skipped_duplicate_url_count, 1)

    def test_inbox_rejects_duplicate_headers_and_ragged_rows(self):
        headers = sorted(inbox.REQUIRED_COLUMNS)
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            inbox.validate_columns("test.csv", headers + [" title "])
        for row in (["x"] * (len(headers) - 1), ["x"] * (len(headers) + 1)):
            stream = io.StringIO()
            writer = csv.writer(stream)
            writer.writerow(headers)
            writer.writerow(row)
            stream.seek(0)
            with self.assertRaisesRegex(RuntimeError, "test.csv:2.*width"):
                inbox.read_inbox_rows_from_reader("test.csv", csv.DictReader(stream))

    def test_raw_csv_rejects_duplicate_headers(self):
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            validate_columns("test.csv", sorted(REQUIRED_COLUMNS) + ["title"])

    def test_redirects_remain_local(self):
        for target in ("https://evil.example/path", "//evil.example/path", "/\\evil.example", "/\r\nheader:x"):
            with self.subTest(target=target):
                self.assertEqual(message_location("saved", return_to=target), "/?notice=saved")
        self.assertEqual(message_location("saved", return_to="/?view=archive&q=a"),
                         "/?view=archive&q=a&notice=saved")

    def test_historical_links_cannot_execute_script(self):
        for url in ("javascript:alert(1)", "data:text/html,test", "//evil.example", "https://[bad"):
            self.assertEqual(listing_href(url), "#")
        self.assertEqual(listing_href('https://example.com/?a=1&b=2'),
                         'https://example.com/?a=1&amp;b=2')

    def test_legacy_collector_parser_still_supported(self):
        import json
        payload = {"props": {"pageProps": {"jobs": {"jobs": [{
            "slug": "test", "title": "Engineer", "brand": {"name": "Example"},
            "published": "2026-09-10T09:00:00Z", "bodyhtml": "<p>Python</p>",
        }]}}}}
        jobs = parse_alfred_jobs_page('<script id="__NEXT_DATA__" type="application/json">'
                                     + json.dumps(payload) + '</script>')
        self.assertEqual(jobs[0].posted_date, "2026-09-10")
        self.assertEqual(jobs[0].description, "Python")

    def test_alfred_diagnostic_compares_embedded_and_displayed_counts(self):
        import json
        jobs = [
            {"slug": "first", "title": "First", "brand": {"name": "One"}},
            {"slug": "second", "title": "Second", "brand": {"name": "Two"}},
        ]
        payload = {"props": {"pageProps": {"jobs": {
            "jobs": jobs, "totalCount": 2, "totalPages": 1,
        }}}}
        html = (
            '<main>Öll störf (2)</main>'
            '<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(payload)
            + '</script>'
        )
        diagnostic = diagnose_alfred_jobs_page(html)
        self.assertTrue(diagnostic.appears_complete)
        self.assertEqual(diagnostic.embedded_job_count, 2)
        self.assertEqual(diagnostic.unique_slug_count, 2)
        self.assertEqual(diagnostic.payload_total_pages, 1)
