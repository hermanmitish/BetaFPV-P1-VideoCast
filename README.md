# BetaFPV P1 Video Cast

**Patch the BetaFPV P1 HD (ArLink VR01 / Artosyn AR9301) FPV goggles to send their live 1080p
video out the existing USB‑C port** — to your Mac, into OBS, with the telemetry OSD overlaid and
recording to MP4. No Wi‑Fi module, no capture card, no permanent hardware mods.

It `LD_PRELOAD`s a tiny shim into the goggle's firmware that tees its **existing** H.265 encoder
(the composited feed the goggles already produce) out a CDC‑ECM USB network link over TCP, forcing
a keyframe on connect — no re‑encode on the goggle, no extra memory, low latency. A companion macOS
app, **P1 Video Cast**, decodes it, optionally overlays the OSD, records, and can re‑stream to OBS.

## What you get
- **Live 1080p H.265** to the Mac over USB‑C, low latency (`tcp://10.55.0.1:9000`)
- **OSD overlay** — the goggle's telemetry, tapped from its framebuffer (`:9001`) and composited on top
- **Recording** — MP4 to `~/Movies`, OSD baked in, with zero live‑view slowdown
- **OBS** — one‑click H.264/MPEG‑TS re‑stream (`tcp://127.0.0.1:9200`) for an OBS Media Source

## Requirements
- A **BetaFPV P1 HD** (non‑Pro) goggle — verified on firmware v2.0.5 / v2.0.6
- A bound drone / air‑unit (the encoder only runs when there's video)
- macOS · Python 3 · `brew install zig ffmpeg` (zig cross‑compiles the goggle shim)
- A **3.3 V USB‑TTL serial adapter** — for the one‑time install only

## Install (one time, over serial)
1. Wire the adapter to the goggle `DEBUG` header — `GND/TX0/RX0`, **crossed**, **1228800 baud**,
   solid ground. (Details in [docs/ecm-rtsp-videoout.md](docs/ecm-rtsp-videoout.md).)
2. Build + deploy the patch and reboot:
   ```sh
   tools/deploy-venc8.sh --uart
   ```
   It installs to the goggle's `/usrdata` — **no firmware re‑flash**. It persists until a firmware
   upgrade (then just re‑run it); remove with `rm /usrdata/run_dbg.sh` on the goggle.
3. Set the Mac's new **USB‑ECM network adapter** to `10.55.0.2 / 255.255.255.0`.

After the first install you can re‑deploy over USB‑C with no serial: `tools/deploy-venc8.sh`.

## Use
```sh
python3 -m venv app/.venv
app/.venv/bin/pip install -r app/requirements.txt
app/.venv/bin/python app/p1cast.py
```
Plug in the goggle, bind a drone — video appears. Toggle **OSD** for telemetry, **OBS** to expose the
re‑stream, **Record** to save an MP4. See [app/README.md](app/README.md).

Prefer the terminal? `tools/view-venc8.sh` (one‑liner `ffplay`) or `tools/stream-venc8-loop.sh`
(always‑on). The deploy also enables the firmware's built‑in **RTSP** server
(`rtsp://10.55.0.1:554/venc8/stream`, ~3 s latency) if you'd rather use any RTSP player.

## Layout
| Path | What |
|---|---|
| `device/venc8tap.c` | the `LD_PRELOAD` shim — tees chn8 H.265 to `:9000`, forces a keyframe on connect |
| `device/fbtap.c` | OSD tap — serves the goggle's fb0 telemetry layer on `:9001` |
| `device/run_dbg-venc8.sh` | goggle boot hook (CDC‑ECM gadget + RTSP + telnet + the taps) |
| `tools/deploy-venc8.sh` | build + deploy over serial (`--uart`) or USB‑ECM |
| `tools/view-venc8.sh`, `stream-venc8-loop.sh` | terminal `ffplay` viewers |
| `tools/goggle-uart.py`, `goggle-net.py` | goggle shells (serial / over USB‑ECM) |
| `tools/p1-ota-extract.py` | unpack the OTA firmware partitions |
| `app/` | **P1 Video Cast** — GUI viewer / recorder / OBS re‑stream (PySide6 + PyAV) |
| `docs/` | how the ECM+RTSP path works, venc8 tap internals, Pro‑vs‑non‑Pro firmware notes |

## Notes & limits
- The goggle's USB port is **device‑role‑locked**, so the obvious "make it a UVC webcam" route is a
  hardware dead‑end on this model — hence the network‑tap approach.
- The built‑in RTSP server leaks sessions after a few connect/disconnect cycles (reboot to clear);
  the venc8 tap is the recommended path.

---

*Vibe‑coded with [Claude Code](https://claude.com/claude-code) 🤖 — see the commit history for the
full (occasionally winding) journey, including the dead‑ends.*
