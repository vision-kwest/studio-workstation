#!/usr/bin/env bash
# Ubuntu 24.04 x86_64, CPU-only DCV image preparation.
# Run as root through SSM Run Command on a disposable EC2 build instance.
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
log(){ printf '%s %s\n' "$(date -Is)" "$*"; }
[[ $(id -u) -eq 0 ]] || { echo 'Run as root' >&2; exit 1; }
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 && "$(uname -m)" == x86_64 ]] || { echo 'Ubuntu 24.04 x86_64 required' >&2; exit 1; }
exec > >(tee -a /var/log/studio-workstation-image.log) 2>&1
log "Installing CPU workstation prerequisites"
apt-get update
apt-get install -y --no-install-recommends ubuntu-desktop-minimal gdm3 xorg xserver-xorg-video-dummy curl wget ca-certificates gnupg tar openssl
# X11 is required for DCV console sessions; no GPU is assumed.
install -d /etc/gdm3
cat >/etc/gdm3/custom.conf <<'EOF'
[daemon]
WaylandEnable=false
EOF
# A dedicated, initially locked login account: password must be set AFTER cloning the AMI.
if ! id workstation >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash --groups sudo workstation
fi
passwd -l workstation || true
# Use a dummy Xorg device on headless CPU-only EC2 hosts.
install -d /etc/X11/xorg.conf.d
cat >/etc/X11/xorg.conf.d/20-studio-dummy.conf <<'EOF'
Section "Device"
    Identifier "DummyDevice"
    Driver "dummy"
    VideoRam 256000
EndSection
Section "Monitor"
    Identifier "DummyMonitor"
    HorizSync 30-80
    VertRefresh 50-75
    Modeline "1920x1080" 172.80 1920 2040 2248 2576 1080 1081 1084 1118
EndSection
Section "Screen"
    Identifier "DummyScreen"
    Device "DummyDevice"
    Monitor "DummyMonitor"
    DefaultDepth 24
    SubSection "Display"
        Depth 24
        Modes "1920x1080"
    EndSubSection
EndSection
EOF
DCV_URL="https://d1uj6qtbmh3dt5.cloudfront.net/2026.0/Servers/nice-dcv-2026.0-24952-ubuntu2404-x86_64.tgz"
DCV_SHA256="${DCV_SHA256:?Set DCV_SHA256 to the verified official archive SHA-256 before installing}"
workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT
curl --fail --location --silent --show-error "$DCV_URL" -o "$workdir/dcv.tgz"
printf '%s  %s\n' "$DCV_SHA256" "$workdir/dcv.tgz" | sha256sum -c -
mkdir "$workdir/pkg"
tar -xzf "$workdir/dcv.tgz" -C "$workdir/pkg" --strip-components=1
find "$workdir/pkg" -maxdepth 1 -name 'nice-dcv-server_*_amd64.ubuntu2404.deb' | grep -q .
apt-get install -y "$workdir"/pkg/nice-dcv-server_*_amd64.ubuntu2404.deb "$workdir"/pkg/nice-dcv-web-viewer_*_amd64.ubuntu2404.deb
usermod -aG video dcv
install -d /etc/dcv
cat >>/etc/dcv/dcv.conf <<'EOF'

[security]
authentication="system"

[session-management]
create-session=true

[session-management/automatic-console-session]
owner="workstation"
EOF
systemctl set-default graphical.target
systemctl enable gdm3 dcvserver
log "Installation complete. Reboot and validate BEFORE capturing AMI."
