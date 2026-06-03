#!/usr/bin/env bash
# deploy-mjpeg.sh — install the MJPEG "screen share" video-out on the goggle, over the UART.
#
# Builds device/mjpegtap.c (needs zig), then uploads it + the MJPEG boot config and reboots.
# Transfers happen over the DEBUG UART (no ECM needed): the boot script via text upload, the
# .so via printf-hex binary upload. Prereqs: a UART console on the goggle (see docs), zig.
#
# After it boots, view with:  tools/view-mjpeg.sh   (no drone needed — it mirrors the screen)
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${1:-}"; PYARGS=(); [ -n "$PORT" ] && PYARGS=(--port "$PORT")

VENV="$HERE/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  echo "[*] creating venv with pyserial..."; python3 -m venv "$VENV"; "$VENV/bin/pip" install -q pyserial
fi
PY="$VENV/bin/python"; G="$HERE/tools/goggle.py"

command -v zig >/dev/null || { echo "need zig (brew install zig) to cross-compile the hook"; exit 1; }
echo "[*] building libmjpegtap.so (aarch64/glibc-2.25)..."
mkdir -p "$HERE/build"
zig cc -target aarch64-linux-gnu.2.25 -shared -fPIC -O2 -s \
  -o "$HERE/build/libmjpegtap.so" "$HERE/device/mjpegtap.c" -ldl -lpthread

echo "[*] uploading hook (binary, printf-hex over UART)..."
"$PY" "$G" "${PYARGS[@]}" bupload "$HERE/build/libmjpegtap.so" /usrdata/libmjpegtap.so
echo "[*] uploading MJPEG boot config..."
"$PY" "$G" "${PYARGS[@]}" upload "$HERE/device/run_dbg-mjpeg.sh" /usrdata/run_dbg.sh
echo "[*] buildtime + exec bit..."
"$PY" "$G" "${PYARGS[@]}" run "cp /usr/usrdata/buildtime /usrdata/buildtime; chmod +x /usrdata/run_dbg.sh; echo deployed"
echo "[*] rebooting..."
"$PY" "$G" "${PYARGS[@]}" reboot
echo "[*] done. View with:  tools/view-mjpeg.sh   (no drone needed)"
