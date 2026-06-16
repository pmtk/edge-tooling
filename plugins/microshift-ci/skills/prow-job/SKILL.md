---
name: microshift-ci:prow-job
argument-hint: <prow-job-url-or-artifacts-dir>
description: Download Prow job artifacts, identify root cause of failure, and produce a structured error report
user-invocable: true
allowed-tools: Skill, Bash, Read, Write, Glob, Grep, Agent
---

# microshift-ci:prow-job

## Synopsis

```bash
/microshift-ci:prow-job <prow-job-url>
/microshift-ci:prow-job <artifacts-dir>
```

## Description

Analyzes a single Prow CI test job by scanning artifacts for errors and producing a structured failure report. Accepts either a Prow job URL (downloads artifacts) or a local directory path (uses pre-downloaded artifacts).

## Arguments

- `<ARGUMENTS>` (required): Either a job URL or a local artifacts directory path:
  - **Prow URL**: `https://prow.ci.openshift.org/view/gs/test-platform-results/logs/periodic-ci-openshift-microshift-release-4.21-periodics-e2e-aws-ovn-ocp-conformance-serial/1984108354347208704`
  - **GCS web URL**: `https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/logs/periodic-ci-openshift-microshift-release-4.21-periodics-e2e-aws-ovn-ocp-conformance-serial/1984108354347208704`
  - **Local artifacts directory**: `/tmp/microshift-ci-claude-workdir.260404/artifacts/1984108354347208704` (must contain `build-log.txt` and `finished.json`)

## Goal

Reduce noise for developers by processing large logs from a CI test pipeline and producing a verified root cause analysis, not just the first error found. A report is acceptable when:

- The failing step and (for test failures) the failing test/scenario are named
- The causal chain bottoms out in an actionable cause (a specific code, configuration, test, or infrastructure problem someone can act on) — or in an explicitly recorded evidence gap
- Every causal-chain link cites evidence from the artifacts (file path and line where applicable)
- The analysis determines whether the **product** or the **test** is at fault. The purpose of this analysis is to surface product defects — NOT to make tests green. "Make the test wait/retry/tolerate" is not a root cause unless the product behavior has been shown to be correct.

## Audience

Software Engineer

## Glossary

- **ci-config**: Top level configuration file specifying build inputs, versions, and test workflows to execute. Periodic tests are suffixed with `__periodic.yaml`.
- **test**: The set of configurations and commands that specify how to execute the test. Can be defined in-line in ci-config, or as individual "steps" (see below).
- **step-registry**: Root directory where all openshift-ci test step configs and commands are stored.
- **step**: Smallest component of the test infrastructure. A step yaml specifies the command or script to execute, environmental variables and default values, and step metadata. Also called "ref" or "step ref".
- **chain**: A yaml configuration specifying 1 or more steps or chains in an array. Steps and chains are exploded and executed serially by index. May override step environment variable values.
- **workflow**: A yaml configuration specifying 1 or more steps, chains, or workflows in an array. Steps, chains, and workflows are exploded and executed serially. May override chain or step environmental variable values. Typically referenced by a test in a ci-config.
- **scenario**: MicroShift integration tests are built on the robotframework test framework. A "scenario" represents the RF suite, the test's environment, the microshift deployment, and the virtual machine on which the entire testing process takes place. Scenarios also include the manner of deployment: rpm-ostree, rpm installation, or bootc container.

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

## Important Files

> These files are available after artifacts are downloaded (via the download script or workflow step 0).
> For a map of which artifact answers which question (scenario naming, journal patterns, sosreport layout, timeout cascades), read `references/microshift-ci-primer.md` next to this skill.

- `<TMP>/build-log.txt`: Log containing prow job output and most likely place to identify AWS infra related or hypervisor related errors.
- `<STEP>/build-log.txt`: Each step in the CI job is individually logged in a build-log.txt file.
- `<TMP>/artifacts/<TEST_NAME>/openshift-microshift-infra-sos-aws/artifacts/sosreport-*.tar.xz`: Compressed archive containing select portions of the test host's filesystem, relevant logs, and system configurations. `<TEST_NAME>` varies by job (e.g., `e2e-aws-tests`, `e2e-aws-ovn-ocp-conformance-arm64`).
- `<TMP>/artifacts/<TEST_NAME>/openshift-microshift-e2e-origin-conformance/build-log.txt`: Step-specific build log for origin conformance tests.

## Important Links

**Step Diagram URL** (found at the end of the main build-log):

```text
https://steps.ci.openshift.org/job?org=openshift&repo=microshift&branch=release-4.19&test=e2e-aws-tests-bootc-nightly&variant=periodics
```

This link provides a diagram of the steps that make up the test. Think about reading this diagram when identifying step failures because not all fatal errors cause the current step to fail but may cause the next step to fail.

**SOS Report** (contains a cross-section of the test host's filesystem, including the microshift journal and container logs)

After downloading artifacts locally, find the SOS report at:

```text
<TMP>/artifacts/<TEST_NAME>/openshift-microshift-infra-sos-aws/artifacts/sosreport-*.tar.xz
```

Where `<TEST_NAME>` is the test name directory (e.g., `e2e-aws-tests`, `e2e-aws-ovn-ocp-conformance-serial`). To extract sosreports, run the extraction script — it finds all `sosreport-*.tar.xz` under the given directory, extracts them idempotently, and prints a JSON index of journals, namespace pod logs, and pre-scanned high-signal lines:

```text
bash plugins/microshift-ci/scripts/extract-sosreport.sh <artifacts-or-scenario-dir>
```

Read the printed index instead of browsing the extracted tree: `journals` lists the journalctl output files, `namespace_pod_logs` points at the per-namespace pod log tree (`.../pods/<pod>/<container>/<container>/logs/{current,previous}.log`), and `highlights` pre-greps fatal patterns (panics, OOM kills, `leader election lost`, ...) with file and line. Scope the argument to the failing scenario's directory when possible — extracting every sosreport in a 20-scenario job is slow and unnecessary.

**There may be several sosreports for a single scenario**: the test framework's sos-on-failure listener (`test/resources/sos-on-failure-listener.py` in openshift/microshift) captures a sosreport at the moment of each test failure, in addition to the one collected at the end of the scenario. **Prefer the on-failure sosreport when investigating a specific test failure**: it contains the pods and container logs of the namespaces created specifically for that test (suite), which are absent from the end-of-scenario sosreport because they have already been cleaned up by then. Match a sosreport to its test failure by capture time.

**Check for plain-text journal exports before extracting tarballs**: scenario artifacts often include uncompressed `journal_*.log` files next to the sosreport tarballs (e.g., `scenario-info/<scenario>/vms/host1/sos/journal_*.log`). These are readable directly with Read/Grep — no `tar` needed — and frequently contain the journal evidence you need (service failures, x509 errors, OOM kills). Search them first.

**The plain-text exports are NOT a substitute for extraction when a container crashed or restarted**: pod and container logs — in particular `previous.log`, the only record of WHY a dead container exited — exist exclusively inside the sosreport tarball. When the journal shows `CrashLoopBackOff`, `Back-off restarting`, repeated `Created container` events, or probe failures after readiness, extraction is mandatory: run `extract-sosreport.sh` and read the dead container's `previous.log`. Stopping at "the container restarted" reports the symptom, not the cause.

Correlate journal entries with the failure timestamp recorded during the Characterize phase.

## Performance Graphs

When the input is a local artifacts directory of the form `<WORKDIR>/artifacts/<BUILD_ID>` (the doctor workflow), pre-generated PCP performance graphs may exist in the sibling directory:

```text
<WORKDIR>/graphs/<BUILD_ID>/
  1_cpu_usage.png    — CPU usage (user, system, I/O wait)
  2_mem_usage.png    — Memory usage (used, cached)
  3_disk_io.png      — Disk I/O (read/write OPS, await)
  4_disk_usage.png   — Disk usage by partition (% fill)
```

Use the Read tool to view these PNGs during the drill-down phase whenever the failure involves a timeout, slowness, readiness/health-check expiry, eviction, OOM, or any resource-related error. Look for CPU saturation, memory exhaustion, or disk I/O stalls overlapping the failure window. If the directory does not exist (e.g., standalone URL invocation), skip graph correlation — do not attempt to generate graphs.

## Work Directory

Compute once at the start by running `date +%y%m%d` and substituting into the path below. In all commands, replace `<WORKDIR>` with the computed path — do not store the work directory in a shell variable.

```text
/tmp/microshift-ci-claude-workdir.<YYMMDD>
```

## Common Commands

Scan the build log for arbitrary text:

```bash
grep '${SOME_TEXT}' ${GREP_OPTS} ${TMP}/build-log.txt
```

Download all prow job artifacts (only needed when given a URL, not a local path):

```bash
GCS_PATH=$(echo "${PROW_URL}" | sed -e 's|https://prow.ci.openshift.org/view/gs/|gs://|' -e 's|https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/|gs://|')
gsutil -q -m cp -r "${GCS_PATH}/" ${TMP}/
```

## Workflow

The user argument is: `<ARGUMENTS>`

0. **Determine input type and set up artifacts directory**:
   - If `<ARGUMENTS>` is a **local directory path** (starts with `/` and contains `build-log.txt`): set `TMP` to that directory. Skip step 1.
   - If `<ARGUMENTS>` is a **URL** (starts with `http`): create a temporary working directory with `mktemp -d <WORKDIR>/openshift-ci-analysis-XXXX`, set `TMP` to that directory, and proceed to step 1.

1. **Download all artifacts** (skip if using pre-downloaded artifacts from step 0):
   Download all prow job artifacts using `gsutil -q -m cp -r` into the temporary working directory. Derive the GCS path by stripping the web prefix from the job URL (handles both Prow and GCS web URL formats):

   ```bash
   GCS_PATH=$(echo "${PROW_URL}" | sed -e 's|https://prow.ci.openshift.org/view/gs/|gs://|' -e 's|https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/|gs://|')
   gsutil -q -m cp -r "${GCS_PATH}/" ${TMP}/
   ```

   This works for both periodic (`logs/...`) and presubmit PR (`pr-logs/pull/...`) job URLs, and for both Prow and GCS web URL formats.
   This makes all build logs, step logs, and SOS reports available locally for analysis.

2. **Localize — identify the failed step and the anchor error**:
   - Scan the top level `build-log.txt` to determine the step where the failure occurred (the last `Running step ...` line before the container logs is a quick anchor — see Tips), then open that step's own `build-log.txt`.
   - Record each candidate error with its filepath, line number, and timestamp. Read 50 lines before and 50 lines after each to separate the fatal error from setup/teardown noise.
   - Select the **anchor error**: the first fatal error that caused the step to fail. This becomes `raw_error` in the report.
   - **The anchor identifies the failure for deduplication — it is NOT the conclusion of the investigation. The first error found is rarely the root cause.**

3. **Characterize — establish exactly WHAT failed before asking why**:
   - For test steps with scenarios: enumerate the failing tests from `scenario-info/<scenario>/junit.xml` under the step's artifacts. A scenario has failed when its junit.xml contains `failures="[1-9]"` or `errors="[1-9]"` in the `<testsuite>` tag, or when `rf-debug.log` contains `| FAIL |`, or when junit.xml is missing/truncated (indicating a timeout kill). Record the failing scenario name(s) — the top-level `testsuite name` in each junit.xml — they populate the `scenarios` field in the report.
   - **Fan-out decision** — count the failing scenarios identified above:
     - **0 or 1 failing scenarios**: continue with the single-agent analysis below (Phases 3→4→5→6 as written).
     - **2+ failing scenarios**: skip to **Phase 3b (Scenario Sub-Agent Orchestration)** — spawn one sub-agent per failing scenario for parallel deep analysis, then synthesize results. Do NOT proceed with Phases 3 (remaining bullets) or 4 for the individual scenarios yourself.
   - For each failing scenario you investigate (single-scenario path only), run `bash plugins/microshift-ci/scripts/extract-sosreport.sh <scenario-dir>` NOW, before forming hypotheses — it is one cheap, idempotent command and its `highlights` index frequently contains the fatal product-side line (container exits, leader election lost, OOM kills) that the test logs only show as a symptom.
   - For conformance steps: extract the failing test names and their failure output from the step's `build-log.txt`.
   - For build/infra steps: extract the failing command and its complete error output from the step log.
   - Record the failure timestamp(s) — they drive the journal and graph correlation in the next phase.
   - When the MicroShift source checkout is available — check with Glob for `<WORKDIR>/src/microshift-release-<RELEASE>/` (release jobs) or `<WORKDIR>/src/microshift/` (main) — read the failing test's source: Robot Framework suites under `test/suites/`, scenario definitions under `test/scenarios*/`. Its assertions, timeouts, and setup are how you distinguish a test bug from a product bug. If the checkout is absent, note `"source checkout not available"` in `analysis_gaps` and continue.
   - Decide the stack layer: cloud infra, ci-config, hypervisor, or a legitimate test failure — and for test failures, the stage: setup, testing, teardown.

   **Phase 3b — Scenario Sub-Agent Orchestration** (only when 2+ scenarios failed):

   When multiple scenarios failed in a scenario-based test step, spawn one sub-agent per failing scenario for parallel deep analysis. Each sub-agent performs the drill-down investigation independently, scoped to a single scenario's artifacts.

   **Launch all scenario sub-agents in a single message** using the Agent tool (foreground, concurrent execution). Each agent receives this prompt (substitute the `<PLACEHOLDERS>`):

   ```text
   Agent: subagent_type=general_purpose, prompt="Perform root cause analysis on this single CI test scenario failure.

   Scenario: <SCENARIO_NAME>
   Scenario artifacts directory: <SCENARIO_DIR>
   Job URL: <JOB_URL>
   Performance graphs (if present): <GRAPHS_DIR>
   MicroShift source (if present): <SRC_DIR>

   ## Investigation steps

   1. Read <SCENARIO_DIR>/junit.xml — identify which tests failed, record their names
   2. Read <SCENARIO_DIR>/rf-debug.log — search for | FAIL | markers to understand the failure sequence and timestamps
   3. Check for plain-text journal exports at <SCENARIO_DIR>/vms/host1/sos/journal_*.log FIRST — these are readable without extraction and often contain the evidence you need (service failures, x509 errors, OOM kills)
   4. Run: bash plugins/microshift-ci/scripts/extract-sosreport.sh <SCENARIO_DIR>
      Read the printed JSON index — check highlights for panics, OOM kills, leader election lost. Scope the extraction to this scenario directory only.
   5. If a container restarted (CrashLoopBackOff, repeated 'Created container' in journal, PLEG events), read the dead container's previous.log inside the extracted sosreport at sos_commands/microshift/namespaces/<ns>/pods/<pod>/<container>/<container>/logs/previous.log — its tail states the exit reason
   6. When the failure involves timeouts, slowness, readiness/health-check expiry, eviction, or resource errors, Read performance graphs at <GRAPHS_DIR> (1_cpu_usage.png, 2_mem_usage.png, 3_disk_io.png, 4_disk_usage.png) and look for saturation overlapping the failure window
   7. If MicroShift source is available at <SRC_DIR>, read the failing test source under test/suites/ and scenario definitions under test/scenarios*/ — this is how you distinguish a test bug from a product bug

   ## Root cause attribution rules

   - A test-layer fix is NEVER the bottom when a product component misbehaved. When a component was unavailable, not ready, crashed, or slow, reconstruct its timeline from the journal and pod logs before concluding.
   - Product defect: the component became ready and later flapped, crashed, or stopped serving (readiness flips, liveness probe refused after startup, container exits and restarts). Report the product mechanism as root cause.
   - Test defect: the component was still starting up normally and the test ran too early.
   - Always check for container restarts — two 'Created container' events for the same pod means the first instance died. Read previous.log.
   - Treat 'timed out waiting for X' as a symptom — explain WHY X was slow or absent.

   ## Evidence requirements

   Every causal-chain link must cite its evidence as file:line with a verbatim quote. Before finalizing, re-read every cited file:line to confirm the quote is actually there.

   ## Output format

   Return EXACTLY this structure — nothing else before or after:

   --- SCENARIO ANALYSIS ---
   {
     \"scenario\": \"<SCENARIO_NAME>\",
     \"severity\": <1-5 per severity rubric>,
     \"stack_layer\": \"<AWS Infra|External Infrastructure|build phase|deploy phase|test setup phase|Test Configuration|test|teardown>\",
     \"error_signature\": \"<concise unique one-line description for dedup>\",
     \"root_cause\": \"<mechanism-focused WHY, ~80 chars, cross-release stable>\",
     \"raw_error\": \"<primary error verbatim from log, ~150 chars, timestamps stripped>\",
     \"infrastructure_failure\": <true|false>,
     \"remediation\": \"<suggested fix direction, ~120 chars>\",
     \"confidence\": \"<high|medium|low>\",
     \"causal_chain\": [{\"cause\": \"...\", \"evidence\": \"<file:line>\", \"quote\": \"<verbatim>\"}],
     \"analysis_gaps\": [\"<missing evidence, if any>\"],
     \"failed_tests\": [\"<test names>\"],
     \"failure_timestamp\": \"<ISO 8601 from rf-debug.log or junit>\"
   }
   --- END SCENARIO ANALYSIS ---"
   ```

   **Where the placeholders come from:** `<SCENARIO_NAME>` is the scenario directory name (e.g., `el96-lrel@standard1`). `<SCENARIO_DIR>` is the full path to `<TMP>/artifacts/<TEST_NAME>/openshift-microshift-e2e-metal-tests/artifacts/scenario-info/<SCENARIO_NAME>/`. `<JOB_URL>` is the Prow job URL (from input or reconstructed). `<GRAPHS_DIR>` is `<WORKDIR>/graphs/<BUILD_ID>/` (may not exist for URL-based invocations). `<SRC_DIR>` is `<WORKDIR>/src/microshift-release-<RELEASE>/` or `<WORKDIR>/src/microshift/` (may not exist).

   **Error handling**: if a sub-agent returns no `--- SCENARIO ANALYSIS ---` block or returns malformed JSON, record that scenario with `confidence: "low"`, `analysis_gaps: ["sub-agent analysis failed for this scenario"]`, and use whatever information you gathered during the Localize phase (step name, junit failure count).

   After all sub-agents return, proceed to **Phase 4b (Cross-Scenario Synthesis)**.

4. **Drill down — iterate hypothesis → evidence until the cause is actionable** (single-scenario path, or non-scenario jobs):
   Repeat this loop until you reach a cause that is **actionable** (a specific code, configuration, test, or infrastructure problem someone can act on) or until the available evidence is exhausted:
   - State a hypothesis for WHY the error in hand occurred.
   - Seek confirming or refuting evidence ONE LAYER DEEPER than the current log:
     - **Sosreport** — ALWAYS extract it for failures in the test stage when present: run `bash plugins/microshift-ci/scripts/extract-sosreport.sh <scenario-or-artifacts-dir>` and start from its printed index (see the SOS Report section, including how to pick the right one when several exist). Correlate the microshift journal with the failure timestamp (entries within ±5 minutes), read the pod/container logs of the failing workload, and scan the system journal for OOM kills, segfaults, service restarts, and disk pressure.
     - **Performance graphs** — when the failure involves a timeout, slowness, readiness/health-check expiry, eviction, or any resource error, Read the PNGs (see Performance Graphs section) and look for saturation overlapping the failure window.
   - Treat restating errors as symptoms: an error like "timed out waiting for X" is NOT a root cause — explain why X was slow or absent, or explicitly record that the evidence ran out.
   - **A test-layer fix is never the bottom when a product component misbehaved.** When the failure involves a product component that was unavailable, not ready, crashed, or slow ("no endpoints available", "connection refused", "not ready", "CrashLoopBackOff", probe failures), you MUST reconstruct that component's story from the journal and its pod logs before concluding. Build an exact timestamped timeline: when was the pod created, when did each container start, when did it become ready, did probes fail afterwards, did it restart, and why. Only then attribute the failure:
     - **Product defect** — the component became ready and later flapped, crashed, or stopped serving (e.g., readiness flips back to not-ready, liveness probe connection refused after startup, container exits and restarts). Report the product mechanism as the root cause even if a test-side wait would also "fix" the symptom.
     - **Test defect** — the component was still starting up normally and the test simply ran too early against a documented startup sequence.
   - **Always check for container restarts.** Grep the journal for repeated `Created container`/`Started container` (crio) and `RemoveContainer`/PLEG events (kubelet) for the same pod. Two container instances for one pod means the first one DIED — a single startup story is the wrong narrative. Read the dead container's log to learn why it exited: in the sosreport at `sos_commands/microshift/namespaces/<namespace>/pods/<pod>/<container>/<container>/logs/previous.log` (`current.log` is the running instance). The last ~20 lines of `previous.log` usually state the exit reason (fatal error, leader election lost, panic, OOM).
   - Record every accepted hop as a causal-chain link with its evidence file and line — these become `causal_chain` in the report. Discarded hypotheses do not go into the chain.

   **Phase 4b — Cross-Scenario Synthesis** (only when Phase 3b was used):

   After all scenario sub-agents return, synthesize their results into the entries that will form the final `--- STRUCTURED SUMMARY ---` array:

   1. **Parse each sub-agent's response**: extract the JSON object from between `--- SCENARIO ANALYSIS ---` and `--- END SCENARIO ANALYSIS ---` markers.
   2. **Group by root cause similarity**: compare each pair of scenario results using token-based overlap on `raw_error` + `root_cause` text. When the overlap ratio (shared tokens / min token count) is >= 0.50, the scenarios share a root cause. Group them.
   3. **Merge same-root-cause groups**: for each group of N scenarios with the same root cause, produce ONE structured summary entry:
      - `scenarios`: array of all N scenario names
      - `severity`, `error_signature`, `root_cause`, `raw_error`, `causal_chain`: take from the highest-severity entry in the group (tie-break: longest causal chain)
      - `confidence`: take the MINIMUM confidence across the group (conservative)
      - `analysis_gaps`: merge from all entries in the group (deduplicate)
      - `remediation`: take from the representative entry
   4. **Independent failures**: scenarios whose root cause doesn't match any other scenario get their own entry. Maximum 5 entries per job — if more than 5 independent failures, keep the 5 most severe.
   5. **Cascade detection**: check failure timestamps across all scenario results. If the earliest-failing scenario's failure could poison shared state (hypervisor resource exhaustion, network misconfiguration, disk full), later scenarios failing with different errors may be cascading. When detected, produce ONE entry for the root scenario and note the cascade in `error_signature` (e.g., "disk exhaustion cascading to N scenarios").

   After synthesis, proceed to Phase 5 (Corroborate).

5. **Corroborate — cross-check the explanation**:
   - When the source checkout is available, list commits from the last month that could be related:

     ```text
     bash plugins/microshift-ci/scripts/repo-log.sh <SRC_DIR> --since <1_MONTH_BEFORE_FINISHED> --until <FINISHED_DATE> --paths test/
     ```

     Derive `FINISHED_DATE` from the job's `finished.json` timestamp. Drop `--paths` to see all changes. Name candidate commits in the causal chain when their timing and touched paths match the failure.
   - If multiple scenarios in this job failed, decide cascade vs independent using the **timeline** (which failed first; did the earlier failure poison shared state?), not just error-text similarity.

6. **Produce a report**: Create a concise report of the failure. The report MUST specify:
   - Where in the pipeline the error occurred
   - The specific step the error occurred in
   - Whether the test failure was legitimate (i.e., a test failed) or due to an infrastructure failure (i.e., build image was not found, AWS infra failed due to quota, hypervisor failed to create test host VM, etc.)
   - The causal chain from the observed symptom to the root cause, each link backed by evidence (file and line)
   - A confidence rating for the root cause (see the field rules below)

## Prerequisites

- `gsutil` CLI must be installed for GCS access (uses anonymous access on public buckets; only needed for URL input — pre-downloaded artifacts skip it)
- Internet access to fetch job data from Prow/GCS
- Bash shell

## Tips

1. There are many setup and teardown stages so fatal errors may be buried by log output from the teardown phase. It is not common to find the fatal error at the end of the log.
2. You can quickly determine the failed step from the build-log.txt by reading the last `Running step e2e-aws-tests-bootc-nightly-openshift-microshift-e2e-metal-tests` line before the container logs appear.

## Output Template

Use this template for your error analysis reports:

```text
Error Severity: {1-5, per the rubric below}
Stack Layer: {AWS Infra, External Infrastructure, build phase, deploy phase, test setup phase, Test Configuration, test, teardown}
Step Name: {The specific step where the error occurred}
Error: {The exact error, including additional log context if it relates to the failure}
Causal Chain: {numbered list from observed symptom to root cause; each link cites its evidence as file:line}
Confidence: {high | medium | low — see CONFIDENCE rules below}
Suggested Remediation: {Based on where the error occurs, think hard about how to correct the error ONLY if it requires fixing. Infrastructure failures may not require code changes.}
```

### Severity rubric

| Severity | Meaning |
|---|---|
| 5 | Release-blocking product regression — product broken, no workaround |
| 4 | Persistent product or test failure with no workaround |
| 3 | Persistent failure with a workaround, or scoped to a single scenario/architecture |
| 2 | Intermittent failure / likely flake |
| 1 | Infrastructure noise or self-healing condition |

After the human-readable report above, append a machine-readable JSON block for downstream automation. This block MUST appear at the very end of the report, after all prose and analysis. The block is a JSON array with one object per failure:

```text
--- STRUCTURED SUMMARY ---
[
  {
    "severity": 3,
    "stack_layer": "test",
    "step_name": "openshift-microshift-e2e-metal-tests",
    "error_signature": "cert-manager not ready within greenboot 10m timeout on ARM",
    "root_cause": "greenboot health check timeout during slow ARM service deployment",
    "raw_error": "cert-manager webhook not ready after 600s",
    "infrastructure_failure": false,
    "job_url": "https://prow.ci.openshift.org/view/gs/test-platform-results/logs/periodic-ci-openshift-microshift-release-4.22-periodics-e2e-aws-tests-arm-nightly/123456",
    "job_name": "periodic-ci-openshift-microshift-release-4.22-periodics-e2e-aws-tests-arm-nightly",
    "release": "4.22",
    "remediation": "investigate greenboot timeout configuration for ARM deployments",
    "finished": "2026-06-01",
    "causal_chain": [
      {"cause": "cert-manager webhook pod not Ready before greenboot deadline",
       "evidence": "artifacts/e2e-aws-tests-arm-nightly/openshift-microshift-e2e-metal-tests/artifacts/scenario-info/el96-lrel@standard1/rf-debug.log:2241",
       "quote": "cert-manager webhook not ready after 600s"},
      {"cause": "image pulls saturated disk I/O, delaying all service startups",
       "evidence": "graphs/123456/3_disk_io.png",
       "quote": "write await >800ms during 06:18-06:24 startup window"}
    ],
    "confidence": "medium",
    "analysis_gaps": [],
    "scenarios": ["el96-lrel@standard1", "el94-y2@el96-lrel@standard1"]
  }
]
--- END STRUCTURED SUMMARY ---
```

**Field descriptions:**

- `severity`: 1-5, same as Error Severity above
- `stack_layer`: one of: AWS Infra, External Infrastructure, build phase, deploy phase, test setup phase, Test Configuration, test, teardown
- `step_name`: the CI step where the error occurred
- `error_signature`: a concise, unique one-line description of the root cause — not the full error, just enough to identify and deduplicate this failure
- `root_cause`: one-line description of WHY the failure happened — the underlying mechanism, not the surface symptom (~80 chars max, see rules below)
- `raw_error`: the primary error message copied VERBATIM from the log file (see rules below)
- `infrastructure_failure`: true if stack_layer is AWS Infra or the failure is due to CI infrastructure rather than product code, false otherwise
- `job_url`: the full prow job URL — when given a URL as input, use it directly; when given a local artifacts dir, reconstruct from the build-log.txt "Link to job on registry info site" line or from the directory path structure
- `job_name`: the full job name — extract from the job_url path, or from the build-log.txt "Running step" lines, or from the artifacts directory structure
- `release`: the release branch — extract from job_name (e.g. 4.22 from release-4.22), or from finished.json metadata repos field, or default to "main"
- `remediation`: suggested fix or next step — what should be done to address this failure (~120 chars max). For infrastructure failures, state the infra action (e.g. "retry the job", "rotate AWS credentials"). For product bugs, state the code-level fix direction. Do NOT propose making the test more tolerant (waits, retries, longer timeouts) unless the causal chain shows the product behaved correctly — masking a product flake with a test change hides the defect
- `finished`: the job finish date in YYYY-MM-DD format, extracted from finished.json timestamp field or build log timestamps
- `causal_chain`: array of links from the observed symptom toward the root cause, in order, built during the drill-down phase. Each link is `{"cause": ..., "evidence": ..., "quote": ...}` where `evidence` is the artifact file path (relative to the artifacts dir, with `:line` where applicable) and `quote` is a short verbatim excerpt supporting the link — copied exactly, with NO prepended labels, summaries, or commentary. The evidence path MUST be a file that actually exists — cite the path you read, not a description of it. **Before finalizing the report, re-read every cited `file:line` and confirm the quote is actually there** — a wrong citation destroys trust in the whole analysis and is worse than an honest gap. A single-link chain is valid when the anchor error IS the actionable cause
- `confidence`: one of `high`, `medium`, `low` (see CONFIDENCE rules below)
- `analysis_gaps`: array of strings naming evidence that was missing or could not be checked (e.g. `"no sosreport in artifacts"`, `"source checkout not available"`). Empty array when nothing was skipped
- `scenarios`: array of scenario names in which this failure occurred, taken from the `scenario-info/<scenario>/` directory names or the junit `testsuite name` (e.g. `["el96-lrel@standard2"]`). Empty array `[]` for non-scenario-based jobs and for build/infra failures that happen before scenarios run

### CONFIDENCE rules

- `high`: every causal-chain link, including the final (root) one, is directly evidenced by a quoted artifact line or graph
- `medium`: the mechanism is inferred but consistent with all available evidence; no link is contradicted
- `low`: the analysis is symptom-level only — the chain stops before an actionable cause because the evidence ran out (`analysis_gaps` MUST be populated in this case)

Do NOT inflate confidence: downstream automation uses it to decide whether to act on the analysis. A `low` confidence report with honest gaps is more useful than a `high` confidence guess.

### RAW_ERROR rules

The `RAW_ERROR` field is used by downstream scripts for deterministic grouping. Two runs analyzing the same job MUST produce the same RAW_ERROR. Keep it simple — fewer rules mean less room for variation.

RAW_ERROR is the **deduplication anchor**, not the investigation result: picking the first fatal error here does NOT mean the analysis stops there — the drill-down phase and `causal_chain` capture the actual root cause investigation.

1. **Copy-paste the exact error text** from the log — do NOT paraphrase, summarize, or reword
2. **Pick only ONE error** — the primary error that caused the step to fail. If multiple errors exist, pick the first fatal one.
3. **Only strip timestamps** — remove leading timestamps like `2026-04-01T06:21:48Z`. Keep everything else verbatim, including prefixes like `An error occurred...` or `error:`.
4. **Never concatenate multiple errors** — pick ONE error, not a semicolon-separated list
5. **Truncate to ~150 characters** if the raw message is very long — keep the distinctive part

Examples of good RAW_ERROR values (copied verbatim from logs):

- `An error occurred (InvalidClientTokenId) when calling the CreateStack operation: The security token included in the request is invalid.`
- `panic: runtime error: index out of range [6] with length 6`
- `Process did not finish before 4h0m0s timeout`
- `error: the server doesn't have a resource type "clusterversion"`
- `package github.com/opencontainers/runc/libcontainer/cgroups: module github.com/opencontainers/runc@latest found, but does not contain package`

The ERROR_SIGNATURE field remains as a human-readable description for reports and Jira bug titles.

### ROOT_CAUSE rules

The `ROOT_CAUSE` field captures the underlying mechanism behind the failure — used by downstream scripts alongside `RAW_ERROR` for cross-release deduplication. Two jobs that fail with different surface errors but the same root cause should produce the same `ROOT_CAUSE`.

**How it differs from the other fields:**

- `ERROR_SIGNATURE` = WHAT failed (human-readable, used for bug titles)
- `ROOT_CAUSE` = WHY it failed (mechanism-focused, used for dedup)
- `RAW_ERROR` = verbatim log text (deterministic anchor)

**Rules:**

1. **One line, ~80 characters max** — short enough for token-based matching
2. **Focus on the mechanism**, not the symptom — ask "why did this happen?" not "what error appeared?"
3. **Be consistent across releases** — the same underlying problem in 4.20 and 4.22 MUST produce the same ROOT_CAUSE even if the error messages differ
4. **Use stable terms** — avoid version numbers, timestamps, job names, or other run-specific details

**Examples:**

| ERROR_SIGNATURE | ROOT_CAUSE |
|---|---|
| MonitorTest failures (SCC annotations, disruption pollers) on ARM64 | OCP MonitorTest framework incompatible with MicroShift single-node topology |
| Pod-network-disruption monitor poller CrashLoopBackOff on ARM64 | OCP MonitorTest framework incompatible with MicroShift single-node topology |
| cert-manager not ready within greenboot 10m timeout on ARM | greenboot health check timeout during slow ARM service deployment |
| InvalidClientTokenId when calling CreateStack | expired or invalid AWS credentials in CI environment |

### Multiple independent failures

When a job has multiple independent test failures across different scenarios, produce **one entry per failure** in the JSON array. Each entry must be self-contained with all fields populated.

**Rules:**

1. **One entry per independent failure** — failures are independent when they occur in different test scenarios with different root causes (e.g., cert-manager timeout in one scenario and storage PV error in another)
2. **Same root cause = one entry** — when multiple scenarios fail with the same root cause, produce ONE entry. Do NOT split them into separate entries.
3. **At most 5 entries per job** — if more than 5 independent failures exist, report the 5 most severe
4. **Cascading failures are NOT independent** — when one failure causes others (e.g., a setup failure causing all subsequent tests to fail), report only the root failure
5. **Single failures are still an array** — even when there is only one failure, wrap it in a JSON array
