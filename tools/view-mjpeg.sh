#!/usr/bin/env bash
# view-mjpeg.sh [PORT] — view the goggle's MJPEG "screen share" tap (composited video + OSD).
# Reads the MJPEG stream off the 2nd USB-ACM port and plays it with ffplay. No drone needed —
# it mirrors whatever is on the goggle screen (menu / "no signal" / live feed).
#
# PORT defaults to the 2nd /dev/cu.usbmodem* (ttyGS1 = our video tap; ttyGS0 = app console).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${1:-}"
if [ -z "$PORT" ]; then
  PORT="$(ls -1 /dev/cu.usbmodem* 2>/dev/null | sort | sed -n '2p')"
  [ -n "$PORT" ] || { echo "no /dev/cu.usbmodem* (2nd one) found — is the goggle plugged in?"; exit 1; }
fi
echo "[*] reading MJPEG from $PORT"
exec "$HERE/.venv/bin/python" "$HERE/tools/serial_to_pipe.py" "$PORT" \
  | ffplay -loglevel warning -f mjpeg -fflags nobuffer -flags low_delay -i -
