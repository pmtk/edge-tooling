# microshift-ci

Analyze MicroShift CI failures, produce HTML reports, and draft JIRA bug suggestions.

## Installation

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install microshift-ci
```

## CI Doctor Pipeline

The full CI doctor pipeline is driven by `run-doctor.py` — a deterministic Python
script that orchestrates all stages (prepare, graphs, analyze, bugs, history,
finalize). LLM agents perform per-job root cause analysis and bounded historical
causal comparison; acquisition, counting, timelines, and rendering are deterministic.

```bash
python3 plugins/microshift-ci/scripts/run-doctor.py \
    --releases 4.19,4.20,4.21,4.22 --workdir /tmp/workdir

# Include pull request analysis
python3 plugins/microshift-ci/scripts/run-doctor.py \
    --releases 4.19,4.20,4.21,4.22 --workdir /tmp/workdir \
    --pull-requests --repo openshift/microshift

# Run specific stages only
python3 plugins/microshift-ci/scripts/run-doctor.py \
    --releases 4.22 --workdir /tmp/workdir --stages analyze,finalize
```

### Two-week failure history

The `history` stage fetches previous Doctor reports from public GCS without a
browser. New reports publish `report-microshift-ci-doctor.json` alongside HTML;
older reports are read from their embedded JSON without executing JavaScript.
Each issue shows a compact list of possible matches: semantic score, age,
distinct execution count, affected releases, and a source report link. Historical
search spans all releases in the component, even when only one current release
is being investigated. Scores are not calibrated probabilities.

The summary distinguishes intermittent evidence from persistent failures in
observed runs. Full per-job timelines, onset evidence, comparison explanations,
and coverage diagnostics remain in `history/results.json`, not the HTML.
Repeated sightings of one execution do not count as recurrences; uncertain
matches do not increase the accepted count. “First observed” is not an
introduction date, and intermittency does not establish a harmless flake.

Controls on `run-doctor.py`:

- `--history-days 14`: lookback, independent of predecessor RCA reuse.
- `--history-as-of <ISO_UTC>`: replay cutoff; otherwise use the Doctor start time.
- `--history-max-budget-usd 2.0`: comparison spending guard for the workdir,
  including retries/resume. The CLI enforces the remaining budget; costs are
  measured after requests, so a final request can cross the limit. Unknown call
  costs stop further model calls.
- `--no-history`: disable history and suppress previously saved results.

Comparisons use compact causal-chain cards and at most ten candidates per issue.
Candidate selection balances releases and release-neutral job families; initial
comparison opportunities are shared across current issues before optional
semantic shortlisting. Prompts/outputs are bounded and decisions are cached. Candidate,
evidence, report, and budget gaps produce a concise partial-history notice in
HTML; detailed diagnostics remain in JSON. Cache and usage files live directly
under `history/` so the CI artifact uploader preserves them.
`history/results.json` is the saved assessment; `report-source.json` preserves
source and collection provenance.
HTML refresh uses saved history without network or model calls for history.

For a model-free acquisition check:

```bash
python3 plugins/microshift-ci/scripts/doctor_history.py \
    --component microshift --workdir /tmp/workdir --releases 4.22 \
    --days 14 --fetch-only
```

Live semantic comparison is CI-only. Test it in the Doctor CI job using an image
containing the branch's changes; the deployed step uses the published
`ci/edge-tooling-ai-helpers:latest` image, so rehearsing unchanged configuration
does not automatically test a local branch. CI configuration lives in
`openshift/release`; use an affected-job rehearsal there with the candidate image
and inspect `history/results.json`, `history/cost.json`, and the HTML artifact.
See [Prow rehearsal instructions](https://docs.ci.openshift.org/how-tos/contributing-openshift-release/#rehearsals).
No local Claude invocation is needed to run the deterministic tests:

```bash
python3 -m unittest discover -s plugins/shared/scripts/tests -p '*history*.py'
```

For a standalone historical job investigation, pass its actual finish timestamp
with `--as-of`; saved `history/context.json` keeps subsequent offline replays
anchored to the same observation window.

## Skills

| Skill | Description |
|---|---|
| `/microshift-ci:prow-job` | Root cause analysis of a single Prow job |
| `/microshift-ci:test-job` | Comprehensive job metadata and scenario results |
| `/microshift-ci:test-scenario` | Analyze individual test scenario results |
| `/microshift-ci:close-stale-bugs` | Close stale, unlinked, unassigned AI-generated bugs (dry-run by default) |
| `/microshift-ci:continue-session` | Download CI Doctor artifacts from a completed prow job |
| `/microshift-ci:fix-test-bugs` | Attempt to fix CI bugs by opening PRs in openshift/microshift (dry-run by default) |

## Usage

### Full pipeline

```bash
python3 plugins/microshift-ci/scripts/run-doctor.py \
    --releases 4.19,4.20,4.21,4.22 --workdir /tmp/workdir
```

### Single job analysis

```text
/microshift-ci:prow-job https://prow.ci.openshift.org/view/gs/test-platform-results/logs/<job-name>/<job-id>
```

Searches JIRA for pre-existing bugs matching CI failures and drafts ticket
suggestions for unmatched ones. The suggest/linked/skip decisions are written
to `<WORKDIR>/bugs/bug-search-<release>.txt` when one release is analyzed, or
to `<WORKDIR>/report-bug-search.txt` when several releases are analyzed. Bugs
are filed manually via the prefilled "Create Bug in JIRA" buttons in the HTML
report. Every failure has a button, even when bugs were matched or the failure
was categorized as `linked` or `skip`, so another bug can still be filed;
those failures also show their decision. A closed bug is listed only when its
summary or description names the failure's distinctive terms (version numbers
do not count); it is shown as possibly related, with the matched terms, and the
failure stays `suggest` because Jira cannot show whether the fix was in the
tested build. Closed bugs read from bug-matches files written before this check
(for example, artifacts from an earlier run) were not context-verified; they
show "match evidence unavailable" and must not be treated as verified matches.

## Requirements

- `gsutil` CLI (uses anonymous access on public GCS buckets)
- `gh` CLI (authenticated with access to openshift/microshift)
- `JIRA_USERNAME` and `JIRA_API_TOKEN` environment variables (Jira REST API credentials for bug correlation)
- Jira MCP server configured (only for `/microshift-ci:close-stale-bugs` `--close` mode)
- Python 3
- `pcp-export-pcp2json` (for PCP performance graphs)
- `matplotlib` Python package (for PCP graph plotting)
- **Category:** ci-cd

## Author

ggiguash
