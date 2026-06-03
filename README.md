# P1 HD goggle USB video-out

Tools and notes for getting the **BetaFPV P1 HD (ArLink VR01 / Artosyn AR9301) non-Pro
goggles** to stream their live FPV video to a computer **over the existing USB-C port** —
no Wi-Fi module, no capture card, no permanent hardware mods.

Three paths, all full 1080p over USB:
- **venc8 H.265 TCP tap** ⭐ *(best — low latency, composited video **+ OSD**)* — an `LD_PRELOAD` shim
  tees the goggle's *existing* chn8 H.265 encoder (the same composited+OSD stream RTSP serves) out
  the ECM link over **TCP:9000**, forcing a keyframe on connect. No re-encode, no extra memory, much
  lower latency than RTSP. View: `tools/view-venc8.sh` (`ffplay -f hevc tcp://10.55.0.1:9000`). See
  [docs/lowlatency-venc8-tap.md](docs/lowlatency-venc8-tap.md).
- **ECM + RTSP** (simplest, standard) — swap the USB gadget for a **CDC-ECM** network interface
  (macOS speaks it natively, unlike stock RNDIS) and enable the firmware's **RTSP** server:
  `rtsp://10.55.0.1:554/venc8/stream`, viewable in VLC/ffplay or OBS → Virtual Camera. ~3 s
  latency (serves the same venc8 stream, but with RTSP buffering). See
  [docs/ecm-rtsp-videoout.md](docs/ecm-rtsp-videoout.md).
- **Raw H.265 tap** (low latency, no OSD) — an `LD_PRELOAD` shim tees the *received* H.265 (two
  low-delay strips) straight to USB-ACM, no re-encode; the host decodes both and stacks them to
  1080p. Pre-composite, so no OSD. See [docs/lowlatency-tap.md](docs/lowlatency-tap.md).

> The obvious "make it a UVC webcam" route is a hardware dead end on this model — its USB-2
> controller won't enumerate a UVC gadget. See [docs/enable-uvc.md](docs/enable-uvc.md).

## Quick start
Both need a 3.3V USB-TTL adapter on the goggle `DEBUG` header (`GND/TX0/RX0`, crossed, **1228800
baud**, solid ground) — see [docs/ecm-rtsp-videoout.md](docs/ecm-rtsp-videoout.md).

> **Fast iteration over USB (no UART):** the ECM+RTSP boot config also starts a BusyBox `telnetd`
> on the goggle, so once it's running you can deploy/run over the same USB link instead of the slow
> byte-paced UART: `tools/goggle-net.py run "<cmd>"` (telnet) and `tools/goggle-net.py push <local>
> <remote>` (the goggle `wget`s from a tiny HTTP server on the Mac's `10.55.0.2`). The UART is only
> needed for the very first deploy / recovery.

**ECM + RTSP:** `tools/deploy-goggle.sh` → set the Mac's new ECM adapter to `10.55.0.2/24` → bind a
drone → `tools/view.sh`.

**Low-latency tap:** `tools/deploy-tap.sh` (needs `zig`) → `tools/view-tap.sh` **then** power the drone.

## Layout
| Path | What |
|---|---|
| `device/run_dbg.sh` | ECM+RTSP boot hook for the goggle |
| `device/run_dbg-acmtap.sh` | boot hook for the low-latency tap (ecm + 3×acm gadget) |
| `device/videotap.c` | `LD_PRELOAD` shim that tees raw H.265 to USB-ACM |
| `tools/deploy-goggle.sh` / `tools/deploy-tap.sh` | one-command deploys (RTSP / tap) over UART |
| `device/run_dbg-venc8.sh` + `device/venc8tap.c` | venc8 tap: RTSP boot + shim teeing chn8 H.265 to TCP:9000 |
| `tools/deploy-venc8.sh` / `tools/view-venc8.sh` | deploy (net/UART) / view the low-latency venc8 tap |
| `app/` (`artlynkstream.py`) | **ArtLynkStream** GUI viewer+recorder for the venc8 tap (PySide6 + PyAV) |
| `tools/goggle.py` | UART console helper (`upload`/`bupload`/`run`/`reboot`/`shell`) at 1228800 |
| `tools/goggle-net.py` | network helper over USB-ECM (`run`/`push`/`shell` via telnet + wget) |
| `tools/view.sh` | low-latency `ffplay` of the RTSP stream |
| `tools/view-tap.sh` / `view-tap.py` / `serial_to_pipe.py` | stack the two raw strips into a 1080p view |
| `tools/p1-ota-extract.py` | unpack the OTA firmware partitions |
| `docs/` | full writeups (RTSP, low-latency tap, UVC dead-end, firmware diff) |
| `images/` | firmware `.img` files (git-ignored); `photos/` | board photos |

## Status / known issues
Working end-to-end (verified on `P1_GND_VR04` v2.0.5 and v2.0.6).
- **RTSP:** ~3 s latency (re-encode); stream stops after a few connect/disconnect cycles until
  reboot (mini-RTSP session leak).
- **Tap:** much lower latency; start the viewer **before** powering the drone (it syncs at the first
  keyframe); macOS may not bind ECM on the 4-function tap gadget (irrelevant — the tap uses ACM).

Details and recovery in [docs/ecm-rtsp-videoout.md](docs/ecm-rtsp-videoout.md) and
[docs/lowlatency-tap.md](docs/lowlatency-tap.md).
