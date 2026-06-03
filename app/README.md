# ArtLynkStream — goggle viewer app

A one‑window, low‑latency viewer + recorder for the goggle's [venc8 H.265 USB tap](../docs/lowlatency-venc8-tap.md).

![states] one window, three states:
- **gray** "Goggle not connected" — `tcp://10.55.0.1:9000` unreachable (USB unplugged / tap not running)
- **black** "Waiting for drone…" — goggle reachable but no video yet (no drone bound)
- **video** — the composited feed (+OSD), low latency
- **● Record** — saves the H.265 to `~/Movies` (no re‑encode) and remuxes to a QuickTime‑playable `.mp4`

It auto‑reconnects, so you can leave it running: plug in the goggle, bind a drone, and video starts.

## Why PyAV (not mpv)
Video is decoded in‑process with **PyAV** — the same FFmpeg libraries ffplay uses — with frame
threading **off**, so frames are shown the instant they decode (matches ffplay's latency). mpv was
tried first and buffered badly on this raw, PTS‑less H.265‑over‑TCP stream (and `profile=low-latency`
secretly sets `fflags=+nobuffer`, dropping our connect‑IDR). Recording starts on a **forced reconnect**
so the file always begins at a fresh keyframe (the GOP is otherwise effectively infinite).

## Setup & run
```sh
python3 -m venv app/.venv
app/.venv/bin/pip install -r app/requirements.txt    # PySide6, av, numpy
app/.venv/bin/python app/artlynkstream.py
```
Needs `ffmpeg` on the system (Homebrew) for the `.mp4` remux, the goggle on the venc8 tap
(`tools/deploy-venc8.sh`), and the Mac's USB‑ECM adapter at `10.55.0.2/24`.

There's also a double‑click **`ArtLynkStream.app`** launcher on the Desktop (points here).
