# Structured Summary Output Format

Shared output contract for CI job analysis skills. Both `prow-job` (manual)
and `prow-job-evidence` (automated) produce this identical format, consumed
by `aggregate.py`, `search-bugs.py`, and `create-report.py`.

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

After the human-readable report above, append a machine-readable JSON block for downstream automation. This block MUST appear at the very end of the report, after all prose and analysis. The block is a JSON array with one object per failure.

**CRITICAL:** You MUST include BOTH the opening `--- STRUCTURED SUMMARY ---` marker AND the closing `--- END STRUCTURED SUMMARY ---` marker. The parser will skip your entire report if either marker is missing.

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
    "scenarios": ["el96-lrel@standard1", "el94-y2@el96-lrel@standard1"],
    "missing_patterns": []
  }
]
--- END STRUCTURED SUMMARY ---
```

## Field Descriptions

- `severity`: 1-5, same as Error Severity above
- `stack_layer`: one of: AWS Infra, External Infrastructure, build phase, deploy phase, test setup phase, Test Configuration, test, teardown
- `step_name`: the CI step where the error occurred
- `error_signature`: a concise, unique one-line description of the root cause — not the full error, just enough to identify and deduplicate this failure
- `root_cause`: one-line description of WHY the failure happened — the underlying mechanism, not the surface symptom (~80 chars max, see rules below)
- `raw_error`: the primary error message copied VERBATIM from the log file (see rules below)
- `infrastructure_failure`: true if stack_layer is AWS Infra or the failure is due to CI infrastructure rather than product code, false otherwise
- `job_url`: the full prow job URL
- `job_name`: the full job name
- `release`: the release branch (e.g. 4.22, main)
- `remediation`: suggested fix or next step (~120 chars max). For infrastructure failures, state the infra action (e.g. "retry the job"). For product bugs, state the code-level fix direction. Do NOT propose making the test more tolerant (waits, retries, longer timeouts) unless the causal chain shows the product behaved correctly
- `finished`: the job finish date in YYYY-MM-DD format
- `causal_chain`: array of links from the observed symptom toward the root cause. Each link is `{"cause": ..., "evidence": ..., "quote": ...}` where `evidence` is the artifact file path (relative to the artifacts dir, with `:line` where applicable) and `quote` is a short verbatim excerpt. The evidence path MUST be a file that actually exists. **Before finalizing the report, re-read every cited `file:line` and confirm the quote is actually there.** A single-link chain is valid when the anchor error IS the actionable cause
- `confidence`: one of `high`, `medium`, `low` (see CONFIDENCE rules below)
- `analysis_gaps`: array of strings naming evidence that was missing or could not be checked (e.g. `"no sosreport in artifacts"`, `"source checkout not available"`). Empty array when nothing was skipped
- `scenarios`: array of scenario names in which this failure occurred. Empty array `[]` for non-scenario-based jobs and for build/infra failures that happen before scenarios run
- `missing_patterns`: (optional) array of patterns that should be added to `extract-evidence.py`. Each entry is `{"file_type": "journal|boot_and_run|build_log", "grep_pattern": "<regex>", "reason": "<why this pattern was needed>"}`. Include when you read a raw artifact file and found evidence NOT in the evidence pack. Empty array or omitted when evidence was sufficient.

## CONFIDENCE Rules

- `high`: every causal-chain link, including the final (root) one, is directly evidenced by a quoted artifact line or graph
- `medium`: the mechanism is inferred but consistent with all available evidence; no link is contradicted. Every causal-chain link MUST still cite an artifact file — `medium` means the *interpretation* is inferred, not that citations can be omitted
- `low`: the analysis is symptom-level only — the chain stops before an actionable cause because the evidence ran out (`analysis_gaps` MUST be populated in this case)

Do NOT inflate confidence: downstream automation uses it to decide whether to act on the analysis. A `low` confidence report with honest gaps is more useful than a `high` confidence guess.

## RAW_ERROR Rules

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

## ROOT_CAUSE Rules

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
| MonitorTest failures (SCC annotations, disruption pollers) on ARM64 | OCP MonitorTest framework expects multi-node SCC annotations absent in MicroShift |
| Pod-network-disruption monitor poller CrashLoopBackOff on ARM64 | disruption monitor poller requires multi-node cluster endpoints unavailable in single-node |
| cert-manager not ready within greenboot 10m timeout on ARM | greenboot health check timeout during slow ARM service deployment |
| InvalidClientTokenId when calling CreateStack | expired or invalid AWS credentials in CI environment |

Note: ROOT_CAUSE describes the specific mechanism observed in the artifacts, not architectural generalizations. Do not use phrases like "MicroShift is single-node" as a root cause — instead describe what specifically went wrong (e.g., "framework expects annotation X which MicroShift does not set").

## Multiple Independent Failures

When a job has multiple independent test failures across different scenarios, produce **one entry per failure** in the JSON array. Each entry must be self-contained with all fields populated.

**Rules:**

1. **One entry per independent failure** — failures are independent when they occur in different test scenarios with different root causes (e.g., cert-manager timeout in one scenario and storage PV error in another)
2. **Same root cause = one entry** — when multiple scenarios fail with the same root cause, produce ONE entry. Do NOT split them into separate entries.
3. **At most 5 entries per job** — if more than 5 independent failures exist, report the 5 most severe
4. **Cascading failures are NOT independent** — when one failure causes others (e.g., a setup failure causing all subsequent tests to fail), report only the root failure
5. **Single failures are still an array** — even when there is only one failure, wrap it in a JSON array
