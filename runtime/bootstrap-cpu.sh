#!/usr/bin/env bash
# Studio Workstation runtime bootstrap. Executed locally by systemd, not by SSM.
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=l
STATE=/var/lib/studio-workstation
mkdir -p "$STATE" /var/log/studio-workstation
exec >>/var/log/studio-workstation/bootstrap.log 2>&1
echo "=== $(date -Is) stage $(cat "$STATE/stage" 2>/dev/null || echo 0) ==="
trap 'code=$?; echo "ERROR $(date -Is) stage $(cat "$STATE/stage" 2>/dev/null || echo 0) exit $code"; exit "$code"' ERR
. /etc/os-release
test "$ID" = ubuntu
test "$VERSION_ID" = 24.04
test "$(uname -m)" = x86_64

# No SSH, no remote execution dependency. APT transactions are bounded.
apt_refresh() {
  timeout --signal=TERM --kill-after=60s 15m apt-get update -o Acquire::Retries=3
}
apt_install() {
  timeout --signal=TERM --kill-after=60s 45m apt-get install -y -o Acquire::Retries=3 "$@"
}
# A core-system package transition coincided with prior guest reachability failures.
# Do not assume it was the root cause. Fail safely rather than silently risk it.
check_core_upgrade() {
  local plan="$STATE/apt-plan-$(cat "$STATE/stage").txt"
  apt-get -s install "$@" | tee "$plan"
  if grep -Eq '^Inst (systemd|libsystemd0|systemd-sysv|udev|libudev1) ' "$plan"; then
    echo 'CORE_OS_UPGRADE_REQUIRED: choose a refreshed Canonical Ubuntu 24.04 AMI before continuing.'
    exit 40
  fi
}
advance_reboot() {
  printf '%s\n' "$1" > "$STATE/stage"
  sync
  echo "Stage complete. Scheduling reboot for next stage $1."
  shutdown -r +1 "Studio Workstation bootstrap stage transition"
}
stage=$(cat "$STATE/stage" 2>/dev/null || echo 0)
case "$stage" in
  0)
    echo "Stage 0: install GDM first"
    apt_refresh
    check_core_upgrade gdm3
    apt_install gdm3
    dpkg --audit
    mkdir -p /etc/gdm3
    if ! grep -q '^\[daemon\]' /etc/gdm3/custom.conf; then
      printf '\n[daemon]\n' >> /etc/gdm3/custom.conf
    fi
    sed -i '/^WaylandEnable=/d' /etc/gdm3/custom.conf
    sed -i '/^\[daemon\]/a WaylandEnable=false' /etc/gdm3/custom.conf
    advance_reboot 1
    ;;
  1)
    echo "Stage 1: install RES-aligned Ubuntu desktop and CPU dummy Xorg"
    apt_refresh
    check_core_upgrade ubuntu-desktop xserver-xorg-video-dummy
    apt_install ubuntu-desktop xserver-xorg-video-dummy
    dpkg --audit
    cat > /etc/X11/xorg.conf <<'XORG'
Section "Device"
    Identifier "DummyDevice"
    Driver "dummy"
    Option "UseEDID" "false"
    VideoRam 512000
EndSection
Section "Monitor"
    Identifier "DummyMonitor"
    HorizSync 5.0 - 1000.0
    VertRefresh 5.0 - 200.0
    Option "ReducedBlanking"
EndSection
Section "Screen"
    Identifier "DummyScreen"
    Device "DummyDevice"
    Monitor "DummyMonitor"
    DefaultDepth 24
    SubSection "Display"
        Viewport 0 0
        Depth 24
        Virtual 4096 2160
    EndSubSection
EndSection
XORG
    if ! id workstation >/dev/null 2>&1; then
      useradd -m -s /bin/bash workstation
    fi
    passwd -l workstation || true
    systemctl set-default graphical.target
    systemctl enable gdm3
    advance_reboot 2
    ;;
  2)
    echo "Stage 2: install verified Amazon DCV packages"
    apt_refresh
    workdir=$(mktemp -d)
    trap 'rm -rf "$workdir"' EXIT
    cd "$workdir"
    base=https://d1uj6qtbmh3dt5.cloudfront.net
    curl -fLsS --retry 3 "$base/nice-dcv-ubuntu2404-x86_64.tgz" -o dcv.tgz
    curl -fLsS --retry 3 "$base/nice-dcv-ubuntu2404-x86_64.tgz.sha256sum" -o checksum
    expected=$(awk '{print $1}' checksum)
    [[ "$expected" =~ ^[a-fA-F0-9]{64}$ ]]
    printf '%s  dcv.tgz\n' "$expected" | sha256sum -c -
    mkdir packages
    tar -xzf dcv.tgz -C packages --strip-components=1
    shopt -s nullglob
    server=(packages/nice-dcv-server_*_amd64.ubuntu2404.deb)
    viewer=(packages/nice-dcv-web-viewer_*_amd64.ubuntu2404.deb)
    xdcv=(packages/nice-xdcv_*_amd64.ubuntu2404.deb)
    test "${#server[@]}" -eq 1
    test "${#viewer[@]}" -eq 1
    test "${#xdcv[@]}" -eq 1
    apt_install "./${server[0]}" "./${viewer[0]}" "./${xdcv[0]}"
    usermod -aG video dcv
    mkdir -p /etc/dcv /etc/X11/xorg.conf.d
    cat > /etc/X11/xorg.conf.d/99-disable-dpms.conf <<'DPMS'
Section "Extensions"
    Option "DPMS" "Disable"
EndSection
DPMS
    # Standalone Linux authentication, not RES externalAuth/broker.
    cat >> /etc/dcv/dcv.conf <<'DCV'

[security]
authentication="system"

[session-management]
create-session=true

[session-management/automatic-console-session]
owner="workstation"
DCV
    systemctl enable dcvserver
    rm -rf "$workdir"
    trap - EXIT
    advance_reboot 3
    ;;
  3)
    echo "Stage 3: validate after reboot"
    systemctl is-active gdm3
    systemctl is-active dcvserver
    for n in $(seq 1 30); do
      if dcv list-sessions 2>/dev/null | grep -q console && pgrep -x Xorg >/dev/null; then
        break
      fi
      sleep 5
    done
    dcv list-sessions
    dcv list-sessions | grep -q console
    pgrep -a Xorg
    ss -lnt | grep -q ':8443 '
    test "$(passwd -S workstation | awk '{print $2}')" = L
    touch "$STATE/ready"
    echo 'READY: DCV service checks passed; browser login still requires secure password provisioning and manual acceptance test.'
    ;;
  *)
    echo "Unexpected bootstrap stage: $stage" >&2
    exit 2
    ;;
esac
