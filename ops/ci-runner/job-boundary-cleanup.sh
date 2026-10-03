#!/usr/bin/env bash
set -euo pipefail

# Keep hooks outside the checkout, independent of success/failure/cancellation.
hook_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec timeout --signal=TERM --kill-after=5s 120s \
  /usr/bin/python3 "${hook_dir}/runner_cleanup.py" "${1:-started}"
