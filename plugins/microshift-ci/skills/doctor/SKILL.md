---
name: microshift-ci:doctor
argument-hint: <release1,release2,...>
description: Analyze CI for multiple MicroShift releases and produce an HTML summary
user-invocable: true
allowed-tools: Skill, Bash, Read, Write, Glob, Grep, Agent
---

# microshift-ci:doctor

## Synopsis

```bash
/microshift-ci:doctor <release1,release2,...>
```

## Description

Accepts a comma-separated list of MicroShift release versions, runs analysis for each release and for open rebase PRs, and produces a single HTML summary file. Deterministic scripts handle data collection, artifact download, aggregation, and HTML generation. LLM agents handle per-job root cause analysis and Jira bug correlation.

## Arguments

- `<ARGUMENTS>` (required): Comma-separated list of release versions (e.g., `4.19,4.20,4.21,4.22`)

## Prerequisites

`gsutil`, `gh` (authenticated), Jira MCP server, Python 3, `pcp-export-pcp2json`, `matplotlib`.

## Work Directory

Compute once at the start by running `date +%y%m%d` and substituting into the path below. In all commands, replace `<WORKDIR>` with the computed path — do not use shell variables.

```text
/tmp/microshift-ci-claude-workdir.<YYMMDD>
```

## Implementation Steps

### Step 1: Prepare — Collect and Download All Artifacts

```text
bash plugins/microshift-ci/scripts/doctor.sh prepare --component microshift --workdir <WORKDIR> <ARGUMENTS> --rebase --repo openshift/microshift
```

Outputs: `jobs/release-<version>-jobs.json`, `jobs/prs-jobs.json`, `jobs/prs-status.json`, JSON summary on stdout. Clone failure is non-fatal — agents record it in `analysis_gaps`.

Read the JSON output to know which releases have jobs to analyze.

**Job JSON field names** (use these exactly — do NOT guess alternatives like `job_name`):

- `job` — full job name
- `build_id` — unique build identifier
- `artifacts_dir` — local path to downloaded artifacts
- `url` — Prow job URL
- `status` — job result (`failure`, `FAILURE`, `SUCCESS`, `PENDING`)
- `pr_number` — PR number (PR jobs only)

**Error Handling**:

- If `<ARGUMENTS>` is empty, show usage and stop
- If a release has no failed jobs, its jobs JSON will be an empty array — skip analysis
- If a release has an `"error"` field in the JSON summary, report the error and continue with other releases

### Step 1b: Generate PCP Performance Graphs

```text
bash plugins/microshift-ci/scripts/doctor.sh graphs --component microshift --workdir <WORKDIR>
```

Errors and stops on missing prerequisites (`pcp2json`, `matplotlib`).

### Step 2: Analyze Each Job Using prow-job-analyzer Agent

Do NOT read the job JSON files into the main conversation — the prepare script already printed all job details. Use the JSON summary to build agent prompts.

For **every** failed job (all releases + PRs), launch a separate **Agent** (using the `Agent` tool, NOT `Skill`) with `subagent_type=microshift-ci:prow-job-analyzer`. For PR jobs, only launch agents for jobs with FAILURE status.

**For release jobs:**

```text
Agent: subagent_type=microshift-ci:prow-job-analyzer, prompt="Analyze this prow job:
artifacts_dir: <ARTIFACTS_DIR>
graphs_dir: <WORKDIR>/graphs/<JOB_ID>
source_dir: <WORKDIR>/src/microshift-release-<RELEASE> (or <WORKDIR>/src/microshift for main)
job_url: <JOB_URL>
job_name: <JOB_NAME>"
```

**For PR jobs:**

```text
Agent: subagent_type=microshift-ci:prow-job-analyzer, prompt="Analyze this prow job:
artifacts_dir: <ARTIFACTS_DIR>
graphs_dir: <WORKDIR>/graphs/<BUILD_ID>
source_dir: <WORKDIR>/src/microshift
job_url: <JOB_URL>
job_name: <JOB_NAME>"
```

Substitute from the prepare script's JSON output (`artifacts_dir`, `build_id`, `release`, `url`, `job` fields). Only include `graphs_dir` and `source_dir` if those directories exist.

Save each agent's JSON response:
- Release jobs: `<WORKDIR>/jobs/release-<RELEASE>-job-<N>-<JOB_ID>.json`
- PR jobs: `<WORKDIR>/jobs/prs-job-<N>-pr<PR>-<JOB_NAME_SUFFIX>.json`

Launch **ALL** agents in a **single message** as **foreground** agents (do NOT use `run_in_background`) — foreground agents in the same message run concurrently but keep your turn active until all complete. Proceed to Step 3 immediately without ending your turn.

### Step 3: Run Bug Correlation (Dry-Run)

Collect all release versions + rebase PR source identifiers into a comma-separated list. Launch a **single** `microshift-ci:create-bugs` **foreground** agent:

```text
Agent: subagent_type=general_purpose, prompt="Run /microshift-ci:create-bugs <all-sources-comma-separated>"
```

Outputs: `<WORKDIR>/bugs/bug-matches-<source>.json`, `<WORKDIR>/report-create-bugs.txt`.

If create-bugs fails, note the failure but do not block HTML generation. Proceed to Step 4 immediately.

### Step 4: Finalize — Aggregate and Generate HTML Report

**MANDATORY** — the task is incomplete without this step. Run even if previous steps produced errors.

```text
bash plugins/microshift-ci/scripts/doctor.sh finalize --component microshift --workdir <WORKDIR> <ARGUMENTS>
```

Generates `report-microshift-ci-doctor.html`. Report the script's output to the user.

### Step 5: Report Completion

Display the HTML file path. Summarize failed job counts per release, rebase PR status, and bug correlation results. Example:

```text
Release 4.19: 3 failed · Release 4.20: ERROR · Release 4.22: 12 failed
2 rebase PRs, 5 failed jobs · HTML report: <WORKDIR>/report-microshift-ci-doctor.html
```

`/microshift-ci:doctor-refresh` regenerates the HTML report from existing data — use it after `/microshift-ci:create-bugs --create` to include newly created bugs.
