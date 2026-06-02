# P1 HD goggle USB video-out

Tools and notes for getting the **BetaFPV P1 HD (ArLink VR01 / Artosyn AR9301) non-Pro
goggles** to stream their live FPV video to a computer **over the existing USB-C port** —
no Wi-Fi module, no capture card, no permanent hardware mods.

It works by replacing the goggle's USB gadget with a **CDC-ECM** network interface (which
macOS supports natively, unlike the stock RNDIS) and enabling the firmware's built-in
**RTSP** server. Output: `rtsp://10.55.0.1:554/venc8/stream` (H.265 1080p60), viewable in
VLC/ffplay or pipeable into OBS → Virtual Camera.

> The obvious "make it a UVC webcam" route is a hardware dead end on this model — its USB-2
> controller won't enumerate a UVC gadget. See [docs/enable-uvc.md](docs/enable-uvc.md).

## Quick start
1. Wire a 3.3V USB-TTL adapter to the goggle `DEBUG` header (`GND/TX0/RX0`, crossed, **1228800
   baud**, solid ground). See [docs/ecm-rtsp-videoout.md](docs/ecm-rtsp-videoout.md).
2. Deploy: `tools/deploy-goggle.sh` (uploads the boot hook, reboots).
3. Set the Mac's new ECM network adapter to `10.55.0.2 / 255.255.255.0`.
4. Bind a drone, then: `tools/view.sh`.

## Layout
| Path | What |
|---|---|
| `device/run_dbg.sh` | boot hook installed on the goggle (ECM gadget + RTSP enable) |
| `tools/deploy-goggle.sh` | one-command deploy over UART |
| `tools/goggle.py` | UART console helper (upload / run / reboot / shell) at 1228800 |
| `tools/view.sh` | low-latency `ffplay` of the RTSP stream |
| `tools/p1-ota-extract.py` | unpack the OTA firmware partitions |
| `docs/` | the full writeups (solution, UVC dead-end, firmware diff) |
| `images/` | firmware `.img` files (git-ignored) |
| `photos/` | board photos |

## Status / known issues
Working end-to-end (verified on `P1_GND_VR04` v2.0.5 and v2.0.6). Rough edges:
- ~2–3 s latency (client buffering + re-encode) — partially tunable.
- Stream stops after a few connect/disconnect cycles until the goggle reboots (mini-RTSP
  session leak) — not yet fixed.

Details and recovery steps in [docs/ecm-rtsp-videoout.md](docs/ecm-rtsp-videoout.md).
