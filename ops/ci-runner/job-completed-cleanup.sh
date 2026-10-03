#!/usr/bin/env bash
set -euo pipefail
hook_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! bash "${hook_dir}/job-boundary-cleanup.sh" completed; then
  echo '::warning::CI completion cleanup timed out or failed; the next start will retry safe recovery.'
fi
