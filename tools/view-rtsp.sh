#!/usr/bin/env bash
# view.sh — play the goggle's FPV RTSP stream (confirmed-stable form).
# Requires ffmpeg+ffplay (brew install ffmpeg). Needs a drone bound (else no video).
#
# Plain ffplay is the most stable here. A few "Skipping undecodable NALU" lines at the
# start are normal (it joins mid-GOP and locks on the next keyframe).
# Do NOT add `-framedrop`/`-fflags nobuffer`: they stop ffplay from ever locking a keyframe
# (terminal spam, no window). The mini-RTSP server leaks sessions — connect ONCE per boot.
#
# Latency (~3 s) is dominated by the goggle's venc8 re-encode pipeline + its long GOP, not the
# client; shave a little with FFPLAY_LOWLAT=1 (adds -framedrop), real fix = tune venc8 GOP.
URL="${1:-rtsp://10.55.0.1:554/venc8/stream}"
EXTRA=""; [ "${FFPLAY_LOWLAT:-0}" = "1" ] && EXTRA="-framedrop"
echo "[*] $URL   (q or Esc to quit)"
exec ffplay -hide_banner -rtsp_transport tcp $EXTRA "$URL"
