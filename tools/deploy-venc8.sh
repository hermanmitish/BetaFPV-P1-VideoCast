#!/usr/bin/env bash
# deploy-venc8.sh — install the low-latency chn8 H.265 tap on the goggle.
#
# Builds device/venc8tap.c (needs zig) and deploys it + the venc8 boot config. Deploys over the
# USB-ECM network (telnet + wget via tools/goggle-net.py) when the goggle is already running the
# RTSP/telnet config; falls back to UART (tools/goggle-uart.py) with `--uart`.
#
# After it boots (with a drone bound so chn8 encodes), view with:  tools/view-venc8.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
USE_UART=0; [ "${1:-}" = "--uart" ] && USE_UART=1

command -v zig >/dev/null || { echo "need zig (brew install zig)"; exit 1; }
echo "[*] building libvenc8tap.so (aarch64/glibc-2.25)..."
mkdir -p "$HERE/build"
zig cc -target aarch64-linux-gnu.2.25 -shared -fPIC -O2 -s \
  -o "$HERE/build/venc8tap.so" "$HERE/device/venc8tap.c" -ldl -lpthread

if [ "$USE_UART" = "1" ]; then
  VENV="$HERE/.venv"; [ -x "$VENV/bin/python" ] || { python3 -m venv "$VENV"; "$VENV/bin/pip" install -q pyserial; }
  PY="$VENV/bin/python"; G="$HERE/tools/goggle-uart.py"
  echo "[*] uploading over UART..."
  "$PY" "$G" bupload "$HERE/build/venc8tap.so" /usrdata/libvenc8tap.so
  "$PY" "$G" upload  "$HERE/device/run_dbg-venc8.sh" /usrdata/run_dbg.sh
  "$PY" "$G" run "cp /usr/usrdata/buildtime /usrdata/buildtime; chmod +x /usrdata/run_dbg.sh; echo deployed"
  "$PY" "$G" reboot
else
  N="python3 $HERE/tools/goggle-net.py"
  echo "[*] deploying over USB-ECM (telnet)..."
  $N push "$HERE/build/venc8tap.so" /usrdata/libvenc8tap.so
  $N push "$HERE/device/run_dbg-venc8.sh" /usrdata/run_dbg.sh
  $N run "cp /usr/usrdata/buildtime /usrdata/buildtime; chmod +x /usrdata/run_dbg.sh; echo deployed"
  $N run "reboot" || true
fi
echo "[*] done. Bind a drone, then:  tools/view-venc8.sh"
