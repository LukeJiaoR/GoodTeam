#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_BIN="${PYTHON}"
elif [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
  PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "error: no python interpreter found; set PYTHON or create .venv" >&2
  exit 1
fi

if [[ ! -d "${REPO_ROOT}/src/goodteam_os" ]]; then
  echo "error: expected GoodTeam repo root at ${REPO_ROOT}" >&2
  exit 1
fi

export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
ADAPTER="${GOODTEAM_DOGFOOD_ADAPTER:-noop}"

exec "${PYTHON_BIN}" -m goodteam_os.execution.dogfood \
  --project-root "${REPO_ROOT}" \
  --adapter "${ADAPTER}"
