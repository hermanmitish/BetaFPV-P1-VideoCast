#!/usr/bin/env bash
# deploy-tap.sh — install the low-latency raw-H.265 tap on the goggle, over the UART.
#
# Builds device/videotap.c (needs zig), then uploads it + the ACM-tap boot config and reboots.
# Transfers happen over the DEBUG UART (no ECM needed): the boot script via text upload, the
# .so via printf-hex binary upload. Prereqs: a UART console on the goggle (see docs), zig.
#
# After it boots, view with:  tools/view-tap.sh   (start it, THEN power the drone)
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${1:-}"; PYARGS=(); [ -n "$PORT" ] && PYARGS=(--port "$PORT")

VENV="$HERE/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  echo "[*] creating venv with pyserial..."; python3 -m venv "$VENV"; "$VENV/bin/pip" install -q pyserial
fi
PY="$VENV/bin/python"; G="$HERE/tools/goggle.py"

command -v zig >/dev/null || { echo "need zig (brew install zig) to cross-compile the hook"; exit 1; }
echo "[*] building libvideotap.so (aarch64/glibc-2.25)..."
mkdir -p "$HERE/build"
zig cc -target aarch64-linux-gnu.2.25 -shared -fPIC -O2 -s \
  -o "$HERE/build/libvideotap.so" "$HERE/device/videotap.c" -ldl

echo "[*] uploading hook (binary, printf-hex over UART)..."
"$PY" "$G" "${PYARGS[@]}" bupload "$HERE/build/libvideotap.so" /usrdata/libvideotap.so
echo "[*] uploading ACM-tap boot config..."
"$PY" "$G" "${PYARGS[@]}" upload "$HERE/device/run_dbg-acmtap.sh" /usrdata/run_dbg.sh
echo "[*] buildtime + exec bit..."
"$PY" "$G" "${PYARGS[@]}" run "cp /usr/usrdata/buildtime /usrdata/buildtime; chmod +x /usrdata/run_dbg.sh; echo deployed"
echo "[*] rebooting..."
"$PY" "$G" "${PYARGS[@]}" reboot
echo "[*] done. Start the viewer, THEN power the drone:  tools/view-tap.sh"
