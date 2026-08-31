#!/usr/bin/env bash
set -euo pipefail

readonly REPOSITORY_URL="https://github.com/LukeJiaoR/GoodTeam"
readonly RUNNER_USER="goodteam-ci"
readonly RUNNER_NAME="goodteam-ci-01"
readonly RUNNER_LABEL="goodteam-ci"
readonly RUNNER_HOME="/data/${RUNNER_USER}"
readonly RUNNER_DIR="${RUNNER_HOME}/actions-runner"
readonly RUNNER_VERSION="2.337.0"
readonly RUNNER_ARCHIVE="actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
readonly RUNNER_SHA256="70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613"
readonly GOODTEAM_SERVICE="actions.runner.LukeJiaoR-GoodTeam.${RUNNER_NAME}.service"
readonly YUVA_SERVICE="actions.runner.LukeJiaoR-ziwei.yuva-ci-01.service"
readonly SHARED_SLICE="goodteam-shared-ci.slice"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root on the CI host." >&2
  exit 1
fi
if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "This provisioning script supports Linux x86_64 only." >&2
  exit 1
fi

source /etc/os-release
if [[ "${ID:-}" != "ubuntu" && "${ID:-}" != "debian" ]]; then
  echo "Supported distributions are Ubuntu 24.04 LTS and Debian 12." >&2
  exit 1
fi
if [[ ! -d /data ]] || [[ "$(findmnt -no FSTYPE /data)" != "ext4" ]]; then
  echo "A dedicated ext4 /data filesystem is required." >&2
  exit 1
fi
if [[ ! -f "${SCRIPT_DIR}/job-boundary-cleanup.sh" ]]; then
  echo "job-boundary-cleanup.sh must be beside this script." >&2
  exit 1
fi
if systemctl list-unit-files --type=service --no-legend | awk '{print $1}' | grep -Fxq "$GOODTEAM_SERVICE"; then
  echo "${GOODTEAM_SERVICE} is already installed; refusing to overwrite it." >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install --yes \
  build-essential ca-certificates curl git jq libicu-dev libssl-dev \
  python3 python3-venv

if ! id "$RUNNER_USER" >/dev/null 2>&1; then
  useradd --create-home --home-dir "$RUNNER_HOME" --shell /bin/bash "$RUNNER_USER"
fi
chmod 0750 "$RUNNER_HOME"
if id -nG "$RUNNER_USER" | tr ' ' '\n' | grep -Eq '^(docker|sudo)$'; then
  echo "Refusing to provision: ${RUNNER_USER} must not belong to docker or sudo." >&2
  exit 1
fi

install --directory --owner "$RUNNER_USER" --group "$RUNNER_USER" --mode 0750 "$RUNNER_DIR"
install --directory --owner "$RUNNER_USER" --group "$RUNNER_USER" --mode 0750 "${RUNNER_HOME}/hooks"
install --owner "$RUNNER_USER" --group "$RUNNER_USER" --mode 0750 \
  "${SCRIPT_DIR}/job-boundary-cleanup.sh" "${RUNNER_HOME}/hooks/job-boundary-cleanup.sh"

archive_path="$(mktemp)"
trap 'rm -f "$archive_path"' EXIT
curl --fail --silent --show-error --location \
  "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${RUNNER_ARCHIVE}" \
  --output "$archive_path"
echo "${RUNNER_SHA256}  ${archive_path}" | sha256sum --check --status
tar --extract --gzip --file "$archive_path" --directory "$RUNNER_DIR"
rm -f "$archive_path"
trap - EXIT
chown -R "$RUNNER_USER:$RUNNER_USER" "$RUNNER_DIR"
"${RUNNER_DIR}/bin/installdependencies.sh"

if [[ -z "${RUNNER_REGISTRATION_TOKEN:-}" ]]; then
  read -r -s -p "Short-lived GitHub runner registration token: " RUNNER_REGISTRATION_TOKEN
  echo
fi
if [[ -z "$RUNNER_REGISTRATION_TOKEN" ]]; then
  echo "A short-lived registration token is required." >&2
  exit 1
fi

install --owner "$RUNNER_USER" --group "$RUNNER_USER" --mode 0600 /dev/null "${RUNNER_DIR}/.env"
echo "ACTIONS_RUNNER_HOOK_JOB_STARTED=${RUNNER_HOME}/hooks/job-boundary-cleanup.sh" > "${RUNNER_DIR}/.env"
echo "ACTIONS_RUNNER_HOOK_JOB_COMPLETED=${RUNNER_HOME}/hooks/job-boundary-cleanup.sh" >> "${RUNNER_DIR}/.env"

runuser -u "$RUNNER_USER" -- env RUNNER_REGISTRATION_TOKEN="$RUNNER_REGISTRATION_TOKEN" \
  "${RUNNER_DIR}/config.sh" \
  --url "$REPOSITORY_URL" \
  --token "$RUNNER_REGISTRATION_TOKEN" \
  --name "$RUNNER_NAME" \
  --labels "$RUNNER_LABEL" \
  --work _work \
  --unattended \
  --replace
unset RUNNER_REGISTRATION_TOKEN

cd "$RUNNER_DIR"
./svc.sh install "$RUNNER_USER"

# Bound the two repository runners together, leaving host capacity outside the
# slice. The aggregate limits apply even when both repositories schedule jobs.
cat > "/etc/systemd/system/${SHARED_SLICE}" <<'EOF'
[Unit]
Description=Aggregate capacity boundary for co-located repository CI runners

[Slice]
CPUQuota=600%
MemoryHigh=6G
MemoryMax=7G
MemorySwapMax=8G
TasksMax=4096
EOF

for service in "$GOODTEAM_SERVICE" "$YUVA_SERVICE"; do
  if systemctl list-unit-files --type=service --no-legend | awk '{print $1}' | grep -Fxq "$service"; then
    install --directory --mode 0755 "/etc/systemd/system/${service}.d"
    cat > "/etc/systemd/system/${service}.d/shared-ci-capacity.conf" <<EOF
[Service]
Slice=${SHARED_SLICE}
EOF
  fi
done

systemctl daemon-reload
systemctl restart "$GOODTEAM_SERVICE"
if systemctl is-active --quiet "$YUVA_SERVICE"; then
  systemctl restart "$YUVA_SERVICE"
fi

systemctl --no-pager --full status "$GOODTEAM_SERVICE"
systemctl show "$SHARED_SLICE" \
  -p CPUQuotaPerSecUSec -p MemoryHigh -p MemoryMax -p MemorySwapMax -p TasksMax
systemctl show "$GOODTEAM_SERVICE" "$YUVA_SERVICE" -p Slice
echo "Provisioned ${RUNNER_NAME} in the shared CI capacity slice."
