#!/usr/bin/env bash
set -euo pipefail

# actions/checkout owns repository cleanup. Remove only this runner's temporary
# children and expire dependency-cache files that have not been used recently.
if [[ -n "${RUNNER_TEMP:-}" && -d "$RUNNER_TEMP" ]]; then
  find "$RUNNER_TEMP" -mindepth 1 -maxdepth 1 -user "$(id -un)" -exec rm -rf -- {} +
fi

if [[ -d "${HOME}/.cache/pip" ]]; then
  find "${HOME}/.cache/pip" -type f -atime +30 -delete
fi
if [[ -d "${HOME}/.npm/_cacache" ]]; then
  find "${HOME}/.npm/_cacache" -type f -atime +30 -delete
fi
