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

Two video-out paths: **ECM+RTSP** (simple, ~3s latency) and the **raw H.265 tap** (low latency,
LD_PRELOAD). See [docs/ecm-rtsp-videoout.md](docs/ecm-rtsp-videoout.md) and
[docs/lowlatency-tap.md](docs/lowlatency-tap.md).

## Layout
- `device/run_dbg.sh` — ECM+RTSP boot hook (ECM gadget + RTSP enable).
- `device/run_dbg-acmtap.sh` + `device/videotap.c` — low-latency tap: ecm+3×acm gadget + an
  LD_PRELOAD shim that tees raw received H.265 to USB-ACM.
- `device/run_dbg-venc8.sh` + `device/venc8tap.c` — **best video-out**: RTSP boot + LD_PRELOAD shim
  that interposes `AR_MPI_VENC_GetStream`, tees the existing chn8 H.265 (composited + OSD) out
  `tcp://10.55.0.1:9000`, forcing a keyframe (`AR_MPI_VENC_RequestIDR(8,1)`) on each client connect.
  Low latency, zero extra memory. Deploy `tools/deploy-venc8.sh`, view `tools/view-venc8.sh`.
- `tools/goggle.py` — UART console helper (`upload` text / `bupload` binary / `run`/`reboot`/`shell`).
- `tools/goggle-net.py` — fast deploy/run over the USB-ECM link (no UART): `run`/`shell` via BusyBox
  `telnetd` (10.55.0.1:23, `-l /bin/sh`, started by `run_dbg.sh`), `push` via the goggle `wget`-ing
  from a temp HTTP server on the Mac's 10.55.0.2. UART only needed for first deploy / recovery.
- `tools/deploy-goggle.sh` / `tools/deploy-tap.sh` — one-command deploys (RTSP / tap) over UART.
- `tools/view.sh` (RTSP) / `tools/view-tap.sh`+`view-tap.py`+`serial_to_pipe.py` (tap).
- `docs/` — RTSP solution, low-latency tap, UVC dead-end, Pro-vs-non-Pro firmware diff.
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
  shows on the Mac as `/dev/cu.usbmodem*` (the enumeration tell).
- **Can't make a 2nd encoder during live video:** `/proc/media-mem` (MMZ) is ~98MB used / ~10MB free
  (fragmented) with 1080p video up, so `ar_lowdelay_module_venc_init` for our own MJPEG/H26x channel
  fails `EN_ERR_NOMEM` (0x8008800C). MJPEG *is* supported (gets to the alloc step); it just won't fit,
  and nothing's trimmable. → tap the existing chn8 encoder instead (venc8tap). The app encodes on
  **chn8** (RTSP); record=chn4, replay=chn16. `ar_lowdelay_module_venc_init` only dlsym-resolves once
  the encode path is live (drone bound).
- **venc8 tap:** chn8 H.265 is ~60fps with an effectively infinite GOP (params+IDR once at start,
  then only P-frames) — a mid-stream viewer must force a keyframe (`AR_MPI_VENC_RequestIDR(8,1)`) and
  be fed cached VPS/SPS/PPS, else it can't decode. ffplay viewer: NO `-fflags nobuffer`/`-framedrop`
  (drops the IDR); use `-flags low_delay -f hevc`. ffplay's fd=/clock readouts are bogus for raw HEVC.
- **Low-latency tap:** the received H.265 is two low-delay streams decoded on VDEC chn0 (1920×560)
  + chn1 (1920×552), 32px overlap. The shim interposes `AR_MPI_VDEC_SendStream(chn, pstStream, ms)`
  (interposable across libs); `VDEC_STREAM_S` = `+0x00 u32 len`, `+0x1c u8* data` (virtual, H.265
  Annex-B). It forwards from a clean keyframe. Start `view-tap.sh` BEFORE powering the drone.
- Binary files reach the device over UART via `goggle.py bupload` (printf-hex; device has no
  base64/xxd). The ECM-network `wget` path is flaky (macOS may not bind ECM on the tap gadget).
