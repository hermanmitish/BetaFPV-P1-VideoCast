# P1 Video Cast — the app

A one‑window, low‑latency viewer + recorder for the goggle's
[H.265 USB tap](../docs/lowlatency-venc8-tap.md).

Three states in one window:
- **gray** "Goggle not connected" — `tcp://10.55.0.1:9000` unreachable (USB unplugged / tap not running)
- **black** "Waiting for drone…" — goggle reachable but no video yet (no drone bound)
- **video** — the composited feed, low latency

It auto‑reconnects, so just leave it running: plug in the goggle, bind a drone, and video starts.

## Buttons
- **OSD** — overlays the goggle's telemetry (its fb0 layer, tapped on `:9001`) onto the video,
  GPU‑composited. Off by default.
- **OBS** — exposes a clean H.264/MPEG‑TS re‑stream on `tcp://127.0.0.1:9200`, so OBS can add it as a
  **Media Source** (Input `tcp://127.0.0.1:9200`). Lazy — only encodes while it's on *and* OBS is
  connected; carries the OSD when OSD is on. (Latency in OBS is bounded by OBS's own pipeline, not us.)
- **Record** — saves an `.mp4` to `~/Movies`. The raw H.265 is teed live (cheap, never drops a frame);
  on stop it's composited with the OSD and re‑encoded **offline** (a background "Saving…" pass), so the
  live view never slows down. Encoded at the goggle's real frame rate (auto‑detected from the clip).

## Why PyAV (not mpv)
Video is decoded in‑process with **PyAV** — the FFmpeg libraries ffplay uses — with frame threading
**off**, so frames show the instant they decode (matches ffplay's latency). mpv buffered badly on this
raw, PTS‑less H.265‑over‑TCP stream. Recording starts on a **forced reconnect** so the file always
begins at a fresh keyframe (the GOP is otherwise effectively infinite).

## Setup & run
```sh
python3 -m venv app/.venv
app/.venv/bin/pip install -r app/requirements.txt    # PySide6, av, numpy
app/.venv/bin/python app/p1cast.py
```
Needs `ffmpeg` on the system (Homebrew) for the lossless OSD‑off remux, the goggle on the venc8 tap
(`tools/deploy-venc8.sh`), and the Mac's USB‑ECM adapter at `10.55.0.2/24`.
