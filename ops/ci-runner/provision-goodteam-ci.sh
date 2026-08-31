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
readonly PYTHON_VERSION="3.12.11"
readonly PYTHON_ARCHIVE="Python-${PYTHON_VERSION}.tgz"
readonly PYTHON_SHA256="7b8d59af8216044d2313de8120bfc2cc00a9bd2e542f15795e1d616c51faf3d6"
readonly PYTHON_PREFIX="/opt/goodteam-python/${PYTHON_VERSION}"
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
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install --yes \
  build-essential ca-certificates curl git jq libbz2-dev libffi-dev \
  libgdbm-dev libicu-dev liblzma-dev libncurses-dev libreadline-dev \
  libsqlite3-dev libssl-dev python3 python3-venv tk-dev uuid-dev zlib1g-dev

if ! command -v python3.12 >/dev/null 2>&1; then
  python_source_dir="$(mktemp -d /usr/local/src/goodteam-python.XXXXXX)"
  python_archive_path="$(mktemp)"
  curl --fail --silent --show-error --location \
    "https://www.python.org/ftp/python/${PYTHON_VERSION}/${PYTHON_ARCHIVE}" \
    --output "$python_archive_path"
  echo "${PYTHON_SHA256}  ${python_archive_path}" | sha256sum --check --status
  tar --extract --gzip --file "$python_archive_path" --directory "$python_source_dir"
  cd "${python_source_dir}/Python-${PYTHON_VERSION}"
  ./configure --prefix="$PYTHON_PREFIX" --with-ensurepip=install
  make --jobs="$(nproc)"
  make altinstall
  ln --symbolic --force "${PYTHON_PREFIX}/bin/python3.12" /usr/local/bin/python3.12
  case "$python_source_dir" in
    /usr/local/src/goodteam-python.*) rm -rf -- "$python_source_dir" ;;
    *) echo "Refusing to remove unexpected source path: ${python_source_dir}" >&2; exit 1 ;;
  esac
  rm -f -- "$python_archive_path"
fi
if [[ "$(python3.12 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" != "3.12" ]]; then
  echo "Python 3.12 is required." >&2
  exit 1
fi

if systemctl list-unit-files --type=service --no-legend | awk '{print $1}' | grep -Fxq "$GOODTEAM_SERVICE"; then
  printf '%s\n' '/usr/local/bin' '/usr/bin' '/bin' > "${RUNNER_DIR}/.path"
  chown "$RUNNER_USER:$RUNNER_USER" "${RUNNER_DIR}/.path"
  systemctl restart "$GOODTEAM_SERVICE"
  echo "Python runtime and service PATH verified; ${GOODTEAM_SERVICE} is already installed."
  exit 0
fi

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

runuser -u "$RUNNER_USER" -- env \
  HOME="$RUNNER_HOME" \
  PATH="/usr/local/bin:/usr/bin:/bin" \
  RUNNER_REGISTRATION_TOKEN="$RUNNER_REGISTRATION_TOKEN" \
  "${RUNNER_DIR}/config.sh" \
  --url "$REPOSITORY_URL" \
  --token "$RUNNER_REGISTRATION_TOKEN" \
  --name "$RUNNER_NAME" \
  --labels "$RUNNER_LABEL" \
  --work _work \
  --unattended \
  --replace
unset RUNNER_REGISTRATION_TOKEN
printf '%s\n' '/usr/local/bin' '/usr/bin' '/bin' > "${RUNNER_DIR}/.path"
chown "$RUNNER_USER:$RUNNER_USER" "${RUNNER_DIR}/.path"

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
