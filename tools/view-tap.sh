#!/usr/bin/env bash
# view-tap.sh [CHN0_PORT CHN1_PORT] — low-latency full-frame view from the raw VDEC tap.
# Thin wrapper around view-tap.py (needs the venv's pyserial + ffmpeg/ffplay).
# Start this FIRST, then power the drone (the tap syncs at the first keyframe on link-up).
HERE="$(cd "$(dirname "$0")/.." && pwd)"
exec "$HERE/.venv/bin/python" "$HERE/tools/view-tap.py" "$@"
