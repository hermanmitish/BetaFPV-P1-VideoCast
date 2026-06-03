#!/usr/bin/env bash
# stream-venc8-loop.sh — always-on viewer for the goggle venc8 H.265 tap.
# Waits for the goggle to be reachable, shows the stream, and reconnects forever — so you can leave
# it running and the video just starts whenever the goggle is plugged in and a drone is bound.
# Stop it by closing this window (Ctrl-C / Terminate).
set -u
HOST="${1:-10.55.0.1}"; PORT="${2:-9000}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

echo "ArtLynkStream — always-on low-latency goggle viewer"
echo "Target: tcp://$HOST:$PORT   (plug in USB-C, set the Mac adapter to 10.55.0.2, bind a drone)"
echo "Close this window to stop."
echo

while true; do
  # wait until the goggle's tap port is reachable
  if ! nc -z -G 2 "$HOST" "$PORT" 2>/dev/null; then
    printf "\r[%s] waiting for goggle…        " "$(date +%H:%M:%S)"
    sleep 2; continue
  fi
  echo
  echo "[$(date +%H:%M:%S)] goggle reachable — connecting (video appears once a drone is bound)"
  # ffplay blocks here: if no drone yet it sits waiting, then video starts automatically; a fresh
  # connect forces a keyframe so it locks immediately. NO -fflags nobuffer / -framedrop (drops the IDR).
  ffplay -hide_banner -loglevel error -flags low_delay -f hevc "tcp://$HOST:$PORT" 2>/dev/null
  echo "[$(date +%H:%M:%S)] stream ended / window closed — reconnecting…"
  sleep 1
done
