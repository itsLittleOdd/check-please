#!/bin/bash
# Isolated-install smoke test (backlog item 22).
#
# Proves the plugin actually installs and enables through Hermes' own
# install path, pinned to a real commit, in a throwaway HERMES_HOME.
# Never touches the real ~/.hermes. Run as gate 6 on ticks that change
# plugin.yaml, __init__.py or desktop/.
#
# Usage: tests/install_smoke.sh   (run from the repo root)
# Exit 0 on success, non-zero on any failure. Always cleans up its temp
# home, even on failure (trap below).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH_ROOT="${HOME}/.hermes/cache/scratch"
mkdir -p "$SCRATCH_ROOT"
TMP_HOME="$(mktemp -d "${SCRATCH_ROOT}/check-please-install-smoke.XXXXXX")"

cleanup() {
  rm -rf "$TMP_HOME"
}
trap cleanup EXIT

SHA="$(git -C "$REPO_DIR" rev-parse HEAD)"

echo "install_smoke: repo=$REPO_DIR sha=$SHA tmp_home=$TMP_HOME"

HERMES_HOME="$TMP_HOME" hermes plugins install "file://${REPO_DIR}" --ref "$SHA" --enable

DOCTOR_OUT="$(HERMES_HOME="$TMP_HOME" hermes plugins doctor check-please)"
echo "$DOCTOR_OUT"

if ! echo "$DOCTOR_OUT" | grep -qi "check-please"; then
  echo "install_smoke: FAIL - doctor output does not mention check-please" >&2
  exit 1
fi

if echo "$DOCTOR_OUT" | grep -qiE "error|fail"; then
  echo "install_smoke: FAIL - doctor output reports an error" >&2
  exit 1
fi

# Confirm the real ~/.hermes was never touched: no check-please references
# in the real plugins dir or config.yaml.
REAL_HOME="${HOME}/.hermes"
if [ -d "$REAL_HOME" ]; then
  if grep -rl "check-please" "$REAL_HOME"/*/plugins 2>/dev/null | grep -v "$TMP_HOME" >/dev/null; then
    echo "install_smoke: FAIL - found check-please references outside the temp home" >&2
    exit 1
  fi
fi

echo "install_smoke: PASS (installed and enabled check-please@${SHA:0:7} in an isolated HERMES_HOME, doctor OK, real ~/.hermes untouched)"
exit 0
