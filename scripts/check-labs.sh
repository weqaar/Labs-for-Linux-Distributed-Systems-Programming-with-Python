#!/usr/bin/env bash
#
# Run the quality gates in every lab.
#
# This is what a build agent runs against the finished book. Its purpose is to
# catch the case where a change to an earlier lab breaks a later one, which is
# easy to do because the labs build on each other rather than standing alone.
#
# Every lab runs even after one fails. Stopping at the first failure would hide
# how much is broken, and the answer to "did my change break one lab or nine"
# decides what to do next.

set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
labs="${root}/labs"
junit_root="${LABS_JUNIT_DIR:-}"

if [[ ! -d "${labs}" ]]; then
    echo "No labs directory at ${labs}" >&2
    exit 2
fi

if ! command -v pybootstrap >/dev/null 2>&1; then
    echo "pybootstrap is not installed. See Chapter 1, or run:" >&2
    echo "    pip install 'pybootstrap[gates]'" >&2
    exit 2
fi

passed=()
failed=()
errored=()

for lab in "${labs}"/*/; do
    [[ -f "${lab}/pyproject.toml" ]] || continue
    name="$(basename "${lab}")"
    scaffold=0

    if grep -Rqs \
        "Placeholder so the project has something to import, test and type check" \
        "${lab}/src"; then
        echo "[FAIL ] product: generated greeting scaffold is still present"
        scaffold=1
    fi

    args=(check)
    if [[ -n "${junit_root}" ]]; then
        args+=(--junit-dir "${junit_root}/${name}")
    fi

    echo "=== ${name} ==="
    (cd "${lab}" && pybootstrap "${args[@]}")
    status=$?

    case "${status}" in
        0)
            if [[ ${scaffold} -eq 1 ]]; then
                failed+=("${name}")
            else
                passed+=("${name}")
            fi
            ;;
        1) failed+=("${name}") ;;
        # Anything else means a gate could not run rather than a lab being
        # wrong, and the two need different responses.
        *) errored+=("${name}") ;;
    esac
    echo
done

echo "=== summary ==="
echo "passed:  ${#passed[@]}"
echo "failed:  ${#failed[@]} ${failed[*]:-}"
echo "errored: ${#errored[@]} ${errored[*]:-}"

# The same precedence the gates themselves use: a lab whose tooling is broken
# is worse news than a lab whose code is wrong, because it reports nothing.
if [[ ${#errored[@]} -gt 0 ]]; then
    exit 2
elif [[ ${#failed[@]} -gt 0 ]]; then
    exit 1
fi
exit 0
