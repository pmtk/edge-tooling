---
name: microshift-ci:analyze-evidence
argument-hint: <evidence-pack.json>
description: Analyze a Prow job from a pre-extracted evidence pack and produce a structured error report
user-invocable: true
allowed-tools: Bash, Read, Write, Glob, Grep
---

# microshift-ci:analyze-evidence

## Synopsis

```bash
/microshift-ci:analyze-evidence <evidence-pack.json>
```

## Description

Analyzes a single Prow CI test job using a pre-extracted evidence pack (produced by `extract-evidence.py` or `doctor.sh evidence`). The evidence pack contains structured data from deterministic log/artifact parsing — failed step, junit failures, journal alerts, infrastructure indicators, etc. This skill starts at root cause reasoning, skipping the exploratory log scanning that `/microshift-ci:prow-job` does.

The agent has full access to raw artifacts via the `artifacts_dir` path in the evidence JSON for deeper investigation when the evidence pack is insufficient.

For standalone analysis of a URL or artifacts directory, use `/microshift-ci:prow-job` instead.

## Arguments

- `<ARGUMENTS>` (required): Path to an evidence pack JSON file (e.g., `<WORKDIR>/evidence/evidence-<BUILD_ID>.json`)

## Goal

Reduce noise for developers by producing a verified root cause analysis from pre-extracted evidence. A report is acceptable when:

- The failing step and (for test failures) the failing test/scenario are named
- The causal chain bottoms out in an actionable cause — or in an explicitly recorded evidence gap
- Every causal-chain link cites evidence from the artifacts (file path and line where applicable)
- The analysis determines whether the **product** or the **test** is at fault

## Audience

Software Engineer

## Workflow

The user argument is: `<ARGUMENTS>`

1. **Read the evidence pack** at `<ARGUMENTS>`. It contains:

   - **`job_name`**, **`job_url`**, **`release`**, **`finished`** — job metadata
   - **`artifacts_dir`** — path to raw artifacts for deeper investigation
   - **`job_type`** — `scenario-e2e`, `conformance`, `build`, `config`, `rebase`, or `other`
   - **`failed_step`** — the step that failed, its exit code, anchor error with context lines, ci-operator reason
   - **`infrastructure_indicators`** — whether this is an infra failure (scheduling, AWS, etc.) with matched patterns
   - **`scenarios`** — for each scenario: junit failures, RF failures, boot_and_run alerts, journal alerts (OOM, panics, container restarts, etcd pressure, OVN binding, probe failures, etc.), sosreport paths, extracted sosreport directories, analysis gaps
   - **`conformance_failures`** — for conformance jobs: failing test names and messages
   - **`build_errors`** — for build/config jobs: error lines with context
   - **`pcp_graphs`** — available PCP graph filenames in `<WORKDIR>/graphs/<BUILD_ID>/`
   - **`source_checkout`** — path to source checkout and recent commits
   - **`analysis_gaps`** — what evidence is missing at the job level

2. **Read the CI primer** at `references/microshift-ci-primer.md` for artifact layout, scenario naming conventions, test framework details, and common failure patterns.

3. **Assess the failure** from the structured evidence:

   - If `infrastructure_indicators.is_infra_failure` is true: confirm from the matched patterns and anchor error, then produce the report.
   - If `job_type` is `scenario-e2e`: examine each scenario's `journal_alerts`, `boot_and_run_alerts`, `rf_failures`, and `test_failures`. Identify which scenarios failed independently vs. cascaded. Use the timeline (which failed first).
   - If `job_type` is `conformance`: examine `conformance_failures` for the specific test failure.
   - If `job_type` is `build`/`config`/`rebase`: examine `build_errors` for the specific error with context.
   - If the evidence pack shows no `failed_step` (null or empty) and no error indicators across all categories, the job passed. Produce a minimal report noting "job completed successfully" with severity 1 and `infrastructure_failure: false`. Do NOT drill down.

4. **Drill down** — iterate hypothesis → evidence until the cause is actionable:

   Start from the evidence pack's alerts and errors. For each hypothesis:
   - Check if the evidence pack already contains confirming/refuting data
   - **Mandatory raw-log verification** — do this BEFORE concluding your root cause, even when the evidence pack looks sufficient:
     a. Read ~200 lines of raw journal around the failure timestamp (from `failed_step.anchor_error.context` or the earliest alert timestamp) — look for error patterns NOT in the evidence pack's `journal_alerts` categories (authorization denials, scheduler errors, apiserver errors, admission failures, kubelet sandbox errors)
     b. When the scenario has a sosreport (`sosreport_paths` or `extracted_sosreport_dirs`), check the **kube-apiserver** pod logs — kube-apiserver captures authorization, admission, and scheduling decisions that journal patterns may not extract
     c. If the evidence shows "timed out waiting for X", read raw logs to find WHY X was slow or absent — the timeout is a symptom, not a root cause
   - For deeper investigation, read raw artifact files:
     - **Sosreport pod logs**: when `extracted_sosreport_dirs` lists pre-extracted directories, read the pod logs directly. When not pre-extracted, run `bash plugins/microshift-ci/scripts/extract-sosreport.sh <tarball>` on the tarball paths listed in `sosreport_paths`
     - **PCP graphs**: Read the PNG files listed in `pcp_graphs` (in `<WORKDIR>/graphs/<BUILD_ID>/`) when the failure involves timeouts, slowness, or resource exhaustion
     - **Source code**: use `source_checkout.path` to read test suites (`test/suites/`) or product code to distinguish test vs product bug. Check `recent_commits` for related changes
     - **Raw log files**: read specific sections of build-log.txt, rf-debug.log, or journal_*.log when the extracted alerts point to something that needs more context
   - **A test-layer fix is never the bottom when a product component misbehaved.** When the failure involves a product component that was unavailable, crashed, or slow, reconstruct that component's story from the journal and pod logs before concluding.
   - Always check for container restarts — two `Created container` events for the same pod means the first instance died. Read `previous.log` for the exit reason.
   - Treat restating errors as symptoms: "timed out waiting for X" is NOT a root cause — explain why X was slow or absent.
   - If multiple scenarios failed, decide cascade vs independent using the **timeline**, not error-text similarity.
   - Record every accepted hop as a causal-chain link with its evidence file and line.
   - **Every causal-chain link MUST cite an artifact file path with line number** (e.g., `artifacts/.../boot_and_run.log:4629`). Do NOT use the evidence JSON itself as a citation — trace each alert back to the raw artifact file it came from (the evidence pack includes `file` and `line` for each match). Do NOT cite "architectural design", general knowledge, or anything that is not a file in the artifacts. If you cannot find an artifact file to support a causal-chain link, drop that link or record it as an analysis gap.

5. **Produce the report**: Read `references/structured-summary.md` for the complete output format. The report must include both a human-readable analysis and the `--- STRUCTURED SUMMARY ---` JSON block.

   **Feedback loop:** When you read a raw artifact file and find evidence that was NOT in the evidence pack, include a `missing_patterns` entry in the STRUCTURED SUMMARY:

   ```json
   "missing_patterns": [
     {"file_type": "journal", "grep_pattern": "connection refused.*apiserver", "reason": "needed to trace apiserver unavailability"}
   ]
   ```

   This tells maintainers what patterns to add to `extract-evidence.py`.

## Output Format

Read `references/structured-summary.md` for the complete output format specification including the STRUCTURED SUMMARY JSON schema, field descriptions, severity rubric, confidence rules, RAW_ERROR rules, ROOT_CAUSE rules, and multiple-failure handling.

## Prerequisites

- Python 3 (for sosreport extraction if needed)
- Bash shell

## Notes

- This skill does NOT download artifacts — it expects an evidence pack with `artifacts_dir` pointing to pre-downloaded artifacts
- The evidence pack is a head-start, not a cage — always follow leads into raw artifacts when the structured data is insufficient
- `missing_patterns` feedback is how the extraction script improves over time — include it whenever you read a raw file to find evidence not in the pack
- For standalone analysis from a URL or artifacts directory, use `/microshift-ci:prow-job`
