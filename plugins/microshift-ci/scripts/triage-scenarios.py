#!/usr/bin/env python3
"""Triage a jobs JSON file into scenario-based and direct-test jobs.

For scenario-based jobs, identifies failing scenarios by checking junit.xml.
Outputs structured JSON to stdout for use by the doctor skill.

Usage: triage-scenarios.py <jobs-json-file>
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree


def extract_release(job_name: str) -> str:
    m = re.search(r"release-(\d+\.\d+)", job_name)
    return m.group(1) if m else "main"


def read_finished_date(artifacts_dir: str) -> str:
    path = os.path.join(artifacts_dir, "finished.json")
    try:
        with open(path) as f:
            data = json.load(f)
        ts = data.get("timestamp")
        if ts:
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return ""


def find_scenario_info(artifacts_dir: str):
    """Find scenario-info directories and return (step_name, test_name, scenario_info_dir)."""
    artifacts_sub = os.path.join(artifacts_dir, "artifacts")
    if not os.path.isdir(artifacts_sub):
        return None

    for test_name in os.listdir(artifacts_sub):
        test_path = os.path.join(artifacts_sub, test_name)
        if not os.path.isdir(test_path):
            continue
        for step_name in os.listdir(test_path):
            scen_dir = os.path.join(test_path, step_name, "artifacts", "scenario-info")
            if os.path.isdir(scen_dir):
                return step_name, test_name, scen_dir

    return None


def scenario_failed(scenario_dir: str) -> bool:
    junit_path = os.path.join(scenario_dir, "junit.xml")
    if not os.path.isfile(junit_path):
        return True

    try:
        tree = ElementTree.parse(junit_path)
        root = tree.getroot()
        for ts in [root] if root.tag == "testsuite" else root.iter("testsuite"):
            failures = int(ts.get("failures", "0"))
            errors = int(ts.get("errors", "0"))
            if failures > 0 or errors > 0:
                return True
    except (ElementTree.ParseError, ValueError):
        return True

    return False


def triage(jobs_file: str) -> dict:
    with open(jobs_file) as f:
        jobs = json.load(f)

    if not isinstance(jobs, list):
        return {"scenario_jobs": [], "direct_test_jobs": []}

    scenario_jobs = []
    direct_test_jobs = []

    for job in jobs:
        status = job.get("status", "").upper()
        if status != "FAILURE":
            continue

        artifacts_dir = job.get("artifacts_dir", "")
        if not artifacts_dir or not os.path.isdir(artifacts_dir):
            continue

        result = find_scenario_info(artifacts_dir)
        if result is None:
            direct_test_jobs.append({
                "job": job.get("job", ""),
                "build_id": job.get("build_id", ""),
                "url": job.get("url", ""),
                "artifacts_dir": artifacts_dir,
            })
            continue

        step_name, test_name, scen_info_dir = result

        failing = []
        for name in sorted(os.listdir(scen_info_dir)):
            scen_path = os.path.join(scen_info_dir, name)
            if os.path.isdir(scen_path) and scenario_failed(scen_path):
                failing.append({
                    "name": name,
                    "dir": scen_path,
                })

        if not failing:
            continue

        scenario_jobs.append({
            "job": job.get("job", ""),
            "build_id": job.get("build_id", ""),
            "url": job.get("url", ""),
            "release": extract_release(job.get("job", "")),
            "finished": read_finished_date(artifacts_dir),
            "artifacts_dir": artifacts_dir,
            "step_name": step_name,
            "test_name": test_name,
            "failing_scenarios": failing,
        })

    return {
        "scenario_jobs": scenario_jobs,
        "direct_test_jobs": direct_test_jobs,
    }


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <jobs-json-file>", file=sys.stderr)
        sys.exit(1)

    result = triage(sys.argv[1])
    json.dump(result, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
