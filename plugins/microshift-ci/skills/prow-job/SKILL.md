---
name: microshift-ci:prow-job
argument-hint: <prow-job-url-or-artifacts-dir>
description: Download Prow job artifacts, identify root cause of failure, and produce a structured error report
user-invocable: true
allowed-tools: Bash, Read, Write, Agent
---

# microshift-ci:prow-job

## Synopsis

```bash
/microshift-ci:prow-job <prow-job-url>
/microshift-ci:prow-job <artifacts-dir>
```

## Description

Analyzes a single Prow CI test job by downloading artifacts and running a root cause analysis agent. Accepts either a Prow job URL (downloads artifacts) or a local directory path (uses pre-downloaded artifacts). The analysis produces a structured JSON report that is formatted as human-readable prose for the user.

## Arguments

- `<ARGUMENTS>` (required): Either a job URL or a local artifacts directory path:
  - **Prow URL**: `https://prow.ci.openshift.org/view/gs/test-platform-results/logs/periodic-ci-openshift-microshift-release-4.21-periodics-e2e-aws-ovn-ocp-conformance-serial/1984108354347208704`
  - **GCS web URL**: `https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/logs/periodic-ci-openshift-microshift-release-4.21-periodics-e2e-aws-ovn-ocp-conformance-serial/1984108354347208704`
  - **Local artifacts directory**: `/tmp/microshift-ci-claude-workdir.260404/artifacts/1984108354347208704` (must contain `build-log.txt` and `finished.json`)

## Job Name and Job ID

The Job Name and Job ID are encoded in the URL. There are two URL formats depending on the job type:

**Periodic/postsubmit jobs:**

```text
https://prow.ci.openshift.org/view/gs/test-platform-results/logs/{JOB_NAME}/{JOB_ID}
https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/logs/{JOB_NAME}/{JOB_ID}
```

GCS path: `gs://test-platform-results/logs/{JOB_NAME}/{JOB_ID}/`

**Presubmit (PR) jobs:**

```text
https://prow.ci.openshift.org/view/gs/test-platform-results/pr-logs/pull/openshift_microshift/{PR_NUMBER}/{JOB_NAME}/{JOB_ID}
https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/openshift_microshift/{PR_NUMBER}/{JOB_NAME}/{JOB_ID}
```

GCS path: `gs://test-platform-results/pr-logs/pull/openshift_microshift/{PR_NUMBER}/{JOB_NAME}/{JOB_ID}/`

To determine the GCS path from any job URL, strip the web prefix and replace with `gs://`:

- Prow URL: strip `https://prow.ci.openshift.org/view/gs/`
- GCS web URL: strip `https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/`

## Work Directory

Compute once at the start by running `date +%y%m%d` and substituting into the path below. In all commands, replace `<WORKDIR>` with the computed path — do not store the work directory in a shell variable.

```text
/tmp/microshift-ci-claude-workdir.<YYMMDD>
```

## Prerequisites

- `gsutil` CLI must be installed for GCS access (uses anonymous access on public buckets; only needed for URL input — pre-downloaded artifacts skip it)
- Internet access to fetch job data from Prow/GCS
- Bash shell

## Workflow

The user argument is: `<ARGUMENTS>`

0. **Determine input type and set up artifacts directory**:
   - If `<ARGUMENTS>` is a **local directory path** (starts with `/` and contains `build-log.txt`): set `TMP` to that directory. Skip step 1. Derive `JOB_URL` from the `build-log.txt` "Link to job on registry info site" line, and extract `JOB_NAME` from the URL path (the path segment before the numeric job ID).
   - If `<ARGUMENTS>` is a **URL** (starts with `http`): set `JOB_URL` to `<ARGUMENTS>`. Extract `JOB_NAME` from the URL path (the path segment before the numeric job ID — e.g. `periodic-ci-openshift-microshift-release-4.22-periodics-e2e-aws-ovn-ocp-conformance-serial` from the URL). Create a temporary working directory with `mktemp -d <WORKDIR>/openshift-ci-analysis-XXXX`, set `TMP` to that directory, and proceed to step 1.

1. **Download all artifacts** (skip if using pre-downloaded artifacts from step 0):
   Download all prow job artifacts using `gsutil -q -m cp -r` into the temporary working directory. Derive the GCS path by stripping the web prefix from the job URL (handles both Prow and GCS web URL formats):

   ```bash
   GCS_PATH=$(echo "${PROW_URL}" | sed -e 's|https://prow.ci.openshift.org/view/gs/|gs://|' -e 's|https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/|gs://|')
   gsutil -q -m cp -r "${GCS_PATH}/" ${TMP}/
   ```

   This works for both periodic (`logs/...`) and presubmit PR (`pr-logs/pull/...`) job URLs, and for both Prow and GCS web URL formats.
   This makes all build logs, step logs, and SOS reports available locally for analysis.

2. **Run root cause analysis**:
   Spawn a single Agent with `subagent_type=microshift-ci:prow-job-analyzer` to analyze the artifacts. Build the prompt with:

   - `artifacts_dir`: the `TMP` path from step 0/1
   - `graphs_dir`: check if `<WORKDIR>/graphs/<BUILD_ID>/` exists (where BUILD_ID is extracted from the artifacts path). If it exists, include it. If not, omit.
   - `source_dir`: check if `<WORKDIR>/src/microshift-release-<RELEASE>/` (release jobs) or `<WORKDIR>/src/microshift/` (main) exists. If it exists, include it. If not, omit.

   Example prompt:

   ```text
   Analyze this prow job:
   artifacts_dir: /tmp/microshift-ci-claude-workdir.260710/artifacts/2075422415638237184
   job_url: https://prow.ci.openshift.org/view/gs/test-platform-results/logs/periodic-ci-openshift-microshift-release-4.22-periodics-e2e-aws-ovn-ocp-conformance-serial/2075422415638237184
   job_name: periodic-ci-openshift-microshift-release-4.22-periodics-e2e-aws-ovn-ocp-conformance-serial
   graphs_dir: /tmp/microshift-ci-claude-workdir.260710/graphs/2075422415638237184
   source_dir: /tmp/microshift-ci-claude-workdir.260710/src/microshift-release-4.22
   ```

3. **Display results**:
   Parse the JSON array returned by the agent. For each entry, format the output as:

   ```text
   Error Severity: {severity}/5
   Stack Layer: {stack_layer}
   Step Name: {step_name}
   Error: {raw_error}
   Causal Chain:
     1. {causal_chain[0].cause}
        Evidence: {causal_chain[0].evidence} — "{causal_chain[0].quote}"
     2. {causal_chain[1].cause}
        Evidence: {causal_chain[1].evidence} — "{causal_chain[1].quote}"
     ...
   Confidence: {confidence}
   Suggested Remediation: {remediation}
   ```

   If `TMP` is inside a `<WORKDIR>` structure, also save the raw JSON to:
   `<WORKDIR>/jobs/release-<RELEASE>-job-<JOB_ID>.json`
   (derive RELEASE and JOB_ID from the artifacts path and the JSON content).

4. **Consult previous Doctor reports after saving the independent RCA**:
   - Keep the artifact-grounded RCA unchanged; an old report is a hypothesis, not evidence for this execution.
   - Use a workdir containing only this investigation's job JSON when investigating a single job.
   - Derive `<FINISHED_UTC>` from this job's `finished.json` Unix timestamp, formatted as ISO 8601 UTC.
   - Run the shared history workflow with a two-week lookback:

     ```bash
     python3 plugins/microshift-ci/scripts/doctor_history.py \
       --component microshift --workdir <WORKDIR> --releases <RELEASE> \
       --days 14 --as-of <FINISHED_UTC>
     ```

   - Live semantic comparison is CI-only; local runs can reuse cached decisions. Use `--fetch-only` for explicitly model-free acquisition and leave semantic pilots to CI.
   - Read `<WORKDIR>/history/results.json`. Present a compact match list: score, age, distinct execution count, affected releases, and source link. Search historical causes across releases; `<RELEASE>` selects the current investigation, not a historical version boundary.
   - Report the observed pattern and a short partial-history notice when needed. Keep per-job timelines, comparison/debug details, and methodology out of the user-facing summary; retain them in JSON.
   - Say **first observed**, not **introduced**. Preserve uncertain-match labels; keep detailed coverage/budget gaps in JSON. Do not infer harmlessness from intermittency.
