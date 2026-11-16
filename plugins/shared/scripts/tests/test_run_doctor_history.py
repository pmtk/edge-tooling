#!/usr/bin/env python3
"""History outages must not hide fresh RCA or masquerade as cached success."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "run-doctor.py"
SPEC = importlib.util.spec_from_file_location("run_doctor_history_test", SCRIPT)
doctor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(doctor)


class HistoryPipelineTests(unittest.TestCase):
    def pipeline(self, root):
        pipeline = doctor.DoctorPipeline.__new__(doctor.DoctorPipeline)
        pipeline.workdir = Path(root)
        pipeline.logs_dir = Path(root) / "logs"
        pipeline.logs_dir.mkdir()
        pipeline.plugin_dir = SCRIPT.parent
        pipeline.component = "microshift"
        pipeline.releases = ["4.22"]
        pipeline.model = "unused-model"
        pipeline.args = SimpleNamespace(history_days=14, history_as_of=None,
                                        history_max_budget_usd=2.0)
        pipeline.message = mock.Mock()
        return pipeline

    def test_failed_history_replaces_stale_success_and_still_finalizes(self):
        with tempfile.TemporaryDirectory() as root:
            pipeline = self.pipeline(root)
            result_path = Path(root) / "history" / "results.json"
            result_path.parent.mkdir()
            result_path.write_text(json.dumps({"coverage": {"complete": True},
                                               "issues": {"stale": {"occurrences": 99}}}))
            process = mock.Mock(returncode=1)
            (result_path.parent / "cost.json").write_text(json.dumps({
                "cost_usd": 0.42, "unaccounted_call": True,
            }))
            with mock.patch.object(doctor.subprocess, "Popen", return_value=process):
                pipeline.stages = ["history", "finalize"]
                pipeline.prepare = pipeline.graphs = pipeline.analyze = pipeline.bugs = lambda: True
                pipeline.compute_costs = mock.Mock()
                pipeline.finalize = mock.Mock(return_value=True)
                pipeline.diagnostics_file = Path(root) / "diagnostics.txt"
                self.assertEqual(pipeline.run(), 1)
            pipeline.finalize.assert_called_once()
            saved = json.loads(result_path.read_text())
            self.assertFalse(saved["coverage"]["complete"])
            self.assertNotIn("stale", saved["issues"])
            self.assertTrue(saved["coverage"]["gaps"])
            self.assertEqual(saved["cost_usd"], 0.42)
            self.assertFalse(saved["cost_accounting_complete"])

    def test_explicit_disable_does_not_display_saved_history(self):
        with tempfile.TemporaryDirectory() as root:
            args = SimpleNamespace(component="microshift", workdir=root,
                                   stages=None, no_history=True, history_days=14,
                                   releases="4.22", model="unused", parallel=1,
                                   predecessor_workdir=None, no_predecessor_reuse=False)
            previous = Path(root) / "history" / "results.json"
            previous.parent.mkdir()
            previous.write_text('{"issues":{"stale":{}}}')
            plugin_dir = SCRIPT.parents[2] / "microshift-ci"
            with mock.patch.object(doctor, "detect_plugin_dir", return_value=str(plugin_dir)):
                pipeline = doctor.DoctorPipeline(args)
            self.assertNotIn("history", pipeline.stages)
            saved = json.loads(previous.read_text())
            self.assertEqual(saved["issues"], {})
            self.assertIn("disabled", saved["coverage"]["gaps"][0])


if __name__ == "__main__":
    unittest.main()
