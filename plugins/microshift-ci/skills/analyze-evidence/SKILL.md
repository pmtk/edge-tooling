---
name: microshift-ci:analyze-evidence
argument-hint: <evidence-pack.json>
description: Analyze a Prow job from a pre-extracted evidence pack and produce a structured error report
user-invocable: true
allowed-tools: Bash, Read, Write, Glob, Grep
---

# microshift-ci:analyze-evidence

Analyzes a Prow CI job using a pre-extracted evidence pack. The agent has full access to raw artifacts via `artifacts_dir` for deeper investigation. For standalone analysis from a URL or local directory, use `/microshift-ci:prow-job`.

## Workflow

The user argument is: `<ARGUMENTS>`

1. **Read the evidence pack** at `<ARGUMENTS>` and `references/microshift-ci-primer.md`.

2. **Assess the failure**:
   - `infrastructure_indicators.is_infra_failure` true → confirm from matched patterns and anchor error, produce report.
   - `scenario-e2e` → examine each scenario's alerts, failures, and journal. Use `failure_timeline` to distinguish cascade from independent failures.
   - `conformance` → examine `conformance_failures`.
   - `build`/`config`/`rebase` → examine `build_errors`.
   - No `failed_step` and no error indicators → job passed. Severity 1, `infrastructure_failure: false`. Do NOT drill down.

3. **Drill down** — iterate hypothesis → evidence until the cause is actionable:

   **Mandatory raw-log verification** — BEFORE concluding, even when the evidence pack looks sufficient:
   - Read ~200 lines of raw journal around the failure timestamp — look for patterns NOT in the evidence pack (authorization denials, scheduler errors, admission failures, kubelet sandbox errors).
   - When a sosreport exists, check **kube-apiserver** pod logs for authorization/admission/scheduling decisions.
   - "Timed out waiting for X" is a symptom — read raw logs to find WHY X was slow or absent.

   **Deeper investigation** via raw artifacts:
   - **Sosreport pod logs**: read from `extracted_sosreport_dirs` when available, or run `bash plugins/microshift-ci/scripts/extract-sosreport.sh <tarball>` on paths in `sosreport_paths`.
   - **PCP graphs**: read PNGs listed in `pcp_graphs` when the failure involves timeouts, slowness, or resource exhaustion.
   - **Source code**: use `source_checkout.path` to read `test/suites/` or product code. Check `recent_commits` for related changes.

   **Critical rules**:
   - A test-layer fix is never the bottom when a product component misbehaved — reconstruct the component's story from journal and pod logs before concluding.
   - Two `Created container` events for the same pod = the first instance died. Read `previous.log` for the exit reason.
   - Multiple scenario failures: decide cascade vs independent using the **timeline**, not error-text similarity.
   - **Every causal-chain link MUST cite an artifact file path** (e.g., `artifacts/.../boot_and_run.log:4629`). Do NOT cite the evidence JSON, general knowledge, or architectural statements. The evidence pack includes `file` and `line` for each match — trace back to those. Drop unsupported links or record as analysis gaps.

4. **Produce the report** per `references/structured-summary.md`. Include both human-readable analysis and the `--- STRUCTURED SUMMARY ---` JSON block.

   When you read a raw artifact and find evidence NOT in the evidence pack, include `missing_patterns` entries: `{"file_type": "journal|boot_and_run|build_log", "grep_pattern": "<regex>", "reason": "<why>"}`.
