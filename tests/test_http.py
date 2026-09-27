import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from career_radar.web_app import CareerRadarHandler


class FormHandler(CareerRadarHandler):
    def handle_candidate_action(self, form):
        self.server.actions.append(form)
        self.send_response(204)
        self.end_headers()


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FormHandler)
        cls.server.actions = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request(self, method="POST", headers=None, body="candidate_id=1&action=skip", path="/candidate-action", include_body=False):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            response_body = response.read()
            return (response.status, response_body) if include_body else response.status
        finally:
            connection.close()

    def test_same_origin_forms_work(self):
        before = len(self.server.actions)
        status = self.request(headers={"Content-Type": "application/x-www-form-urlencoded",
                                       "Origin": f"http://127.0.0.1:{self.server.server_port}"})
        self.assertEqual(status, 204)
        self.assertEqual(len(self.server.actions), before + 1)

    def test_cross_site_forms_cannot_mutate(self):
        before = len(self.server.actions)
        for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"},
                        {"Sec-Fetch-Site": "cross-site"}, {"Host": "evil.example"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request(headers=headers), 403)
        self.assertEqual(len(self.server.actions), before)

    def test_malformed_and_oversized_forms_fail_cleanly(self):
        for length in ("-1", "abc", "65537"):
            self.assertEqual(self.request(headers={"Content-Length": length}), 400)
        self.assertEqual(self.request(headers={"Content-Type": "text/plain"}), 400)

    def test_dashboard_and_fragments_have_correct_content(self):
        with patch("career_radar.web_app.render_dashboard", return_value="<html>test</html>"), \
             patch("career_radar.web_app.render_new_match_fragment", return_value="<div>test</div>"):
            self.assertEqual(self.request("GET", body=None, path="/"), 200)
            self.assertEqual(self.request("GET", body=None, path="/new-matches?page=1"), 200)
            self.assertEqual(self.request("GET", body=None, path="/unknown"), 404)
            self.assertEqual(self.request("GET", headers={"Host": "evil.example"}, body=None, path="/"), 403)

    def test_today_api_is_a_same_origin_json_surface(self):
        payload = {"generated_at": "2026-09-14", "counts": {}, "up_next": [], "new_matches": []}
        with patch("career_radar.web_app.get_today_api_payload", return_value=payload):
            status, body = self.request("GET", body=None, path="/api/v1/today", include_body=True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), payload)
        self.assertEqual(self.request("GET", headers={"Host": "evil.example"}, body=None, path="/api/v1/today"), 403)

    def test_refresh_api_reuses_the_existing_inbox_import(self):
        result = SimpleNamespace(
            source_row_count=3,
            saved_ai_enrichment_count=1,
            skipped_unchanged_ai_enrichment_count=2,
            historical_applied_count=0,
            historical_not_applying_count=0,
        )
        headers = {
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{self.server.server_port}",
        }
        with patch("career_radar.web_app.import_chatgpt_inbox", return_value=result) as refresh:
            status, body = self.request(
                headers=headers,
                body="{}",
                path="/api/v1/refresh",
                include_body=True,
            )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["refreshed"])
        refresh.assert_called_once_with()

    def test_candidate_decision_api_reuses_existing_application_stages(self):
        headers = {
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{self.server.server_port}",
        }
        with patch.object(FormHandler, "apply_candidate_action", return_value=("Example", "Role")) as action:
            status, body = self.request(
                headers=headers,
                body='{"candidate_id": 1, "decision": "not_interested"}',
                path="/api/v1/candidate-action",
                include_body=True,
            )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["saved"])
        action.assert_called_once_with(1, "not_interested")
