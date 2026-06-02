#!/usr/bin/env bash
# deploy-goggle.sh — push the USB video-out boot script to the goggle over the UART.
#
# Prereqs:
#   * USB-TTL adapter on the goggle DEBUG header (GND/TX0/RX0), 3.3V, crossed TX<->RX.
#     Console is 1228800 baud. A ground that is rock-solid matters (bad GND = garbage).
#   * The goggle booted to a shell (press Enter once on first connect).
#
# Effect: installs device/run_dbg.sh -> /usrdata/run_dbg.sh, copies buildtime so the
# stock run.sh honors it, then reboots. After reboot the goggle exposes a CDC-ECM USB
# network (goggle 10.55.0.1) + an RTSP server. Set your Mac's ECM adapter to
# 10.55.0.2 / 255.255.255.0, bind a drone, then run tools/view.sh.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${1:-}"                       # optional: pass the serial device explicitly
PYARGS=(); [ -n "$PORT" ] && PYARGS=(--port "$PORT")

# pick a python with pyserial (reuse a local .venv, create if missing)
VENV="$HERE/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  echo "[*] creating venv with pyserial..."
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q pyserial
fi
PY="$VENV/bin/python"
SER="$HERE/tools/goggle.py"

echo "[*] uploading run_dbg.sh ..."
"$PY" "$SER" "${PYARGS[@]}" upload "$HERE/device/run_dbg.sh" /usrdata/run_dbg.sh
echo "[*] setting buildtime + exec bit ..."
"$PY" "$SER" "${PYARGS[@]}" run "cp /usr/usrdata/buildtime /usrdata/buildtime; chmod +x /usrdata/run_dbg.sh; echo deployed"
echo "[*] rebooting goggle ..."
"$PY" "$SER" "${PYARGS[@]}" reboot
echo "[*] done. After it boots:"
echo "      - set the Mac ECM adapter to 10.55.0.2 / 255.255.255.0"
echo "      - bind a drone, then: tools/view.sh"
