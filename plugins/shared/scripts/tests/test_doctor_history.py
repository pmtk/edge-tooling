"""History regressions. All HTTP/Claude boundaries are deterministic and isolated.

Run: python3 plugins/shared/scripts/tests/test_doctor_history.py
No test may invoke the real Claude executable or a paid provider.
"""

import copy
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib import error, parse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import doctor_history as history

AS_OF = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
START = AS_OF - timedelta(days=14)
DOCTOR = "periodic-ci-openshift-eng-edge-tooling-main-microshift-ci-doctor"
JOB = "periodic-ci-microshift-release-4.20-amd64-scenario"


def url(build, job=JOB):
    return f"https://prow.ci.openshift.org/view/gs/{history.BUCKET}/logs/{job}/{build}"


def issue(build=100, date="2026-11-15T10:00:00Z", cause="The LV thin pool exhausted metadata", **extra):
    value = {"title": "Provisioning timed out", "root_cause": cause, "confidence": "high",
             "causal_chain": [{"cause": cause, "quote": "metadata full", "evidence": "log/path"}],
             "scenarios": ["thin", "restart"],
             "affected_jobs": [{"name": JOB, "url": url(build), "date": date}]}
    value.update(extra)
    return value


def card(value, release="4.20"):
    cards = {}
    history.index_report({"releases_data": {release: {"issues": [value]}}},
                         {"url": "https://example.org/report.html", "date": "2026-11-15T11:00:00Z"}, cards, {}, [])
    return next(iter(cards.values()))


def release_card(release, build=100, cause="The LV thin pool exhausted metadata"):
    value = issue(build, cause=cause)
    job = JOB.replace("4.20", release)
    value["affected_jobs"][0].update(name=job, url=url(build, job))
    return card(value, release)


def decision(value, verdict="same_cause", score=93):
    return {"candidate_id": value["id"], "score": score, "verdict": verdict,
            "reason": "Both causal mechanisms exhaust metadata before allocation", "contradictions": []}


def observation(build, date, status, job=JOB):
    ref = history.execution_ref(url(build, job))
    return {"id": ref["id"], "name": job, "date": date, "status": status, "url": ref["url"]}


class IdentityTests(unittest.TestCase):
    def test_key_ignores_paths_jobs_history_and_scenario_order(self):
        first = issue()
        second = copy.deepcopy(first)
        second.update({"history": {"arbitrary": "nested"}, "affected_jobs": [], "bug_match": {"key": "X"}})
        second["causal_chain"][0]["evidence"] = "another/path"
        second["scenarios"].reverse()
        self.assertEqual(history.issue_key(first), history.issue_key(second))
        second["causal_chain"][0]["quote"] = "different evidence"
        self.assertNotEqual(history.issue_key(first), history.issue_key(second))

    def test_execution_aliases_deduplicate_but_distinct_jobs_do_not(self):
        urls = [url(123), f"gs://origin-ci-test/logs/{JOB}/123/artifacts/file",
                f"gs://test-platform-results/logs/{JOB}/123",
                f"https://storage.googleapis.com/{history.BUCKET}/logs/{JOB}/123/finished.json?x=1",
                f"https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/{history.BUCKET}/logs/{JOB}/123/",
                f"https://gcs.ci.openshift.org/gcs/test-platform-results/logs/{JOB}/123/artifacts/file",
                f"https://prow.ci.openshift.org/view/gs/test-platform-results/logs/{JOB}/123",
                f"https://{history.BUCKET}.storage.googleapis.com/logs/{JOB}/123"]
        identities = {history.execution_ref(value)["id"] for value in urls}
        self.assertEqual(len(identities), 1)
        self.assertNotEqual(history.execution_ref(url(123, "other-job"))["id"], identities.pop())
        self.assertIsNone(history.execution_ref("https://attacker.example/logs/job/123"))
        self.assertIsNone(history.execution_ref("gs://private/logs/job/123"))
        self.assertIsNone(history.execution_ref("gs://test-platform-results-public/logs/job/not-a-build"))

    def test_pr_context_is_part_of_identity(self):
        a = history.execution_ref(f"gs://{history.BUCKET}/pr-logs/pull/org_repo/12/job/123")
        b = history.execution_ref(f"gs://{history.BUCKET}/pr-logs/pull/org_repo/13/job/123")
        self.assertEqual(a["pr"], "12")
        self.assertNotEqual(a["id"], b["id"])

    def test_timestamp_precision_and_future_exclusion(self):
        self.assertEqual(history.observation_date("2026-11-05"), "2026-11-05")
        self.assertTrue(history.within_window("2026-11-05", START, AS_OF))
        self.assertFalse(history.within_window("2026-11-16", START, AS_OF))
        self.assertFalse(history.within_window("2026-11-02", START, AS_OF))
        self.assertFalse(history.within_window("2026-11-17T00:00:00Z", START, AS_OF))
        self.assertFalse(history.definitely_before("2026-11-05", "2026-11-05T12:00:00Z"))
        self.assertEqual(history.parse_time(AS_OF.timestamp() * 1000), AS_OF)
        self.assertIsNone(history.parse_time("2026-11-16T12:00:00"))


class StorageTests(unittest.TestCase):
    def test_http_pagination_without_newest_first_assumption(self):
        requested = []
        def response(address, timeout):
            requested.append(address)
            query = parse.parse_qs(parse.urlsplit(address).query)
            data = {"prefixes": ["logs/job/2/", "logs/job/1/"], "nextPageToken": "next"}
            if query.get("pageToken") == ["next"]:
                data = {"prefixes": ["logs/job/3/"]}
            return io.BytesIO(json.dumps(data).encode())
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(history.request, "urlopen", side_effect=response):
            storage = history.PublicStorage(tmp)
            self.assertEqual(storage.prefixes("logs/job/"), ["logs/job/1/", "logs/job/2/", "logs/job/3/"])
            self.assertEqual(storage.prefixes("logs/job/"), ["logs/job/1/", "logs/job/2/", "logs/job/3/"])
        self.assertEqual(len(requested), 2)

    def test_repeating_page_token_is_an_explicit_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = history.PublicStorage(tmp)
            with mock.patch.object(storage, "json", return_value={"prefixes": [], "nextPageToken": "same"}):
                with self.assertRaisesRegex(history.HistoryError, "pagination"):
                    storage.prefixes("logs/job/")

    def test_legacy_html_escapes_decode_as_data(self):
        original = {"releases_data": {}, "value": "</script><!-- untrusted \\ text", "unicode": "\u2028"}
        embedded = json.dumps(original).replace("</", "<\\/").replace("<!--", "<\\!--")
        text = "<script>window.REPORT_DATA = " + embedded + ";</script><script>throw 'never execute'</script>"
        self.assertEqual(history.decode_report_html(text), original)
        with self.assertRaises(history.HistoryError):
            history.decode_report_html("window.REPORT_DATA = fetch('https://attacker');")

    def test_report_window_current_future_missing_and_failed_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = history.PublicStorage(tmp)
            prefix = f"logs/{DOCTOR}/"
            starts = {"1": AS_OF - timedelta(days=16), "2": AS_OF - timedelta(days=10),
                      "3": AS_OF - timedelta(days=9), "4": AS_OF - timedelta(days=8),
                      "5": AS_OF, "6": AS_OF - timedelta(days=1), "7": AS_OF - timedelta(days=2)}
            called_reports = []
            def metadata(path, name):
                build = path.rstrip("/").rsplit("/", 1)[-1]
                if name == "started":
                    return {"timestamp": starts[build].timestamp()}
                return {"timestamp": (AS_OF + timedelta(hours=1) if build == "6" else starts[build] + timedelta(hours=1)).timestamp(),
                        "result": "FAILURE" if build == "4" else "SUCCESS"}
            def download(address):
                if address.startswith(history.LIST_URL):
                    return {"items": []}
                build = address.split(f"/{DOCTOR}/")[1].split("/")[0]
                called_reports.append((build, address))
                if build == "3":
                    raise error.HTTPError(address, 404, "missing", {}, None)
                return {"schema_version": 1, "component": "microshift", "history": {"recursive": "do not save"},
                        "releases_data": {"4.20": {"issues": [issue(12)]}}}
            with mock.patch.object(storage, "prefixes", return_value=[prefix + k + "/" for k in reversed(starts)]), \
                    mock.patch.object(storage, "metadata", side_effect=metadata), \
                    mock.patch.object(storage, "json", side_effect=download), \
                    mock.patch.object(storage, "text", side_effect=OSError("legacy missing")):
                cards, snapshots, coverage = history.acquire_reports(storage, "microshift", AS_OF, 14,
                                                                       "rehearse-555-" + DOCTOR, "7")
            self.assertEqual(coverage["reports_found"], 3)
            self.assertEqual(coverage["reports_loaded"], 2)
            self.assertFalse(coverage["complete"])
            self.assertEqual(len(cards), 1)
            self.assertEqual(len(cards[0]["jobs"]), 1)
            self.assertEqual(len(cards[0]["reports"]), 2)
            self.assertEqual({b for b, _ in called_reports}, {"2", "3", "4"})
            self.assertTrue(any("partial" in gap for gap in coverage["gaps"]))
            for saved in Path(tmp).glob("source-*.json"):
                self.assertNotIn("recursive", saved.read_text())

    def test_sidecar_falls_back_to_legacy_without_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = history.PublicStorage(tmp)
            stamp = (AS_OF - timedelta(days=1)).timestamp()
            with mock.patch.object(storage, "prefixes", return_value=[f"logs/{DOCTOR}/11/"]), \
                    mock.patch.object(storage, "metadata", return_value={"timestamp": stamp, "result": "SUCCESS"}), \
                    mock.patch.object(storage, "json", side_effect=OSError("no sidecar")), \
                    mock.patch.object(storage, "text", return_value="window.REPORT_DATA = " + json.dumps({"releases_data": {}}) + ";</script>"):
                cards, snapshots, coverage = history.acquire_reports(storage, "microshift", AS_OF, 14)
            self.assertEqual(coverage["reports_loaded"], 1)
            self.assertTrue(coverage["complete"])

    def test_persisted_downloaded_anchor_is_honored_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = history.PublicStorage(tmp)
            history.write_json(Path(tmp) / "context.json", {"as_of": history.iso(AS_OF), "source_url": url(4)})
            with mock.patch.object(storage, "metadata", side_effect=AssertionError("must not fetch CI anchor")), \
                    mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(history.resolve_anchor(storage), AS_OF)
            self.assertEqual(history.read_json(Path(tmp) / "context.json")["source_url"], url(4))

    def test_doctor_ci_anchor_uses_started_not_wall_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = history.PublicStorage(tmp)
            with mock.patch.object(storage, "metadata", return_value={"timestamp": AS_OF.timestamp()}) as metadata, \
                    mock.patch.dict(os.environ, {"JOB_NAME": DOCTOR, "BUILD_ID": "999"}, clear=True):
                self.assertEqual(history.resolve_anchor(storage), AS_OF)
                metadata.assert_called_once_with(f"logs/{DOCTOR}/999", "started")


class AnchorRegressionTests(unittest.TestCase):
    def test_new_ci_build_replaces_saved_anchor_from_previous_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = history.PublicStorage(tmp)
            old = AS_OF - timedelta(days=1)
            source = f"gs://test-platform-results/logs/{DOCTOR}/998"
            history.write_json(Path(tmp) / "context.json", {
                "as_of": history.iso(old), "source_url": source, "component": "microshift"})
            with mock.patch.dict(os.environ, {"JOB_NAME": DOCTOR, "BUILD_ID": "999"}, clear=True), \
                    mock.patch.object(storage, "metadata", return_value={"timestamp": AS_OF.timestamp()}) as metadata:
                self.assertEqual(history.resolve_anchor(storage, component="microshift"), AS_OF)
                metadata.assert_called_once_with(f"logs/{DOCTOR}/999", "started")
            context = history.read_json(Path(tmp) / "context.json")
            self.assertEqual(context["component"], "microshift")
            self.assertEqual(history.execution_ref(context["source_url"])["build_id"], "999")

    def test_same_ci_execution_reuses_its_saved_anchor_even_through_bucket_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = history.PublicStorage(tmp)
            history.write_json(Path(tmp) / "context.json", {
                "as_of": history.iso(AS_OF), "source_url": f"gs://test-platform-results/logs/{DOCTOR}/999"})
            with mock.patch.dict(os.environ, {"JOB_NAME": DOCTOR, "BUILD_ID": "999"}, clear=True), \
                    mock.patch.object(storage, "metadata", side_effect=AssertionError("same execution anchor is persisted")):
                self.assertEqual(history.resolve_anchor(storage, component="microshift"), AS_OF)

    def test_explicit_anchor_overrides_new_ci_execution_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = history.PublicStorage(tmp)
            history.write_json(Path(tmp) / "context.json", {
                "as_of": history.iso(AS_OF - timedelta(days=1)),
                "source_url": f"gs://{history.BUCKET}/logs/{DOCTOR}/998"})
            with mock.patch.dict(os.environ, {"JOB_NAME": DOCTOR, "BUILD_ID": "999"}, clear=True), \
                    mock.patch.object(storage, "metadata", side_effect=AssertionError("explicit anchor must win")):
                self.assertEqual(history.resolve_anchor(storage, history.iso(AS_OF), "microshift"), AS_OF)

    def test_saved_component_and_legacy_source_component_must_match(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=True):
            storage = history.PublicStorage(tmp)
            context_path = Path(tmp) / "context.json"
            history.write_json(context_path, {"as_of": history.iso(AS_OF), "component": "lvm-operator"})
            with self.assertRaisesRegex(history.HistoryError, "another component"):
                history.resolve_anchor(storage, component="microshift")
            lvms = "periodic-ci-openshift-eng-edge-tooling-main-lvms-ci-doctor"
            history.write_json(context_path, {"as_of": history.iso(AS_OF),
                "source_url": f"https://gcs.ci.openshift.org/gcs/test-platform-results/logs/{lvms}/123"})
            with self.assertRaisesRegex(history.HistoryError, "another component"):
                history.resolve_anchor(storage, component="microshift")


class ComparatorTests(unittest.TestCase):
    def test_strict_validation_rejects_invalid_outputs(self):
        value = card(issue())
        good = decision(value)
        invalid = [{"comparisons": []}, {"comparisons": [dict(good, score=True)]},
                   {"comparisons": [dict(good, score=101)]}, {"comparisons": [dict(good, verdict=[])]},
                   {"comparisons": [dict(good, candidate_id="injected")]},
                   {"comparisons": [dict(good, reason="")]},
                   {"comparisons": [dict(good, contradictions=["mechanisms contradict"])]},
                   {"comparisons": [dict(good, extra="not allowed")]},
                   {"comparisons": [good, good]}]
        for output in invalid:
            with self.subTest(output=output), self.assertRaises(history.HistoryError):
                history.validate_output(output, [value["id"]])
        self.assertEqual(history.validate_output({"comparisons": [good]}, [value["id"]])["comparisons"], [good])

    def test_negative_decisions_are_cached_without_fallback_similarity(self):
        current = card(issue())
        historical = card(issue(2, cause="DNS lookup could not reach the API server"))
        with tempfile.TemporaryDirectory() as tmp:
            comparator = history.Comparator(tmp, "model", 2)
            result = {"comparisons": [decision(historical, "different_cause", 5)]}
            with mock.patch.object(comparator, "invoke", return_value=result) as invoke:
                first, gaps = comparator.compare(current, [historical])
                second, gaps2 = comparator.compare(current, [historical])
                self.assertEqual(first, second)
                self.assertEqual(gaps + gaps2, [])
                self.assertEqual(invoke.call_count, 1)
            newer = history.Comparator(tmp, "different-model", 2, fetch_only=True)
            output, gaps = newer.compare(current, [historical])
            self.assertFalse(output)
            self.assertTrue(any("fetch-only" in gap for gap in gaps))

    def test_local_and_fetch_only_guards_never_launch_cli(self):
        current, historical = card(issue()), card(issue(2, cause="disk attachment race"))
        payload = {"current": current["evidence"], "candidates": [dict(candidate_id=historical["id"], **historical["evidence"])]}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(history.subprocess, "Popen", side_effect=AssertionError("real CLI forbidden")), \
                mock.patch.dict(os.environ, {}, clear=True):
            comparator = history.Comparator(tmp, "model", 2)
            with self.assertRaisesRegex(history.HistoryError, "CI-only"):
                comparator.invoke(payload, "compare")
            comparator.fetch_only = True
            with mock.patch.dict(os.environ, {"CI": "true"}), self.assertRaisesRegex(history.HistoryError, "fetch-only"):
                comparator.invoke(payload, "compare")

    def test_exact_reused_execution_needs_no_model(self):
        current = card(issue())
        with tempfile.TemporaryDirectory() as tmp:
            comparator = history.Comparator(tmp, "model", 2, fetch_only=True)
            with mock.patch.object(comparator, "invoke", side_effect=AssertionError("reused execution")):
                decisions, gaps = comparator.compare(current, [current])
            self.assertEqual(decisions[current["id"]]["verdict"], "same_cause")
            self.assertEqual(gaps, [])

    def test_mocked_cli_is_toolless_bounded_and_tracks_actual_cost(self):
        current, historical = card(issue()), card(issue(2, cause="disk attachment race"))
        payload = {"current": current["evidence"], "candidates": [dict(candidate_id=historical["id"], **historical["evidence"])]}
        envelope = {"subtype": "success", "total_cost_usd": 0.125,
                    "usage": {"input_tokens": 900, "output_tokens": 100},
                    "structured_output": {"comparisons": [decision(historical)]}}
        process = mock.Mock(returncode=0)
        process.communicate.return_value = (json.dumps(envelope), "")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"CI": "true"}), \
                mock.patch.object(history.subprocess, "Popen", return_value=process) as popen:
            comparator = history.Comparator(tmp, "model", 0.5)
            comparator.invoke(payload, "compare")
            comparator.invoke(payload, "compare")
            self.assertEqual(comparator.cost, 0.25)
            command = popen.call_args.args[0]
            self.assertEqual(command[command.index("--tools") + 1], "")
            self.assertEqual(command[command.index("--max-budget-usd") + 1], "0.37500000")
            self.assertIn("--strict-mcp-config", command)
            self.assertIn("--no-session-persistence", command)
            self.assertEqual(popen.call_args.kwargs["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], str(history.MAX_OUTPUT_TOKENS))
            self.assertEqual(popen.call_args.kwargs["env"]["CLAUDE_CODE_SIMPLE"], "1")
            self.assertFalse(Path(popen.call_args.kwargs["cwd"]).exists())
            saved = history.Comparator(tmp, "model", 0.5)
            self.assertEqual(saved.cost, 0.25)
            self.assertEqual(len(history.read_json(Path(tmp) / "cost.json")["calls"]), 2)

    def test_failed_result_still_consumes_budget_and_exhaustion_is_visible(self):
        current, historical = card(issue()), card(issue(2, cause="disk attachment race"))
        payload = {"current": current["evidence"], "candidates": [dict(candidate_id=historical["id"], **historical["evidence"])]}
        process = mock.Mock(returncode=1)
        process.communicate.return_value = (json.dumps({"is_error": True, "total_cost_usd": 0.1}), "")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"CI": "true"}), \
                mock.patch.object(history.subprocess, "Popen", return_value=process) as popen:
            comparator = history.Comparator(tmp, "model", 0.1)
            with self.assertRaisesRegex(history.HistoryError, "did not complete"):
                comparator.invoke(payload, "compare")
            with self.assertRaisesRegex(history.HistoryError, "budget exhausted"):
                comparator.invoke(payload, "compare")
            self.assertEqual(popen.call_count, 1)
            self.assertEqual(comparator.cost, 0.1)

    def test_unknown_cost_disables_further_calls_even_after_resume(self):
        current, historical = card(issue()), card(issue(2, cause="disk attachment race"))
        payload = {"current": current["evidence"], "candidates": [dict(candidate_id=historical["id"], **historical["evidence"])]}
        process = mock.Mock(returncode=0)
        process.communicate.return_value = ("not json", "")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"CI": "true"}), \
                mock.patch.object(history.subprocess, "Popen", return_value=process) as popen:
            comparator = history.Comparator(tmp, "model", 2)
            with self.assertRaisesRegex(history.HistoryError, "cost unknown"):
                comparator.invoke(payload, "compare")
            resumed = history.Comparator(tmp, "model", 2)
            with self.assertRaisesRegex(history.HistoryError, "cost unknown"):
                resumed.invoke(payload, "compare")
            self.assertEqual(popen.call_count, 1)

    def test_input_limit_prevents_cli_invocation(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"CI": "true"}), \
                mock.patch.object(history.subprocess, "Popen", side_effect=AssertionError("oversized request")):
            comparator = history.Comparator(tmp, "model", 2)
            with self.assertRaisesRegex(history.HistoryError, "input-token"):
                comparator.invoke({"current": "x" * history.MAX_INPUT_TOKENS, "candidates": []}, "compare")

    def test_semantic_shortlist_retrieves_paraphrase_without_lexical_overlap(self):
        current = card(issue())
        unrelated = card(issue(2, cause="Image registry authentication rejected a token"))
        paraphrase = card(issue(3, cause="Device-mapper bookkeeping capacity reached its limit"))
        with tempfile.TemporaryDirectory() as tmp:
            comparator = history.Comparator(tmp, "model", 2)
            with mock.patch.object(comparator, "invoke", return_value={"candidate_ids": [paraphrase["id"]]}) as invoke:
                selected, gaps = comparator.shortlist(current, [unrelated, paraphrase])
            self.assertEqual(selected, {paraphrase["id"]})
            self.assertEqual(gaps, [])
            self.assertEqual(invoke.call_args.args[1], "shortlist")


class TimelineTests(unittest.TestCase):
    def test_repeated_doctor_sightings_and_aliases_count_one_execution(self):
        current = card(issue(3, "2026-11-15T10:00:00Z"))
        old_issue = issue(1, "2026-11-05T10:00:00Z")
        old_issue["affected_jobs"].append({"name": JOB, "date": "2026-11-05T10:00:00Z", "url": f"gs://origin-ci-test/logs/{JOB}/1"})
        historical = card(old_issue)
        duplicate = copy.deepcopy(historical)
        result = history.build_history(current, [historical, duplicate],
                    {historical["id"]: decision(historical)}, {}, START, AS_OF, [])
        self.assertEqual(result["occurrences"], 2)
        self.assertTrue(all(m["occurrences"] == 1 for m in result["matches"]))
        self.assertFalse(result["multiple_rca_entries"])

    def test_uncertain_and_different_causes_do_not_inflate_primary_count(self):
        current = card(issue(3, "2026-11-15T10:00:00Z"))
        uncertain = card(issue(1, "2026-11-05T10:00:00Z", cause="Possibly metadata pressure"))
        different = card(issue(2, "2026-11-10T10:00:00Z", cause="DNS outage"))
        decisions = {uncertain["id"]: decision(uncertain, "insufficient_evidence", 60),
                     different["id"]: decision(different, "different_cause", 5)}
        result = history.build_history(current, [uncertain, different], decisions, {}, START, AS_OF, [])
        self.assertEqual(result["occurrences"], 1)
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["matches"][0]["verdict"], "insufficient_evidence")

    def test_pass_between_failures_is_intermittent_but_unclassified_failure_is_not(self):
        current = card(issue(4, "2026-11-15T10:00:00Z"))
        historical = card(issue(2, "2026-11-05T10:00:00Z"))
        observations = {r["id"]: r for r in [observation(1, "2026-11-04T10:00:00Z", "success"),
            observation(3, "2026-11-10T10:00:00Z", "success"),
            observation(5, "2026-11-11T10:00:00Z", "unclassified_failure")]}
        result = history.build_history(current, [historical], {historical["id"]: decision(historical)}, observations, START, AS_OF, [])
        self.assertEqual(result["pattern"], "intermittent evidence")
        timeline = result["timelines"][0]
        self.assertEqual((timeline["passes"], timeline["matched_failures"], timeline["unclassified_failures"]), (2, 2, 1))
        self.assertEqual(timeline["last_good"], "2026-11-10T10:00:00Z")
        self.assertEqual(timeline["onset_interval"]["after"], "2026-11-04T10:00:00Z")
        self.assertEqual(timeline["onset_interval"]["by"], "2026-11-05T10:00:00Z")
        observations[history.execution_ref(url(3))["id"]]["status"] = "unclassified_failure"
        changed = history.build_history(current, [historical], {historical["id"]: decision(historical)}, observations, START, AS_OF, [])
        self.assertNotEqual(changed["pattern"], "intermittent evidence")

    def test_unobserved_passes_pending_and_gaps_never_imply_persistence(self):
        current = card(issue(4, "2026-11-15T10:00:00Z"))
        historical = card(issue(2, "2026-11-05T10:00:00Z"))
        result = history.build_history(current, [historical], {historical["id"]: decision(historical)}, {}, START, AS_OF, ["missing Doctor report"])
        self.assertEqual(result["pattern"], "insufficient evidence")
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["occurrences"], 2)

    def test_cross_job_pass_does_not_imply_intermittency(self):
        current = card(issue(4, "2026-11-15T10:00:00Z"))
        historical = card(issue(2, "2026-11-05T10:00:00Z"))
        row = observation(3, "2026-11-10T10:00:00Z", "success", "periodic-other-architecture")
        result = history.build_history(current, [historical], {historical["id"]: decision(historical)}, {row["id"]: row}, START, AS_OF, [])
        self.assertEqual(result["pattern"], "persistent in observed runs")
        self.assertEqual(result["timelines"][0]["passes"], 0)

    def test_stale_and_future_observations_do_not_count(self):
        current = card(issue(4, "2026-11-15T10:00:00Z"))
        old = issue(1, "2026-11-01T10:00:00Z")
        old["affected_jobs"].append({"name": JOB, "url": url(5), "date": "2026-11-17T10:00:00Z"})
        historical = card(old)
        result = history.build_history(current, [historical], {historical["id"]: decision(historical)}, {}, START, AS_OF, [])
        self.assertEqual(result["occurrences"], 1)
        self.assertEqual(result["matches"][0]["occurrences"], 0)
        self.assertTrue(result["already_present_before_window"])

    def test_execution_metadata_overrides_legacy_report_dates(self):
        current = card(issue(4, "2026-11-15T10:00:00Z"))
        historical = card(issue(2, "2026-11-05T10:00:00Z"))
        row = observation(2, "2026-11-01T10:00:00Z", "unclassified_failure")
        row["metadata"] = True
        result = history.build_history(current, [historical], {historical["id"]: decision(historical)}, {row["id"]: row}, START, AS_OF, [])
        self.assertEqual(result["occurrences"], 1)
        self.assertTrue(result["already_present_before_window"])

    def test_same_day_date_only_failure_and_pass_cannot_establish_order(self):
        current = card(issue(4, "2026-11-15T10:00:00Z"))
        historical = card(issue(2, "2026-11-05"))
        row = observation(3, "2026-11-05T10:00:00Z", "success")
        result = history.build_history(current, [historical], {historical["id"]: decision(historical)}, {row["id"]: row}, START, AS_OF, [])
        self.assertNotEqual(result["pattern"], "intermittent evidence")
        self.assertEqual(result["first_seen"], "2026-11-05")

    def test_pr_unknown_head_and_different_heads_are_not_comparable(self):
        pr_url = lambda build: f"gs://{history.BUCKET}/pr-logs/pull/org_repo/12/job/{build}"
        first = issue(1, "2026-11-05T10:00:00Z")
        first["affected_jobs"][0]["url"] = pr_url(1)
        latest = issue(3, "2026-11-15T10:00:00Z")
        latest["affected_jobs"][0]["url"] = pr_url(3)
        historical, current = card(first), card(latest)
        ref = history.execution_ref(pr_url(2))
        row = {"id": ref["id"], "name": "job", "date": "2026-11-10T10:00:00Z", "url": ref["url"], "status": "success"}
        result = history.build_history(current, [historical], {historical["id"]: decision(historical)}, {row["id"]: row}, START, AS_OF, [])
        self.assertNotEqual(result["pattern"], "intermittent evidence")
        self.assertFalse(result["timelines"][0]["comparable"])
        self.assertTrue(any("head revision" in gap for gap in result["gaps"]))
        historical["jobs"][history.execution_ref(pr_url(1))["id"]]["head_sha"] = "first-head"
        current["jobs"][history.execution_ref(pr_url(3))["id"]]["head_sha"] = "second-head"
        row["head_sha"] = "second-head"
        for job in historical["jobs"].values():
            job["base_sha"] = "same-base"
        for job in current["jobs"].values():
            job["base_sha"] = "same-base"
        row["base_sha"] = "same-base"
        result = history.build_history(current, [historical], {historical["id"]: decision(historical)}, {row["id"]: row}, START, AS_OF, [])
        self.assertEqual(len(result["timelines"]), 2)
        self.assertNotEqual(result["pattern"], "intermittent evidence")


class WorkflowTests(unittest.TestCase):
    def test_targeted_metadata_classifies_passes_not_uninvestigated_failures(self):
        current = card(issue(4))
        prefix = f"logs/{JOB}/"
        def metadata(path, name):
            build = int(path.rstrip("/").rsplit("/", 1)[-1])
            if build == 3 and name == "finished":
                raise OSError("pending")
            stamp = AS_OF - timedelta(days=5 - build)
            return {"timestamp": stamp.timestamp(), "result": "SUCCESS" if build == 1 else "FAILURE"}
        with tempfile.TemporaryDirectory() as tmp:
            storage = history.PublicStorage(tmp)
            with mock.patch.object(storage, "prefixes", return_value=[prefix + str(i) + "/" for i in (1, 2, 3)]) as listing, \
                    mock.patch.object(storage, "metadata", side_effect=metadata):
                rows, gaps = history.metadata_observations(storage, current["jobs"], START, AS_OF)
                again, again_gaps = history.metadata_observations(storage, current["jobs"], START, AS_OF)
            listing.assert_called_once_with(prefix)
        self.assertEqual({r["status"] for r in rows.values()}, {"success", "unclassified_failure"})
        self.assertEqual(rows, again)
        self.assertEqual(gaps, again_gaps)
        self.assertTrue(any("pending" in gap for gap in gaps))

    def test_current_raw_jobs_are_freshly_aggregated(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Path(tmp) / "jobs"
            jobs.mkdir()
            raw = {"error_signature": "fresh signature", "root_cause": "fresh cause", "job_name": JOB,
                   "job_url": url(123), "finished": "2026-11-15T10:00:00Z", "stack_layer": "test",
                   "step_name": "special-step", "raw_error": "distinctive error fragment"}
            history.write_json(jobs / "release-4.20-job-123.json", [raw])
            history.write_json(jobs / "release-4.20-summary.json", {"issues": [issue(cause="stale")]})
            current, snapshots, gaps = history.load_current(tmp, ["4.20"], AS_OF)
            self.assertEqual(current[0]["evidence"]["root_cause"], "fresh cause")
            self.assertIn("special-step", current[0]["retrieval"])
            self.assertEqual(history.read_json(jobs / "release-4.20-summary.json")["issues"][0]["root_cause"], "fresh cause")

    def test_fetch_only_workflow_saves_explicit_incomplete_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Path(tmp) / "jobs"
            jobs.mkdir()
            history.write_json(jobs / "release-4.20-summary.json", {"issues": [issue()]})
            historical = card(issue(2, cause="different wording"))
            coverage = {"complete": True, "reports_found": 1, "reports_loaded": 1, "gaps": []}
            with mock.patch.object(history, "acquire_reports", return_value=([historical], {}, coverage)), \
                    mock.patch.object(history, "metadata_observations", return_value=({}, [])), \
                    mock.patch.object(history.subprocess, "Popen", side_effect=AssertionError("no paid models")):
                result = history.run("microshift", tmp, ["4.20"], as_of=history.iso(AS_OF), fetch_only=True)
            value = result["issues"][history.issue_key(issue())]
            self.assertEqual(value["status"], "incomplete")
            self.assertEqual(value["occurrences"], 1)
            self.assertTrue(any("fetch-only" in gap for gap in value["gaps"]))
            self.assertFalse(result["coverage"]["complete"])
            self.assertEqual(history.read_json(Path(tmp) / "history" / "results.json"), result)


class CoverageRegressionTests(unittest.TestCase):
    def test_historical_discovery_does_not_inherit_twenty_build_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = history.PublicStorage(tmp)
            stamp = (AS_OF - timedelta(days=1)).timestamp()
            paths = [f"logs/{DOCTOR}/{i}/" for i in range(1, 26)]
            report = {"schema_version": 1, "releases_data": {}}
            with mock.patch.object(storage, "prefixes", return_value=paths), \
                    mock.patch.object(storage, "metadata", return_value={"timestamp": stamp, "result": "SUCCESS"}), \
                    mock.patch.object(storage, "json", return_value=report):
                cards, snapshots, coverage = history.acquire_reports(storage, "microshift", AS_OF, 14)
            self.assertEqual(coverage["reports_found"], 25)
            self.assertEqual(coverage["reports_loaded"], 25)

    def test_fetch_only_keeps_unassessed_causes_incomplete(self):
        current = card(issue())
        historical = [card(issue(i + 1, cause=f"Mechanism number {i}")) for i in range(12)]
        coverage = {"complete": True, "reports_found": 1, "reports_loaded": 1, "gaps": []}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(history, "load_current", return_value=([current], {}, [])), \
                mock.patch.object(history, "acquire_reports", return_value=(historical, {}, coverage)), \
                mock.patch.object(history, "metadata_observations", return_value=({}, [])), \
                mock.patch.object(history.subprocess, "Popen", side_effect=AssertionError("paid call forbidden")):
            result = history.run("microshift", tmp, ["4.20"], as_of=history.iso(AS_OF), fetch_only=True)
        value = result["issues"][current["id"]]
        self.assertEqual(value["status"], "incomplete")
        self.assertTrue(value["gaps"])
        self.assertEqual(value["matches"], [])
        self.assertEqual(value["occurrences"], 1)
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(result["comparison_usage"], [])
        self.assertEqual(result["cost_usd"], 0)

    def test_missing_metadata_does_not_reuse_previous_build_start(self):
        current = card(issue())
        prefix = f"logs/{JOB}/"
        def metadata(path, name):
            if path.endswith("/1/"):
                return {"timestamp": (START - timedelta(days=1)).timestamp(), "result": "SUCCESS"}
            raise OSError("unknown execution timestamp")
        with tempfile.TemporaryDirectory() as tmp:
            storage = history.PublicStorage(tmp)
            with mock.patch.object(storage, "prefixes", return_value=[prefix + "1/", prefix + "2/"]), \
                    mock.patch.object(storage, "metadata", side_effect=metadata):
                rows, gaps = history.metadata_observations(storage, current["jobs"], START, AS_OF)
        self.assertFalse(rows)
        self.assertTrue(any("unknown execution timestamp" in gap for gap in gaps))


class ChronologyRegressionTests(unittest.TestCase):
    def test_fractional_timestamps_use_chronological_not_lexical_order(self):
        current = card(issue(3, "2026-11-15T10:00:00.100000Z"))
        historical = card(issue(1, "2026-11-15T10:00:00Z"))
        result = history.build_history(current, [historical],
                    {historical["id"]: decision(historical)}, {}, START, AS_OF, [])
        self.assertEqual(result["first_seen"], "2026-11-15T10:00:00Z")
        self.assertEqual(result["last_seen"], "2026-11-15T10:00:00.100000Z")
        self.assertEqual(result["timelines"][0]["first_failure"], "2026-11-15T10:00:00Z")


class PublicationLayoutRegressionTests(unittest.TestCase):
    def test_metadata_and_semantic_cache_survive_first_level_only_upload(self):
        current = card(issue())
        historical = card(issue(2, cause="independent disk attachment race"))
        with tempfile.TemporaryDirectory() as tmp:
            storage = history.PublicStorage(tmp)
            with mock.patch.object(storage, "object", return_value={"timestamp": AS_OF.timestamp()}) as fetch:
                first = storage.metadata(f"logs/{JOB}/123", "started")
                self.assertEqual(storage.metadata(f"logs/{JOB}/123", "started"), first)
                fetch.assert_called_once()
            comparator = history.Comparator(tmp, "model", 2)
            with mock.patch.object(comparator, "invoke", return_value={
                    "comparisons": [decision(historical, "different_cause", 1)]}):
                comparator.compare(current, [historical])
            self.assertEqual(len(list(Path(tmp).glob("metadata-*.json"))), 1)
            self.assertEqual(len(list(Path(tmp).glob("cache-*.json"))), 1)
            self.assertTrue(all(path.is_file() for path in Path(tmp).iterdir()))

    def test_prow_variables_alone_do_not_bypass_explicit_ci_guard(self):
        current, historical = card(issue()), card(issue(2, cause="disk attachment race"))
        payload = {"current": current["evidence"],
                   "candidates": [dict(candidate_id=historical["id"], **historical["evidence"])]}
        prow = {"PROW_JOB_ID": "prow-uuid", "JOB_NAME": DOCTOR, "BUILD_ID": "999"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, prow, clear=True), \
                mock.patch.object(history.subprocess, "Popen", side_effect=AssertionError("explicit CI flag required")):
            with self.assertRaisesRegex(history.HistoryError, "CI-only"):
                history.Comparator(tmp, "model", 2).invoke(payload, "compare")


class CurrentEvidenceRegressionTests(unittest.TestCase):
    def test_unknown_and_boundary_current_dates_cannot_claim_complete_zero_occurrences(self):
        for date in (None, "invalid date", "2026-11-02", "2026-11-16"):
            with self.subTest(date=date):
                current = card(issue(date=date))
                result = history.build_history(current, [], {}, {}, START, AS_OF, [])
                self.assertEqual(result["occurrences"], 0)
                self.assertEqual(result["status"], "incomplete")
                self.assertTrue(any("Current execution date" in gap for gap in result["gaps"]))
                self.assertFalse(result["already_present_before_window"])

    def test_authoritative_metadata_can_resolve_unknown_current_date(self):
        current = card(issue(date=None))
        row = observation(100, "2026-11-15T10:00:00Z", "unclassified_failure")
        row["metadata"] = True
        result = history.build_history(current, [], {}, {row["id"]: row}, START, AS_OF, [])
        self.assertEqual(result["occurrences"], 1)
        self.assertEqual(result["status"], "complete")

    def test_multiple_independent_causes_do_not_imply_disagreement(self):
        current = card(issue(3))
        first = card(issue(1, "2026-11-05T10:00:00Z"))
        independent = card(issue(1, "2026-11-05T10:00:00Z", cause="Independent registry authentication failure"))
        decisions = {first["id"]: decision(first),
                     independent["id"]: decision(independent, "different_cause", 3)}
        result = history.build_history(current, [first, independent], decisions, {}, START, AS_OF, [])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["occurrences"], 2)
        self.assertEqual(len(result["multiple_rca_entries"]), 1)
        self.assertFalse(result["gaps"])


class CrossRunCacheRegressionTests(unittest.TestCase):
    def test_optional_cache_index_is_paginated_and_ignores_cost_and_nested_files(self):
        prefix = f"logs/{DOCTOR}/111/artifacts/microshift-ci-doctor/openshift-edge-tooling-microshift-ci-doctor/artifacts/history/cache-"
        first = "a" * 64
        second = "b" * 64
        def listing(address):
            query = parse.parse_qs(parse.urlsplit(address).query)
            self.assertEqual(query["fields"], ["items(name),nextPageToken"])
            self.assertEqual(query["prefix"], [prefix])
            if query.get("pageToken") == ["next"]:
                return {"items": [{"name": prefix + second + ".json"}]}
            return {"items": [{"name": prefix + first + ".json"},
                              {"name": prefix[:-6] + "cost.json"},
                              {"name": prefix + "nested/cache-" + first + ".json"}],
                    "nextPageToken": "next"}
        with tempfile.TemporaryDirectory() as tmp:
            storage = history.PublicStorage(tmp)
            with mock.patch.object(storage, "json", side_effect=listing) as fetch:
                history.discover_comparison_caches(storage, [(AS_OF, prefix)])
            manifest = history.read_json(Path(tmp) / "cache-sources.json")
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(set(manifest["objects"]), {f"cache-{first}.json", f"cache-{second}.json"})
            self.assertTrue(manifest["complete"])

    def test_remote_negative_decision_is_lazily_validated_and_republished(self):
        current = card(issue())
        historical = card(issue(2, cause="Independent storage attachment race"))
        with tempfile.TemporaryDirectory() as tmp:
            initial = history.Comparator(tmp, "model", 2, fetch_only=True)
            key = initial.cache_key(current, historical)
            filename = "cache-" + key + ".json"
            address = f"{history.PUBLIC}{history.BUCKET}/logs/{DOCTOR}/111/artifacts/step/artifacts/history/{filename}"
            history.write_json(Path(tmp) / "cache-sources.json", {
                "schema_version": 1, "objects": {filename: address}})
            comparator = history.Comparator(tmp, "model", 2, fetch_only=True)
            negative = decision(historical, "different_cause", 1)
            with mock.patch.object(comparator.storage, "json", return_value=negative) as fetch, \
                    mock.patch.object(comparator, "invoke", side_effect=AssertionError("remote cache must avoid paid calls")):
                first, gaps = comparator.compare(current, [historical])
                second, second_gaps = comparator.compare(current, [historical])
            fetch.assert_called_once_with(address)
            self.assertEqual(first, second)
            self.assertEqual(gaps + second_gaps, [])
            self.assertEqual(history.read_json(Path(tmp) / filename), negative)
            self.assertEqual(comparator.cost, 0)
            self.assertFalse((Path(tmp) / "cost.json").exists())

    def test_shortlist_cache_is_imported_and_unknown_keys_make_no_http_requests(self):
        current = card(issue())
        historical = card(issue(2, cause="Different words for a causal mechanism"))
        with tempfile.TemporaryDirectory() as tmp:
            comparator = history.Comparator(tmp, "model", 2, fetch_only=True)
            payload = {"current": current["evidence"], "candidates": [{
                "candidate_id": historical["id"], "root_cause": historical["evidence"]["root_cause"][:450],
                "title": historical["evidence"]["title"][:140]}]}
            key = history.digest([history.PROMPT_VERSION, "model", "shortlist", payload])
            filename = "cache-" + key + ".json"
            address = f"{history.PUBLIC}{history.BUCKET}/logs/{DOCTOR}/111/artifacts/history/{filename}"
            comparator.cache_sources = {"objects": {filename: address}}
            with mock.patch.object(comparator.storage, "json", return_value={"candidate_ids": [historical["id"]]}) as fetch, \
                    mock.patch.object(comparator, "invoke", side_effect=AssertionError("cached shortlist")):
                selected, gaps = comparator.shortlist(current, [historical])
                self.assertIsNone(comparator.read_cache("c" * 64, [historical["id"]]))
            fetch.assert_called_once_with(address)
            self.assertEqual(selected, {historical["id"]})
            self.assertEqual(gaps, [])
            self.assertTrue((Path(tmp) / filename).is_file())

    def test_invalid_or_unavailable_remote_cache_is_only_an_optional_miss(self):
        historical = card(issue())
        with tempfile.TemporaryDirectory() as tmp:
            comparator = history.Comparator(tmp, "model", 2, fetch_only=True)
            key = "a" * 64
            filename = f"cache-{key}.json"
            address = f"{history.PUBLIC}{history.BUCKET}/logs/{DOCTOR}/111/artifacts/history/{filename}"
            comparator.cache_sources = {"objects": {filename: address}}
            with mock.patch.object(comparator.storage, "json", return_value={"candidate_id": "invalid"}):
                self.assertIsNone(comparator.read_cache(key, [historical["id"]]))
            with mock.patch.object(comparator.storage, "json", side_effect=OSError("missing optional artifact")):
                self.assertIsNone(comparator.read_cache(key, [historical["id"]]))
            self.assertFalse((Path(tmp) / filename).exists())


class ComparatorLifecycleRegressionTests(unittest.TestCase):
    def test_timeout_and_interrupt_stop_same_group_child_and_preserve_unknown_cost(self):
        current, historical = card(issue()), card(issue(2, cause="Independent race"))
        payload = {"current": current["evidence"],
                   "candidates": [dict(candidate_id=historical["id"], **historical["evidence"])]}
        for exception in (subprocess.TimeoutExpired("mocked-claude", 180), KeyboardInterrupt()):
            with self.subTest(exception=type(exception).__name__), tempfile.TemporaryDirectory() as tmp, \
                    mock.patch.dict(os.environ, {"OPENSHIFT_CI": "true"}, clear=True):
                process = mock.Mock(returncode=0)
                process.communicate.side_effect = [exception, ("", "")]
                with mock.patch.object(history.subprocess, "Popen", return_value=process) as popen:
                    comparator = history.Comparator(tmp, "model", 2)
                    expected = history.HistoryError if isinstance(exception, subprocess.TimeoutExpired) else KeyboardInterrupt
                    with self.assertRaises(expected):
                        comparator.invoke(payload, "compare")
                self.assertFalse(popen.call_args.kwargs["start_new_session"])
                process.kill.assert_called_once()
                self.assertTrue(history.read_json(Path(tmp) / "cost.json")["unaccounted_call"])


class PullRequestMetadataRegressionTests(unittest.TestCase):
    def test_started_without_refs_loads_matching_batched_pull_from_prowjob(self):
        prefix = "pr-logs/pull/org_repo/12/job/"
        ref = history.execution_ref(f"gs://{history.BUCKET}/{prefix}123")
        def metadata(path, name):
            if name == "started":
                return {"timestamp": (AS_OF - timedelta(days=1)).timestamp(), "pull": "12", "repos": {}}
            if name == "finished":
                return {"timestamp": AS_OF.timestamp(), "result": "SUCCESS"}
            self.assertEqual(name, "prowjob")
            return {"spec": {"refs": {"base_sha": "base", "pulls": [
                {"number": 99, "sha": "wrong-first-pull"}, {"number": 12, "sha": "matching-head"}]}}}
        with tempfile.TemporaryDirectory() as tmp:
            storage = history.PublicStorage(tmp)
            with mock.patch.object(storage, "prefixes", return_value=[prefix + "123/"]), \
                    mock.patch.object(storage, "metadata", side_effect=metadata) as fetch:
                rows, gaps = history.metadata_observations(storage, {ref["id"]: ref}, START, AS_OF)
            self.assertEqual(rows[ref["id"]]["head_sha"], "matching-head")
            self.assertEqual(rows[ref["id"]]["base_sha"], "base")
            self.assertEqual(gaps, [])
            self.assertEqual([call.args[1] for call in fetch.call_args_list], ["started", "finished", "prowjob"])

    def test_complete_started_refs_need_no_extra_prowjob_lookup(self):
        prefix = "pr-logs/pull/org_repo/12/job/"
        ref = history.execution_ref(f"gs://{history.BUCKET}/{prefix}123")
        def metadata(path, name):
            if name == "started":
                return {"timestamp": (AS_OF - timedelta(days=1)).timestamp(),
                        "refs": {"base_sha": "base", "pulls": [{"number": 12, "sha": "head"}]}}
            self.assertEqual(name, "finished")
            return {"timestamp": AS_OF.timestamp(), "result": "SUCCESS"}
        with tempfile.TemporaryDirectory() as tmp:
            storage = history.PublicStorage(tmp)
            with mock.patch.object(storage, "prefixes", return_value=[prefix + "123/"]), \
                    mock.patch.object(storage, "metadata", side_effect=metadata):
                rows, gaps = history.metadata_observations(storage, {ref["id"]: ref}, START, AS_OF)
            self.assertEqual(rows[ref["id"]]["head_sha"], "head")
            self.assertEqual(gaps, [])

    def test_missing_prowjob_revisions_preserve_observation_with_explicit_gap(self):
        prefix = "pr-logs/pull/org_repo/12/job/"
        ref = history.execution_ref(f"gs://{history.BUCKET}/{prefix}123")
        def metadata(path, name):
            if name == "started":
                return {"timestamp": (AS_OF - timedelta(days=1)).timestamp()}
            if name == "finished":
                return {"timestamp": AS_OF.timestamp(), "result": "SUCCESS"}
            raise OSError("prowjob unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            storage = history.PublicStorage(tmp)
            with mock.patch.object(storage, "prefixes", return_value=[prefix + "123/"]), \
                    mock.patch.object(storage, "metadata", side_effect=metadata):
                rows, gaps = history.metadata_observations(storage, {ref["id"]: ref}, START, AS_OF)
            self.assertEqual(rows[ref["id"]]["status"], "success")
            self.assertIsNone(rows[ref["id"]]["head_sha"])
            self.assertTrue(any("PR revision metadata unavailable" in gap for gap in gaps))

    def test_prowjob_revision_spec_is_cached_as_flat_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = history.PublicStorage(tmp)
            data = {"spec": {"refs": {"base_sha": "base", "pulls": [{"number": 12, "sha": "head"}]}}}
            with mock.patch.object(storage, "object", return_value=data) as fetch:
                self.assertEqual(storage.metadata("pr-logs/pull/org_repo/12/job/123", "prowjob"), data)
                self.assertEqual(storage.metadata("pr-logs/pull/org_repo/12/job/123", "prowjob"), data)
            fetch.assert_called_once()
            self.assertEqual(len(list(Path(tmp).glob("metadata-*.json"))), 1)

    def test_same_pr_head_with_different_bases_cannot_establish_intermittency(self):
        def pr_issue(build, date, base):
            value = issue(build, date)
            value["affected_jobs"][0].update({
                "url": f"gs://{history.BUCKET}/pr-logs/pull/org_repo/12/job/{build}",
                "head_sha": "same-head", "base_sha": base})
            return card(value)
        historical = pr_issue(1, "2026-11-05T10:00:00Z", "old-base")
        current = pr_issue(3, "2026-11-15T10:00:00Z", "new-base")
        ref = history.execution_ref(f"gs://{history.BUCKET}/pr-logs/pull/org_repo/12/job/2")
        row = {**ref, "date": "2026-11-10T10:00:00Z", "status": "success",
               "head_sha": "same-head", "base_sha": "new-base"}
        result = history.build_history(current, [historical], {historical["id"]: decision(historical)},
                                       {ref["id"]: row}, START, AS_OF, [])
        self.assertEqual(len(result["timelines"]), 2)
        self.assertTrue(all(t["comparable"] for t in result["timelines"]))
        self.assertNotEqual(result["pattern"], "intermittent evidence")


class RehearsalAnchorRegressionTests(unittest.TestCase):
    def test_presubmit_job_spec_resolves_rehearsal_started_path(self):
        job = "rehearse-999-" + DOCTOR
        spec = {"type": "presubmit", "refs": {"org": "openshift", "repo": "release",
                "pulls": [{"number": 12345, "sha": "head"}]}}
        environ = {"JOB_NAME": job, "BUILD_ID": "456", "JOB_SPEC": json.dumps(spec)}
        path = f"pr-logs/pull/openshift_release/12345/{job}/456"
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, environ, clear=True):
            storage = history.PublicStorage(tmp)
            self.assertEqual(history.current_execution()["path"], path)
            with mock.patch.object(storage, "metadata", return_value={"timestamp": AS_OF.timestamp()}) as fetch:
                self.assertEqual(history.resolve_anchor(storage, component="microshift"), AS_OF)
            fetch.assert_called_once_with(path, "started")

    def test_standard_presubmit_environment_resolves_without_job_spec(self):
        job = "rehearse-999-" + DOCTOR
        environ = {"JOB_NAME": job, "BUILD_ID": "456", "JOB_TYPE": "presubmit",
                   "REPO_OWNER": "openshift", "REPO_NAME": "release", "PULL_NUMBER": "12345"}
        with mock.patch.dict(os.environ, environ, clear=True):
            self.assertEqual(history.current_execution()["path"],
                             f"pr-logs/pull/openshift_release/12345/{job}/456")

    def test_matching_saved_presubmit_identity_can_disambiguate_batch(self):
        job = "rehearse-999-" + DOCTOR
        source = history.execution_ref(f"gs://{history.BUCKET}/pr-logs/pull/openshift_release/12345/{job}/456")
        environ = {"JOB_NAME": job, "BUILD_ID": "456", "JOB_TYPE": "batch"}
        with mock.patch.dict(os.environ, environ, clear=True):
            self.assertEqual(history.current_execution(source), source)
            self.assertIsNone(history.current_execution())


class CrossReleaseRetrievalRegressionTests(unittest.TestCase):
    def test_other_release_survives_more_than_sixty_same_release_cards(self):
        current = card(issue())
        crowded = [card(issue(i, cause="The LV thin pool exhausted metadata analysis %d" % i))
                   for i in range(1, 81)]
        paraphrase = release_card("4.18", 9, "Device-mapper bookkeeping capacity reached its limit")
        ranked = history.retrieve_candidates(current, crowded + [current, paraphrase])
        self.assertEqual(len(ranked), 81)
        self.assertNotIn(current["id"], {c["id"] for c in ranked})
        self.assertIn(paraphrase["id"], {c["id"] for c in ranked[:2]})
        self.assertIn(ranked[0]["id"], {c["id"] for c in crowded})
        self.assertEqual(history.job_family(JOB),
                         history.job_family("rehearse-123-" + JOB.replace("4.20", "4.18")))
        with tempfile.TemporaryDirectory() as tmp:
            comparator = history.Comparator(tmp, "model", 2)
            with mock.patch.object(comparator, "invoke", return_value={"candidate_ids": [paraphrase["id"]]}) as invoke:
                selected, gaps = comparator.shortlist(current, ranked)
        self.assertEqual(selected, {paraphrase["id"]})
        self.assertLessEqual(len(invoke.call_args.args[0]["candidates"]), history.SHORTLIST_CANDIDATES)
        self.assertTrue(any("summary budget excludes" in gap for gap in gaps))

    def test_many_families_and_prs_cannot_crowd_out_other_releases(self):
        current = card(issue())
        crowded = []
        for number in range(30):
            value = issue(number, cause=f"The LV thin pool exhausted metadata in family {number}")
            job = JOB + f"-family-{number}"
            value["affected_jobs"][0].update(name=job, url=url(number, job))
            crowded.append(card(value))
            pr_issue = issue(number, cause=f"The LV thin pool exhausted metadata in PR {number}")
            pr_job = f"pull-ci-microshift-family-{number}"
            pr_issue["affected_jobs"][0].update(name=pr_job, url=(
                f"https://prow.ci.openshift.org/view/gs/{history.BUCKET}/pr-logs/pull/"
                f"openshift_microshift/{7000 + number}/{pr_job}/{number}"))
            indexed = {}
            history.index_report({"pr_data": {"prs": [
                {"number": 7000 + number, "issues": [pr_issue]}]}}, {}, indexed, {}, [])
            crowded.extend(indexed.values())
        other_releases = [release_card(release, number, f"Device-mapper capacity limit mechanism {number}")
                          for number, release in enumerate(("4.18", "4.22", "main"), 500)]
        selected = history.retrieve_candidates(current, crowded + other_releases)[:history.MAX_CANDIDATES]
        self.assertTrue({c["id"] for c in other_releases} <= {c["id"] for c in selected})
        scopes = {scope for c in selected for scope in c["scopes"]}
        self.assertTrue({"release:4.18", "release:4.22", "release:main"} <= scopes)
        self.assertIn("release:4.20", scopes)
        self.assertTrue(any(scope.startswith("pr:") for scope in scopes))

    def test_mechanism_relevance_precedes_exact_job_name(self):
        current = card(issue())
        same_job = card(issue(1, cause="Registry authentication rejected credentials"))
        same_mechanism = release_card("4.21", 2)
        ranked = history.retrieve_candidates(current, [same_job, same_mechanism])
        self.assertEqual(ranked[0]["id"], same_mechanism["id"])

    def test_narrow_current_releases_still_search_all_historical_releases(self):
        current_issue = issue()
        historical = [release_card("4.18", 1, "Device-mapper metadata filled"),
                      release_card("4.21", 2, "Thin provisioning metadata exhausted")]
        coverage = {"complete": True, "reports_found": 1, "reports_loaded": 1, "gaps": []}
        def compare(payload, mode):
            self.assertEqual(mode, "compare")
            return {"comparisons": [decision(c) for c in historical
                                     if c["id"] in {row["candidate_id"] for row in payload["candidates"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Path(tmp) / "jobs"
            jobs.mkdir()
            history.write_json(jobs / "release-4.20-summary.json", {"issues": [current_issue]})
            history.write_json(jobs / "release-4.21-summary.json", {"issues": [issue(cause="Not a current target")]})
            with mock.patch.object(history, "acquire_reports", return_value=(historical, {}, coverage)), \
                    mock.patch.object(history, "metadata_observations", return_value=({}, [])), \
                    mock.patch.object(history.Comparator, "invoke", side_effect=compare), \
                    mock.patch.object(history.subprocess, "Popen", side_effect=AssertionError("no local model")):
                result = history.run("microshift", tmp, ["4.20"], as_of=history.iso(AS_OF))
        self.assertEqual(set(result["issues"]), {history.issue_key(current_issue)})
        value = result["issues"][history.issue_key(current_issue)]
        self.assertEqual(value["occurrences"], 3)
        self.assertEqual({scope for match in value["matches"] for scope in match["scopes"]},
                         {"release:4.18", "release:4.21"})

    def test_same_execution_reuse_is_neither_a_slot_nor_a_fresh_match(self):
        current = card(issue())
        with tempfile.TemporaryDirectory() as tmp:
            comparator = history.Comparator(tmp, "model", 2, fetch_only=True)
            with mock.patch.object(comparator, "invoke", side_effect=AssertionError("same execution is already assessed")):
                states = history.assess_candidates([current], [current], comparator)
                reused = comparator.reuse(current, [current])
        self.assertFalse(states[current["id"]]["attempted"])
        self.assertFalse(states[current["id"]]["ranked"])
        result = history.build_history(current, [current], reused, {}, START, AS_OF, [])
        self.assertEqual(result["occurrences"], 1)
        self.assertEqual(result["matches"], [])

    def test_identical_analysis_on_new_execution_still_needs_supported_comparison(self):
        current = card(issue())
        historical = release_card("4.18", 1)
        self.assertEqual(current["id"], historical["id"])
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(history.subprocess, "Popen", side_effect=AssertionError("no local model")):
            comparator = history.Comparator(tmp, "model", 2, fetch_only=True)
            decisions, gaps = comparator.compare(current, [historical])
        self.assertFalse(decisions)
        self.assertTrue(any("fetch-only" in gap for gap in gaps))

    def test_same_error_different_cause_still_requires_semantic_decision(self):
        current = card(issue())
        historical = release_card("4.18", 1, "An unreachable DNS server prevented API discovery")
        current["retrieval"] = historical["retrieval"] = ["Provisioning timed out", "metadata full"]
        with tempfile.TemporaryDirectory() as tmp:
            comparator = history.Comparator(tmp, "model", 2)
            with mock.patch.object(comparator, "invoke", return_value={
                    "comparisons": [decision(historical, "different_cause", 4)]}) as invoke:
                states = history.assess_candidates([current], [historical], comparator)
        invoke.assert_called_once()
        assessment = states[current["id"]]
        result = history.build_history(current, assessment["ranked"], assessment["decisions"],
                                       {}, START, AS_OF, assessment["gaps"])
        self.assertEqual(result["occurrences"], 1)
        self.assertEqual(result["matches"], [])


class FairComparisonRegressionTests(unittest.TestCase):
    def test_first_comparisons_are_round_robin_and_reserve_budget(self):
        current = [card(issue(100 + i, cause="Current causal mechanism %d" % i)) for i in range(3)]
        historical = [card(issue(i, cause="Historical causal mechanism %d" % i)) for i in range(20)]
        paid = []
        with tempfile.TemporaryDirectory() as tmp:
            comparator = history.Comparator(tmp, "model", 0.3)
            def invoke(payload, mode):
                allowance = comparator.call_budget_usd
                if allowance < 0.01:
                    raise history.HistoryError("history comparison budget exhausted")
                paid.append((payload["current"]["root_cause"], mode, allowance))
                comparator.cost += allowance
                self.assertEqual(mode, "compare")
                return {"comparisons": [
                    dict(candidate_id=c["candidate_id"], score=5, verdict="different_cause",
                         reason="Mechanisms differ", contradictions=[]) for c in payload["candidates"]]}
            with mock.patch.object(comparator, "invoke", side_effect=invoke):
                states = history.assess_candidates(current, historical, comparator)
        self.assertEqual([row[0] for row in paid], [c["evidence"]["root_cause"] for c in current])
        self.assertEqual([row[1] for row in paid], ["compare"] * 3)
        for _, _, allowance in paid:
            self.assertAlmostEqual(allowance, 0.1)
        self.assertAlmostEqual(comparator.cost, 0.3)
        for state in states.values():
            self.assertEqual(len(state["decisions"]), 2)
            self.assertTrue(any("Candidate budget assessed 2 of 20" in gap for gap in state["gaps"]))

    def test_symmetric_direct_pair_reuse_frees_later_issues_and_cross_release_cards(self):
        first = release_card("4.20", 1)
        second = release_card("4.21", 2, "Device-mapper bookkeeping capacity reached its limit")
        with tempfile.TemporaryDirectory() as tmp:
            comparator = history.Comparator(tmp, "model", 2)
            with mock.patch.object(comparator, "invoke", return_value={
                    "comparisons": [decision(second)]}) as invoke:
                states = history.assess_candidates([first, second], [first, second], comparator)
                other_first = release_card("4.18", 3)
                other_second = release_card("4.22", 4, second["evidence"]["root_cause"])
                reused, gaps = comparator.compare(other_second, [other_first])
            invoke.assert_called_once()
            self.assertEqual(len(list(Path(tmp).glob("cache-*.json"))), 1)
            self.assertEqual(comparator.cache_key(first, second), comparator.cache_key(other_second, other_first))
        self.assertEqual(states[first["id"]]["decisions"][second["id"]]["verdict"], "same_cause")
        self.assertEqual(states[second["id"]]["decisions"][first["id"]]["verdict"], "same_cause")
        self.assertFalse(states[second["id"]]["attempted"])
        self.assertEqual(reused[other_first["id"]]["candidate_id"], other_first["id"])
        self.assertEqual(gaps, [])

    def test_direct_supported_edges_do_not_create_transitive_matches(self):
        first = card(issue(1))
        middle = card(issue(2, cause="Bookkeeping capacity reached its limit"))
        last = card(issue(3, cause="Controller reconciliation stalled"))
        with tempfile.TemporaryDirectory() as tmp:
            comparator = history.Comparator(tmp, "model", 2)
            with mock.patch.object(comparator, "invoke", side_effect=[
                    {"comparisons": [decision(middle)]}, {"comparisons": [decision(last)]}]):
                comparator.compare(first, [middle])
                comparator.compare(middle, [last])
            comparator.fetch_only = True
            with mock.patch.object(history.subprocess, "Popen", side_effect=AssertionError("no local model")):
                unknown, gaps = comparator.compare(first, [last])
        self.assertEqual(unknown, {})
        self.assertTrue(any("fetch-only" in gap for gap in gaps))

    def test_reverse_failed_pair_is_not_retried_in_same_assessment(self):
        first, second = card(issue()), card(issue(1, cause="Different mechanism"))
        with tempfile.TemporaryDirectory() as tmp:
            comparator = history.Comparator(tmp, "model", 2)
            with mock.patch.object(comparator, "invoke", side_effect=history.HistoryError("incomplete response")) as invoke:
                forward, first_gaps = comparator.compare(first, [second])
                reverse, second_gaps = comparator.compare(second, [first])
        invoke.assert_called_once()
        self.assertEqual(forward, {})
        self.assertEqual(reverse, {})
        self.assertEqual(first_gaps, second_gaps)

    def test_v1_directional_decision_can_be_reused_in_reverse(self):
        first, second = card(issue()), card(issue(1, cause="Different mechanism"))
        old_key = history.digest(["doctor-causal-v1", "model", first["id"], second["id"],
                                  first["evidence"], second["evidence"]])
        with tempfile.TemporaryDirectory() as tmp:
            history.write_json(Path(tmp) / ("cache-" + old_key + ".json"),
                               decision(second, "different_cause", 3))
            comparator = history.Comparator(tmp, "model", 2, fetch_only=True)
            with mock.patch.object(comparator, "invoke", side_effect=AssertionError("legacy direct cache is supported")):
                decisions, gaps = comparator.compare(second, [first])
            self.assertTrue((Path(tmp) / ("cache-" + comparator.cache_key(first, second) + ".json")).exists())
        self.assertEqual(decisions[first["id"]]["verdict"], "different_cause")
        self.assertEqual(decisions[first["id"]]["candidate_id"], first["id"])
        self.assertEqual(gaps, [])

    def test_per_opportunity_allowance_reaches_cli_without_raising_run_ceiling(self):
        current = card(issue())
        historical = card(issue(1, cause="Different mechanism"))
        payload = {"current": current["evidence"],
                   "candidates": [dict(candidate_id=historical["id"], **historical["evidence"])]}
        process = mock.Mock(returncode=0)
        process.communicate.return_value = (json.dumps({
            "subtype": "success", "total_cost_usd": 0.03,
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "structured_output": {"comparisons": [decision(historical)]}}), "")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"CI": "true"}), \
                mock.patch.object(history.subprocess, "Popen", return_value=process) as popen:
            comparator = history.Comparator(tmp, "model", 2)
            comparator.call_budget_usd = 0.05
            comparator.invoke(payload, "compare")
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("--max-budget-usd") + 1], "0.05000000")
        self.assertEqual(comparator.max_budget, 2)
        self.assertEqual(comparator.cost, 0.03)

    def test_identical_compact_text_does_not_alias_different_full_evidence(self):
        prefix = "The device metadata capacity was exhausted " * 40
        first = card(issue(1, cause=prefix + "because the allocator leaked entries"))
        second = card(issue(2, cause=prefix + "because cleanup was disabled"))
        self.assertEqual(first["evidence"], second["evidence"])
        self.assertNotEqual(first["id"], second["id"])
        with tempfile.TemporaryDirectory() as tmp:
            comparator = history.Comparator(tmp, "model", 2)
            self.assertNotEqual(comparator.cache_key(first, first), comparator.cache_key(first, second))
            with mock.patch.object(comparator, "invoke", return_value={
                    "comparisons": [decision(second, "insufficient_evidence", 30)]}) as invoke:
                decisions, _ = comparator.compare(first, [second])
        invoke.assert_called_once()
        self.assertEqual(decisions[second["id"]]["verdict"], "insufficient_evidence")

    def test_pooled_identical_card_excludes_current_execution_from_historical_match(self):
        current = release_card("4.20", 100)
        pooled = release_card("4.18", 1)
        pooled["jobs"].update(current["jobs"])
        pooled["scopes"].extend(current["scopes"])
        result = history.build_history(current, [pooled], {pooled["id"]: decision(pooled)},
                                       {}, START, AS_OF, [])
        self.assertEqual(result["occurrences"], 2)
        self.assertEqual(result["matches"][0]["occurrences"], 1)
        self.assertEqual(result["matches"][0]["scopes"], ["release:4.18"])
        self.assertEqual(len(result["timelines"]), 2)
        self.assertTrue(all(timeline["matched_failures"] == 1 for timeline in result["timelines"]))

    def test_unconfirmed_causal_decisions_stay_incomplete(self):
        current = card(issue())
        historical = card(issue(1, cause="Unconfirmed mechanism"))
        for verdict in ("insufficient_evidence", "related_symptom"):
            with self.subTest(verdict=verdict):
                result = history.build_history(current, [historical],
                    {historical["id"]: decision(historical, verdict, 45)}, {}, START, AS_OF, [])
                self.assertEqual(result["status"], "incomplete")
                self.assertEqual(result["occurrences"], 1)


if __name__ == "__main__":
    unittest.main()
