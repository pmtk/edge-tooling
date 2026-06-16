#!/usr/bin/bash
set -euo pipefail

# Extract sosreport archives from downloaded Prow job artifacts and write
# a JSON index of the high-signal files inside them.
#
# Shared across components (MicroShift, LVMS, etc.) via symlinks in each
# plugin's scripts/ directory. Intended for analysis agents that cannot
# run tar directly (CI permission allowlist) — this script is the one
# permitted mechanism for sosreport extraction.
#
# Usage:
#   extract-sosreport.sh <artifacts-dir> [--dest DIR]
#
#   <artifacts-dir>: local job artifacts directory (searched recursively
#                    for sosreport-*.tar.xz)
#   --dest DIR:      extraction destination (default: <artifacts-dir>/sos-extracted)
#
# Output: writes <dest>/index.json:
#   {"sosreports": [{
#      "archive": "<tarball path>",
#      "extracted_to": "<dir>",
#      "journals": ["..."],
#      "namespace_pod_logs": "<dir or empty string>",
#      "highlights": [{"file": "...", "line": N, "text": "..."}]
#   }]}
#
# Extraction is idempotent: a .extracted marker file in the destination
# directory causes the (slow) tar step to be skipped on re-runs.
#
# Progress/errors: stderr. Absence of sosreports is NOT an error — the
# caller records it as an analysis gap.

HIGHLIGHT_RE='panic|OOM|oom-kill|segfault|Failed to start|level=error|FATAL|leader election lost'
MAX_HIGHLIGHTS=100

usage() {
    echo "Usage: $(basename "$0") <artifacts-dir> [--dest DIR]" >&2
    exit 1
}

main() {
    local artifacts_dir=""
    local dest=""

    while [[ ${#} -gt 0 ]]; do
        case "${1}" in
            --dest)
                [[ ${#} -ge 2 ]] || { echo "Error: --dest requires a directory" >&2; usage; }
                dest="${2}"; shift 2 ;;
            -h|--help) usage ;;
            -*) echo "Unknown option: ${1}" >&2; usage ;;
            *) artifacts_dir="${1}"; shift ;;
        esac
    done

    [[ -z "${artifacts_dir}" ]] && usage
    [[ -d "${artifacts_dir}" ]] || { echo "Error: not a directory: ${artifacts_dir}" >&2; exit 1; }
    dest="${dest:-${artifacts_dir}/sos-extracted}"

    local -a tarballs=()
    while IFS= read -r t; do
        tarballs+=("${t}")
    done < <(find "${artifacts_dir}" -name 'sosreport-*.tar.xz' | sort)

    if [[ ${#tarballs[@]} -eq 0 ]]; then
        echo "No sosreports found in ${artifacts_dir}" >&2
        mkdir -p "${dest}"
        echo '{"sosreports": [], "note": "no sosreport found"}' > "${dest}/index.json"
        return 0
    fi

    echo "Found ${#tarballs[@]} sosreport(s)" >&2

    local result='{"sosreports": []}'
    local tarball
    for tarball in "${tarballs[@]}"; do
        local name
        name="$(basename "${tarball}" .tar.xz)"
        local outdir="${dest}/${name}"

        if [[ -f "${outdir}/.extracted" ]]; then
            echo "  cached: ${name}" >&2
        else
            echo "  extracting: ${name}" >&2
            mkdir -p "${outdir}"
            # Sosreport tarballs contain a single top-level directory named
            # after the archive — strip it so files land directly in outdir.
            tar --no-same-owner --strip-components=1 -xf "${tarball}" -C "${outdir}"
            touch "${outdir}/.extracted"
        fi

        # Index high-signal locations. Journal command output lands under
        # per-plugin dirs (sos_commands/logs/, sos_commands/microshift/, ...).
        local journals_json
        journals_json=$(find "${outdir}" \
            \( -path '*/sos_commands/*journalctl*' -o -path '*/var/log/journal/*' \) \
            -type f 2>/dev/null | sort | jq -R . | jq -s .)

        local ns_logs
        ns_logs=$(find "${outdir}" -type d -path '*/sos_commands/*/namespaces' 2>/dev/null | head -1)

        # Pre-grep highlights across journals, component command output, and
        # dead-container logs (previous.log explains why a container exited).
        local -a scan_targets=()
        while IFS= read -r f; do
            scan_targets+=("${f}")
        done < <(find "${outdir}" \
            \( -path '*/sos_commands/*journalctl*' -o -path '*/sos_commands/*/inspect_*' \
               -o -path '*/namespaces/*/logs/previous.log' \) \
            -type f 2>/dev/null | sort)

        local highlights_json="[]"
        if [[ ${#scan_targets[@]} -gt 0 ]]; then
            # grep exits 1 on no matches and the journals may contain binary
            # data (-I skips it) — neither should fail the pipeline.
            highlights_json=$({ grep -nHIE "${HIGHLIGHT_RE}" "${scan_targets[@]}" 2>/dev/null || true; } \
                | head -${MAX_HIGHLIGHTS} \
                | jq -R 'capture("^(?<file>[^:]+):(?<line>[0-9]+):(?<text>.*)$")
                         | {file: .file, line: (.line | tonumber), text: (.text[0:200])}' \
                | jq -s .)
        fi

        result=$(echo "${result}" | jq \
            --arg archive "${tarball}" \
            --arg outdir "${outdir}" \
            --argjson journals "${journals_json}" \
            --arg nslogs "${ns_logs}" \
            --argjson highlights "${highlights_json}" \
            '.sosreports += [{
                archive: $archive,
                extracted_to: $outdir,
                journals: $journals,
                namespace_pod_logs: $nslogs,
                highlights: $highlights
            }]')
    done

    echo "${result}" > "${dest}/index.json"
    echo "Index written to ${dest}/index.json" >&2
}

main "${@}"
