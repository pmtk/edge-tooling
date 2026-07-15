# MicroShift CI Artifact Primer

Reference for analyzing MicroShift Prow job artifacts. Read this when
unfamiliar with the artifact layout — it answers "which file answers
which question".

## CI structure terms

- **step**: Smallest CI test infrastructure component — a single command/script with its environment. Also called "ref". Each step logs to its own `build-log.txt`.
- **scenario**: A Robot Framework suite together with its test environment, MicroShift deployment, and VM. Includes the deployment method: rpm-ostree, rpm, or bootc container.

## Job types

- **Scenario-based e2e jobs** (`e2e-aws-tests-*`): the
  `openshift-microshift-e2e-metal-tests` step boots ~20 VM-based test
  scenarios on a hypervisor host. Failures are per-scenario; "the job
  failed" usually means at least 1 scenario failed.
- **Direct-test jobs** (`*-ocp-conformance-*`, `e2e-aws-ai-model-serving-nightly`,
  `e2e-aws-footprint-and-performance-*`): run their test suite directly,
  no scenario fan-out. Job history IS the test history for these.

## Test framework

Tests use Robot Framework — suites in `test/suites/` as `.robot` files,
failures marked with `| FAIL |` in `rf-debug.log`. Test execution order
is randomized by default, so ordering in logs varies between runs.

## Deployment types

Three deployment pipelines: ostree (`test/scenarios/`), bootc
(`test/scenarios-bootc/`), and RPM (`test/suites/rpm/`). The job name
indicates which (`e2e-aws-tests-bootc-*` = bootc, `e2e-aws-tests-*` =
ostree). All three produce the same artifact layout under `scenario-info/`.

## Scenario naming

Scenario names encode OS image, MicroShift version source, and suite.
The `@` separator chains stages left-to-right: starting image →
intermediate upgrades → final image → test suite.

### Version-source markers

- `src` — built from source (the code in the PR or branch)
- `base` — built from the PR's target branch
- `prel` — previous minor release (Y-1 as a released build)
- `crel` — current minor release (already-released RPMs: EC, RC, or
  z-stream); skipped shortly after branch cut before the first EC
- `lrel` — latest available release (EC, RC, or z-stream) from internal
  Red Hat staging repositories
- `zprel` — z-previous release: latest z-stream from the rhocp repository
- `y1` / `y2` — Y-1 / Y-2 minor versions back (e.g. on release-4.22,
  `y1` = 4.21, `y2` = 4.20); also spelled `yminus1` / `yminus2` in
  some scenario filenames

### OS version tokens

- `el96` / `el98` / `el102` — RHEL 9.6 / 9.8 / 10.2

### Reading multi-@ names

| Name | Meaning |
| ---- | ------- |
| `el96-lrel@standard1` | RHEL 9.6 + latest release of MicroShift, standard suite 1 |
| `el94-y2@el96-lrel@standard1` | Start on RHEL 9.4 + Y-2 MicroShift, upgrade to RHEL 9.6 + latest release of MicroShift, run standard suite 1 |
| `el96-yminus2@prel@src@delta-upgrade-ok` | Start on RHEL 9.6 + Y-2, upgrade through Y-1 (prel) to source, using static deltas |

The last `@`-segment is always the test suite or test type.

Scenario definitions live in `test/scenarios*/` (e.g.
`test/scenarios-bootc/el9/`); Robot Framework suites under
`test/suites/`.

## How scenarios run in CI

All scenarios run in parallel on a single hypervisor. Each executes
two phases:

1. **create** — boot VMs, wait for greenboot health check.
   Infrastructure junit: `phase_create/junit.xml`.
2. **run** — execute Robot Framework tests.
   Infrastructure junit: `phase_run/junit.xml`.

Resource contention (CPU, disk I/O, memory) from parallel scenarios
can cause timeouts that don't reproduce in isolation — attribute these
to shared-hypervisor contention, not product/test bugs.

## Where the evidence lives

**Job-level files** (under `<ARTIFACTS_DIR>/`):

| File | Answers |
| ---- | ------- |
| `build-log.txt` | Prow job output — AWS infra and hypervisor errors surface here |
| `<STEP>/build-log.txt` | Per-step log — the primary log for step-level failures |
| `artifacts/<TEST>/openshift-microshift-e2e-origin-conformance/build-log.txt` | Origin conformance test output |

**Per scenario** (under
`artifacts/<TEST_NAME>/openshift-microshift-e2e-metal-tests/artifacts/scenario-info/<scenario>/`):

| File | Answers |
| ---- | ------- |
| `junit.xml` | Which tests failed; the top-level `testsuite name` IS the scenario name |
| `rf-debug.log` | Robot Framework execution trace with timestamps — failures marked `\| FAIL \|`; the primary test-failure evidence |
| `boot_and_run.log` | VM boot + scenario orchestration; timeouts killing the whole scenario show up here (`timeout: sending signal TERM`) |
| `phase_create/junit.xml` | Infrastructure-level junit from VM creation (greenboot check, kickstart, SOS collection) — distinct from the test-level `junit.xml` |
| `phase_run/junit.xml` | Infrastructure-level junit from the test run phase |
| `vms/host1/sos/journal_*.log` | **Plain-text journal exports** — readable without extracting anything; check these FIRST for service failures, x509 errors, OOM kills |
| `vms/host1/sos/sosreport-*.tar.xz` | Full sosreports (see below) |

## Sosreports

- Two types of sosreport are collected:
  1. **On-failure** — the `sos-on-failure-listener.py` Robot Framework
     listener captures a sosreport at each test-case-level keyword
     failure. This report includes the namespaces that the test created
     (detected by tracking Robot variables containing "namespace" or
     "ns"). **Prefer this report**: by the end of the scenario, test
     namespaces are cleaned up and their pod logs are gone.
  2. **End-of-scenario** — collected during teardown regardless of
     pass/fail. Contains system state but may lack test-created
     workloads.
  Match report to failure by comparing the sosreport's capture timestamp
  with the failure timestamp from `rf-debug.log`.
- **Journals:** use the plain-text `journal_*.log` files next to the
  sosreport tarballs — no extraction needed.
- **Pod logs:** extract a specific tarball with
  `bash plugins/shared/scripts/extract-sosreport.sh <tarball>`.
  This extracts pod logs, inspect outputs, and cluster-scoped
  resources (not journals or the full filesystem) into
  `<tarball-parent>/sos-extracted/<sosreport-name>/`.
- Inside an extracted report:
  - Per-namespace pod logs:
    `sos_commands/microshift/namespaces/<ns>/pods/<pod>/<container>/<container>/logs/current.log`
    — and `previous.log` when the container was restarted. **The tail of
    `previous.log` states why the container died** (fatal error, leader
    election lost, panic).
  - Cluster-scoped resources:
    `sos_commands/microshift/cluster-scoped-resources/` — nodes, CRDs,
    webhooks.
  - Component inspect outputs: `sos_commands/*/inspect_*`.

## Greenboot health check

Before running tests, the scenario runner waits for
`greenboot-healthcheck.service` to reach `exited` state. This verifies
MicroShift started successfully. If greenboot fails or times out
(`VM_GREENBOOT_TIMEOUT`), the scenario aborts with
`pre_test_greenboot_check FAILED` in `phase_create/junit.xml` and no
tests run.

In the journal, look for `40_microshift_running_check.sh` lines —
they show which deployments greenboot waited for and when each became
ready. The final verdict is
`greenboot[...]: Script '40_microshift_running_check.sh' SUCCESS/FAILURE`.

## Reading the journal for component failures

Reconstruct a timestamped component timeline before attributing fault:

- Pod lifecycle: kubelet `SyncLoop (PLEG)` events, `Created container` /
  `Started container` (crio), `SyncLoop (probe)` readiness transitions,
  `prober.go "Probe failed"` lines.
- **Two `Created container` events for the same pod = the first instance
  died and was restarted** — a single startup narrative is wrong; read
  `previous.log` for the exit reason.
- etcd pressure: `apply request took too long` warnings indicate
  apiserver/etcd latency (can cost components their leader-election
  leases).

## Common failure patterns

### Timeout cascade

When `TEST_EXECUTION_TIMEOUT` (default 30m) expires, the `timeout`
command sends TERM to Robot Framework. The current test dies with
`Execution terminated by signal` and every subsequent test reports
`Test execution stopped due to a fatal error`. This is a cascade
with ONE root cause — identify what consumed the time budget.

### Greenboot failure masking test failures

If greenboot fails, no tests run — the only junit is the
infrastructure-level `phase_create/junit.xml` recording the
`pre_test_greenboot_check FAILED`. The root cause is in the journal
(MicroShift didn't start, a deployment didn't become ready, etc.).

### Resource contention from parallel scenarios

All scenarios share a single hypervisor. When many scenarios boot
simultaneously, CPU/memory/disk contention can cause:

- Slow MicroShift startup → greenboot timeouts
- etcd `apply request took too long` → leader election loss
- Image pull timeouts

Report these as infrastructure failures attributed to
shared-hypervisor contention.

## Search/index coverage of external tools

- Sippy tracks these jobs at **job level only** — scenario junits and RF
  suite names are not ingested.
- Search.CI indexes build logs and junit, **not** scenario-internal logs
  (`rf-debug.log` content is not searchable).
