#!/usr/bin/env bash
# Deploy to the running bridge over the USB-ECM SSH link — no SD-card pulling.
#
#   deploy-ssh.sh kernel        push Image + dtb + boot.scr to the boot partition, then reboot
#   deploy-ssh.sh push SRC DST  scp a single file into the running rootfs (e.g. a script/binary)
#   deploy-ssh.sh ssh [args...] open a shell (or run a command) on the bridge
#   deploy-ssh.sh reboot        reboot the bridge
#
# Env: BRIDGE_IP (default 10.66.0.1), BRIDGE_PW (default artlynk).
# Notes: dropbear has no sftp-server, so scp must use the legacy protocol (-O). The kernel artifacts
# come from bridge/docker/images/ (populated by build.sh).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
IMAGES="$HERE/../docker/images"
IP="${BRIDGE_IP:-10.66.0.1}"
PW="${BRIDGE_PW:-artlynk}"
OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8"
SSH() { sshpass -p "$PW" ssh $OPTS "root@$IP" "$@"; }
SCP() { sshpass -p "$PW" scp -O $OPTS "$@"; }

case "${1:-}" in
  kernel)
    for f in Image meson-g12a-radxa-zero.dtb boot.scr; do
      [ -f "$IMAGES/$f" ] || { echo "missing $IMAGES/$f — run build.sh first"; exit 1; }
    done
    echo "[*] mounting boot partition on the bridge..."
    SSH 'mkdir -p /mnt/boot && (mountpoint -q /mnt/boot || mount /dev/mmcblk0p1 /mnt/boot)'
    echo "[*] pushing Image + dtb + boot.scr..."
    SCP "$IMAGES/Image" "$IMAGES/meson-g12a-radxa-zero.dtb" "$IMAGES/boot.scr" "root@$IP:/mnt/boot/"
    echo "[*] sync + reboot..."
    SSH 'sync; umount /mnt/boot; echo "[bridge] rebooting into new kernel"; reboot' || true
    echo "[*] done — reconnect with: $0 ssh"
    ;;
  push)
    src="${2:?usage: $0 push SRC DST}"; dst="${3:?usage: $0 push SRC DST}"
    SCP "$src" "root@$IP:$dst"
    echo "[*] pushed $src -> $dst"
    ;;
  ssh)   shift; SSH "$@" ;;
  reboot) SSH reboot || true ;;
  *) sed -n '2,12p' "$0"; exit 1 ;;
esac
