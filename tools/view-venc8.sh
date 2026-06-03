#!/usr/bin/env bash
# view-venc8.sh — view the low-latency chn8 H.265 tap (composited video + OSD) over USB.
# Connects to the goggle's TCP tap (tee'd from its existing RTSP encoder); the connect forces a
# keyframe so it locks immediately. Much lower latency than RTSP (no RTSP buffering).
#
# Needs a drone bound (chn8 only encodes when there's video) and the venc8 tap deployed
# (tools/deploy-venc8.sh). The Mac's ECM adapter must be 10.55.0.2/24.
#
# NOTE: do NOT add -fflags nobuffer / -framedrop here — they drop the initial IDR and the
# decoder never locks. Plain -flags low_delay is already very low latency.
HOST="${1:-10.55.0.1}"; PORT="${2:-9000}"
exec ffplay -flags low_delay -f hevc "tcp://${HOST}:${PORT}"
