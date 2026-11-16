"""Saved history consumer/transport tests; no network or model invocation.

Run: python3 plugins/shared/scripts/tests/test_report_history.py
The renderer probe uses Node's built-ins and a DOM shim (no npm dependencies).
"""

import contextlib
import copy
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("report_history_consumer", SCRIPTS / "create-report.py")
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)
STAMP = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
UNTRUSTED = '</script><!-- <img src=x onerror="throw 1"> & historical evidence'


def payload(html):
    # Parse only the JSON literal; never evaluate script to obtain report data.
    return json.loads(html.split("window.REPORT_DATA = ", 1)[1].split(";</script>", 1)[0])


def issue():
    return {
        "number": 1, "title": "Node boot fails", "root_cause": "Activation races device discovery",
        "confidence": "high", "severity": "HIGH", "failure_type": "test",
        "scenarios": ["boot"], "causal_chain": [{"cause": "Device unavailable", "quote": "device not ready"}],
        "job_count": 1, "affected_jobs": [{"name": "periodic-boot-4.20", "date": "2026-10-28",
                                           "url": "https://example.invalid/current/3"}],
    }


def history_for(current, component="microshift"):
    accepted = {
        "score": 93, "verdict": "same_cause", "title": "Same activation race",
        "reason": UNTRUSTED, "first_seen": "2026-10-20", "last_seen": "2026-10-27T01:00:00Z",
        "occurrences": 2,
        "scopes": ["release:5.1", "release:4.21", "release:4.20", "release:4.18",
                   "release:5.0", "release:4.22", "release:4.20", "pr:7", UNTRUSTED],
        "already_present_before_window": True,
        "jobs": [{"name": "historical boot", "date": "2026-10-20", "url": "https://example.invalid/run/1"}],
        "reports": [{"date": "2026-10-21", "url": "javascript:alert(1)"},
                    {"date": "2026-10-21", "url": "https://example.invalid/doctor/1"},
                    {"date": "2026-10-22", "url": "https://example.invalid/doctor/2"}],
    }
    uncertain = {
        "score": 65, "verdict": "related_symptom", "title": "Similar symptom, different mechanism",
        "reason": "Same timeout, but the evidence identifies an unrelated registry failure.",
        "contradictions": [UNTRUSTED], "occurrences": 20,
        "jobs": [{"name": UNTRUSTED, "date": "2026-10-25", "url": "javascript:alert(1)"}],
        "reports": [{"url": "data:text/html,unsafe", "date": "2026-10-26"}],
    }
    return {
        "schema_version": 1, "days": 14, "as_of": "2026-10-28T12:00:00Z", "component": component,
        "collected_at": "2026-10-28T12:01:00Z", "cost_usd": 0,
        "coverage": {"complete": False, "reports_found": 4, "reports_loaded": 3,
                     "gaps": ["One Doctor report was unavailable."]},
        "issues": {REPORT.issue_key(current): {
            "status": "incomplete", "gaps": ["Candidate budget exhausted; assessment is incomplete."],
            "matches": [accepted, uncertain], "first_seen": "2026-10-20", "last_seen": "2026-10-27T01:00:00Z",
            "occurrences": 2, "pattern": "intermittent evidence",
            "may_predate_window": True,
            "timelines": [{
                "job_name": "periodic-boot", "scope": "same release, architecture and boot scenario",
                "passes": 2, "matched_failures": 2, "unclassified_failures": 1,
                "last_good": "2026-10-26T01:00:00Z", "first_failure": "2026-10-20T01:00:00Z",
                "onset_interval": {"after": "2026-10-19T01:00:00Z", "by": "2026-10-20T01:00:00Z",
                                   "scope": "whole-job pass only"},
                "observations": [
                    {"date": "2026-10-19T01:00:00Z", "status": "success", "url": "https://example.invalid/run/good"},
                    {"date": "2026-10-20T01:00:00Z", "status": "matched_failure", "url": "https://example.invalid/run/1"},
                    {"date": "2026-10-21T01:00:00Z", "status": "unclassified_failure", "url": "javascript:alert(2)"},
                    {"date": "2026-10-26T01:00:00Z", "status": "success", "url": "https://example.invalid/run/good-later"},
                    {"date": "2026-10-27T01:00:00Z", "status": "matched_failure", "url": "https://example.invalid/run/2"},
                ],
            }],
        }},
    }


class ReportHistoryTests(unittest.TestCase):
    def test_exact_key_attachment_across_periodics_and_prs(self):
        current = issue()
        same = copy.deepcopy(current)
        same.update(number=9, affected_jobs=[], bug_match={"duplicates": []})
        distinct = copy.deepcopy(current)
        distinct["root_cause"] = "An unrelated cause with the identical title"
        releases = {"4.20": {"issues": [current, distinct]}, "4.21": {"issues": [same]}, "4.22": None}
        prs = {"prs": [{"number": 7, "issues": [copy.deepcopy(same)]}]}
        history = history_for(current)
        data = payload(REPORT.generate_html("MicroShift", releases, [], prs, None, STAMP, history_data=history))
        expected = history["issues"][REPORT.issue_key(current)]
        self.assertEqual(data["history"], {key: value for key, value in history.items() if key != "issues"})
        self.assertNotIn("issues", data["history"])
        self.assertEqual(data["releases_data"]["4.20"]["issues"][0]["history"], expected)
        self.assertEqual(data["releases_data"]["4.21"]["issues"][0]["history"], expected)
        self.assertEqual(data["pr_data"]["prs"][0]["issues"][0]["history"], expected)
        missing = data["releases_data"]["4.20"]["issues"][1]["history"]
        self.assertEqual(missing["status"], "unavailable")
        self.assertNotIn("occurrences", missing, "an absent assessment is not zero occurrences")
        self.assertTrue(missing["gaps"])

    def test_sidecar_matches_embedded_json_even_for_script_and_comment_text(self):
        current = issue()
        current["root_cause"] = UNTRUSTED
        history = history_for(current)
        source = {"job_name": "doctor-job", "build_id": "42", "url": "https://example.invalid/doctor/42"}
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "report-microshift-ci-doctor.json"
            html = REPORT.generate_html("MicroShift", {"4.20": {"issues": [current]}}, [], None, None, STAMP,
                                        history_data=history, source_metadata=source, sidecar_path=sidecar)
            data = payload(html)
            self.assertEqual(json.loads(sidecar.read_text()), data)
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["component"], "microshift")
        self.assertEqual(data["generated_at"], "2026-10-28T12:00:00Z")
        self.assertEqual(data["source"], source)
        self.assertEqual(data["releases_data"]["4.20"]["issues"][0]["root_cause"], UNTRUSTED)
        self.assertEqual(data["releases_data"]["4.20"]["issues"][0]["history"]["matches"][0]["reason"], UNTRUSTED)
        literal = html.split("window.REPORT_DATA = ", 1)[1].split(";</script>", 1)[0]
        self.assertNotIn("</script>", literal)
        self.assertNotIn("<!--", literal)

    def test_existing_generate_html_call_and_utc_generation(self):
        current = issue()
        data = payload(REPORT.generate_html("Probe", {"4.20": {"issues": [current]}}, [], None, None, STAMP))
        self.assertNotIn("history", data)
        self.assertNotIn("history", data["releases_data"]["4.20"]["issues"][0])
        local_stamp = datetime(2026, 10, 28, 14, tzinfo=timezone(timedelta(hours=2)))
        data = payload(REPORT.generate_html("LVMS", {}, [], None, None, local_stamp))
        self.assertEqual(data["component"], "lvm-operator")
        self.assertEqual(data["generated_at"], "2026-10-28T12:00:00Z")
        data = payload(REPORT.generate_html("Probe", {}, [], None, None, STAMP.replace(tzinfo=None)))
        self.assertEqual(data["generated_at"], "2026-10-28T12:00:00Z")

    def test_saved_history_loading_and_explicit_invalid_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(REPORT.load_history(directory, "microshift"))
            path = Path(directory) / "history" / "results.json"
            path.parent.mkdir()
            valid = history_for(issue())
            path.write_text(json.dumps(valid))
            self.assertEqual(REPORT.load_history(directory, "microshift"), valid)
            invalid = ["not json", "null", "[]", json.dumps({**valid, "schema_version": 2}),
                       json.dumps({**valid, "component": "lvm-operator"}), json.dumps({**valid, "issues": []})]
            for contents in invalid:
                with self.subTest(contents=contents):
                    path.write_text(contents)
                    with contextlib.redirect_stderr(io.StringIO()):
                        data = REPORT.load_history(directory, "microshift")
                    self.assertEqual(data["status"], "unavailable")
                    self.assertIs(data["coverage"]["complete"], False)
                    self.assertTrue(data["coverage"]["gaps"])

    def test_offline_entrypoint_writes_both_components_with_source_and_history(self):
        for component in ("microshift", "lvm-operator"):
            with self.subTest(component=component), tempfile.TemporaryDirectory() as directory:
                workdir = Path(directory)
                (workdir / "jobs").mkdir()
                (workdir / "history").mkdir()
                current = issue()
                (workdir / "jobs" / "release-4.20-summary.json").write_text(json.dumps({
                    "total_failed": 1, "issues": [current]}))
                (workdir / "jobs" / "prs-summary.json").write_text(json.dumps({
                    "has_content": True, "total_failed": 1, "prs": [{"number": 7, "issues": [current]}]}))
                (workdir / "history" / "results.json").write_text(json.dumps(history_for(current, component)))
                env = {**os.environ, "JOB_NAME": "periodic-doctor-" + component, "BUILD_ID": "42"}
                result = subprocess.run([sys.executable, str(SCRIPTS / "create-report.py"), "--component", component,
                                         "--workdir", str(workdir), "4.20"], env=env,
                                        capture_output=True, text=True, check=False, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                html = (workdir / f"report-{component}-ci-doctor.html").read_text()
                data = payload(html)
                self.assertEqual(data, json.loads((workdir / f"report-{component}-ci-doctor.json").read_text()))
                self.assertEqual(data["component"], component)
                self.assertEqual(data["source"]["build_id"], "42")
                self.assertEqual(data["source"]["job_name"], env["JOB_NAME"])
                self.assertEqual(data["source"]["url"],
                                 "https://prow.ci.openshift.org/view/gs/test-platform-results/logs/" + env["JOB_NAME"] + "/42")
                self.assertEqual(data["pr_data"]["prs"][0]["issues"][0]["history"]["occurrences"], 2)
                self.assertEqual(data["releases_data"]["4.20"]["issues"][0]["history"]["occurrences"], 2)
                self.assertEqual(datetime.fromisoformat(data["generated_at"].replace("Z", "+00:00")).utcoffset(), timedelta())
                # Refreshing outside the source CI run must retain the source
                # identity and its actual collection/start timestamps.
                saved_source = {
                    "job_name": "original-doctor-job", "build_id": "7",
                    "url": "https://example.invalid/doctor/7",
                    "started_at": "2026-10-28T10:00:00Z",
                    "collected_at": "2026-10-28T11:00:00Z",
                }
                (workdir / "report-source.json").write_text(json.dumps(saved_source))
                refreshed = subprocess.run(
                    [sys.executable, str(SCRIPTS / "create-report.py"), "--component", component,
                     "--workdir", str(workdir), "4.20"], env=env,
                    capture_output=True, text=True, check=False, timeout=30)
                self.assertEqual(refreshed.returncode, 0, refreshed.stdout + refreshed.stderr)
                refreshed_data = payload((workdir / f"report-{component}-ci-doctor.html").read_text())
                self.assertEqual(refreshed_data["source"], saved_source)
                self.assertEqual(refreshed_data, json.loads((workdir / f"report-{component}-ci-doctor.json").read_text()))

    def test_actual_history_renderer_and_safe_links(self):
        current = issue()
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "history.html"
            report_path.write_text(REPORT.generate_html("MicroShift", {"4.20": {"issues": [current]}}, [],
                                                        {"prs": [{"number": 7, "issues": [copy.deepcopy(current)]}]},
                                                        None, STAMP, history_data=history_for(current)))
            result = subprocess.run(["node", str(Path(__file__).with_suffix(".js")), str(report_path)],
                                    capture_output=True, text=True, check=False, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS history renderer", result.stdout)
        print(result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
