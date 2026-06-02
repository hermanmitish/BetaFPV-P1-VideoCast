# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

A toolkit + reverse-engineering notes for adding **USB video-out to the BetaFPV P1 HD
(ArLink VR01, Artosyn AR9301) non-Pro goggles** — streaming their live FPV feed to a
computer over the existing USB-C port. The working solution replaces the goggle's USB
gadget with **CDC-ECM** (macOS-native networking) and enables the firmware's built-in
**RTSP** server: `rtsp://10.55.0.1:554/venc8/stream` (H.265 1080p60).

> History: this started as `arlink_stream`, an open reimplementation of the *air-unit*
> H.265 RTP streamer. That C project (`src/`, `Makefile`) was removed once the focus moved
> to the goggle side. If you need it, it's in the upstream GitHub fork.

Read [README.md](README.md) first, then [docs/ecm-rtsp-videoout.md](docs/ecm-rtsp-videoout.md).

## Layout
- `device/run_dbg.sh` — boot hook installed on the goggle (builds ECM gadget + enables RTSP).
- `tools/goggle.py` — UART console helper (`upload`/`run`/`reboot`/`shell`) at 1228800 baud.
- `tools/deploy-goggle.sh` — one-command deploy (upload hook + buildtime + reboot).
- `tools/view.sh` — low-latency `ffplay` of the stream.
- `docs/` — solution, UVC dead-end, Pro-vs-non-Pro firmware diff.
- `images/` — firmware `.img` (git-ignored); `photos/` — board shots.

## Device access (UART console)
- Goggle `DEBUG` header `GND/TX0/RX0`, **3.3V TTL**, console **1228800 baud** (the
  `/proc/cmdline` `1152000` is nominal; U-Boot prints garbage, kernel/shell are 1228800).
  `inittab` has `::askfirst:-/bin/sh` → press Enter for a root shell.
- 3.3V USB-TTL adapter, crossed (adapter RX←TX0, TX→RX0, GND→GND, VCC unconnected).
- **Solid GND is critical** — a flaky ground gives ~50% garbled bytes and a fake shell that
  only echoes your input (TX bleeding into RX). Fix GND before suspecting anything else.
- macOS `screen` mangles 1228800; use `tools/goggle.py`. The link drops bytes on long bursts,
  so `goggle.py upload` disables device echo + byte-paces + md5-verifies (retried).

## Persistence (no re-flash)
- `/usr/usrdata/run.sh` runs `/usrdata/run_dbg.sh` **instead of** normal boot when
  `/usrdata/buildtime` == `/usr/usrdata/buildtime`. `deploy-goggle.sh` copies buildtime so it
  matches. Disable = `rm /usrdata/run_dbg.sh`. A firmware upgrade changes buildtime → the
  mismatch auto-wipes `/usrdata` and boots stock (so re-deploy after upgrades).
- SD-card OTA is **RSA-2048 signed** (`artosyn_upgrade`) — can't repack a modified image;
  always persist via `/usrdata`.

## Firmware unpack (for diffing versions)
`tools/p1-ota-extract.py <img> <out>` → partitions. `userapp0.img` is a **UBI** image wrapping
a **squashfs in lz4-*block*** format. Stock `unsquashfs`/PySquashfsImage default to lz4-frame
and fail — patch the lz4 decompressor to `lz4.block.decompress(src, uncompressed_size=outsize)`.
Use `ubireader_extract_images` to get the `.ubifs`, then a patched reader. v2.0.5 vs v2.0.6:
`run.sh`, `usb_gadget_configfs.sh`, `cfg_rx_sys.json` are identical → the solution is portable.

## Key facts / gotchas
- **UVC webcam is impossible here** — the USB-2 `dwc2` controller won't enumerate any
  UVC-containing gadget (proven). See [docs/enable-uvc.md](docs/enable-uvc.md).
- **Enable only `RxEncRtspEnable`** (cfg_rx_sys.json), not also `RxRtspEnable` — both → two
  servers fight over port 554 → one spins `accept()` on a dead fd → console flood
  `Accept connection error: Bad file descriptor` (drowns the UART). Flood recovery: heavily
  byte-paced `killall -9 arlink_daemon arlink_fpv`, then fix files.
- RTSP `DESCRIBE` needs a **drone bound** (H.265 SDP needs the first frame); no drone = no video.
- USB-C role/CC detection is finicky (a USB-C HDMI hub once forced a reboot) — prefer USB-C→USB-A
  to the host.
- Known rough edges: ~2–3 s latency; stream stops after a few connect/disconnect cycles
  (mini-RTSP session leak) until reboot.
- The goggle USB gadget IDs as product "Sirius" / mfr "Artosyn" / serial ZBBM5DZFMP; its ACM
  shows on the Mac as `/dev/cu.usbmodemZBBM5DZFMP4` (the enumeration tell).
