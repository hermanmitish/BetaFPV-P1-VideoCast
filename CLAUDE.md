# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

**BetaFPV P1 Video Cast** — a patch that adds USB video-out to the **BetaFPV P1 HD (ArLink VR01,
Artosyn AR9301) non-Pro goggles**, streaming their live FPV feed to a computer over the existing
USB-C port. An `LD_PRELOAD` shim tees the goggle's existing **chn8 H.265** encoder (composited + OSD)
out a **CDC-ECM** USB network link over **`tcp://10.55.0.1:9000`**, forcing a keyframe on connect.
A second tap serves the OSD framebuffer on `:9001`. The macOS app (`app/p1cast.py`) decodes, overlays
the OSD, records, and re-streams to OBS.

Read [README.md](README.md) first.

## Layout
- `device/venc8tap.c` — the LD_PRELOAD shim: interposes `AR_MPI_VENC_GetStream`, tees chn8 H.265 out
  `tcp://10.55.0.1:9000`, forces `AR_MPI_VENC_RequestIDR(8,1)` on each client connect, caches VPS/SPS/PPS.
- `device/fbtap.c` — reads `/dev/fb0` (the OSD layer) and serves it RLE-compressed on `tcp://…:9001`.
- `device/run_dbg-venc8.sh` — the goggle boot hook: CDC-ECM gadget + RTSP enable + BusyBox `telnetd` +
  starts the two taps.
- `tools/deploy-venc8.sh` — builds (zig) + deploys the shim, fbtap, and boot hook over USB-ECM
  (`--uart` for the first deploy / recovery).
- `tools/goggle-uart.py` — UART console helper (`upload`/`bupload`/`run`/`reboot`/`shell`).
- `tools/goggle-net.py` — fast deploy/run over the USB-ECM link (`run`/`shell` via telnet, `push` via wget).
- `tools/view-venc8.sh` / `stream-venc8-loop.sh` — terminal `ffplay` viewers.
- `tools/p1-ota-extract.py` — unpack the OTA firmware partitions (for diffing versions).
- `app/p1cast.py` — the PySide6 + PyAV viewer / recorder / OBS re-stream.
- `docs/` — ECM+RTSP mechanism, venc8 tap internals, Pro-vs-non-Pro firmware diff.

## Device access (UART console)
- Goggle `DEBUG` header `GND/TX0/RX0`, **3.3V TTL**, console **1228800 baud** (U-Boot prints garbage;
  kernel/shell are 1228800). `inittab` has `::askfirst:-/bin/sh` → press Enter for a root shell.
- 3.3V USB-TTL adapter, crossed (adapter RX←TX0, TX→RX0, GND→GND, VCC unconnected).
- **Solid GND is critical** — a flaky ground gives ~50% garbled bytes and a fake echo-only shell. Fix
  GND before suspecting anything else. macOS `screen` mangles 1228800; use `tools/goggle-uart.py`.

## Persistence (no re-flash)
- `/usr/usrdata/run.sh` runs `/usrdata/run_dbg.sh` **instead of** normal boot when
  `/usrdata/buildtime` == `/usr/usrdata/buildtime` (the deploy copies buildtime so it matches).
  Disable = `rm /usrdata/run_dbg.sh`. A firmware upgrade changes buildtime → the mismatch auto-wipes
  `/usrdata` and boots stock (so re-deploy after upgrades).
- SD-card OTA is **RSA-2048 signed** — can't repack a modified image; always persist via `/usrdata`.

## Key facts / gotchas
- **chn8 is ~30 fps, infinite GOP.** Params + IDR once at start, then only P-frames — a mid-stream
  viewer must force a keyframe (`RequestIDR(8,1)`) and be fed cached VPS/SPS/PPS. The encoder only runs
  when a drone is bound. The raw H.265 carries no reliable timing, so the recorder derives the real fps
  from frame-count ÷ duration (don't assume 60 — that gives 2× speed).
- **fb0 is ARGB4444 (16bpp), stride 4096**, name "arfb", triple-buffered. It's the top OSD overlay:
  alpha 0 = transparent (video shows through), alpha F = opaque. `fbtap.c` sends the raw 16bpp pixels;
  the host decodes ARGB4444. RLE crushes the mostly-transparent OSD to tens of KB.
- **Enable only `RxEncRtspEnable`** (cfg_rx_sys.json), not also `RxRtspEnable` — both → two servers
  fight over port 554 → one spins `accept()` on a dead fd → console flood `Accept connection error:
  Bad file descriptor`.
- **ffplay viewer:** use `-flags low_delay -f hevc`; NO `-fflags nobuffer` / `-framedrop` (they drop
  the connect-IDR and the decoder never locks).
- **App recording = two files.** Live: tee raw H.265 (cheap, never drops) + log the OSD stream. On stop:
  composite + re-encode **offline** (background) — keeps the live view smooth. Lossless ffmpeg remux
  when OSD was off. The "always re-encode in the decode loop" approach is abandoned (synchronous → lags).
- **App OBS re-stream** is a decoupled thread that drops frames if it falls behind (fine for OBS, never
  for recording). Latency in OBS is bounded by OBS's own pipeline, not the stream format.
- **UVC webcam route is a hardware dead-end** on this model: the USB port is device-role-locked
  (`GOTGCTL` ConIDSts=1, `dr_mode=otg`, `otg_cap=2`) — forcing dwc2 host just storms "Mode Mismatch".
  Hence the network-tap approach. (See the git history for the full investigation.)
- The goggle USB gadget IDs as product "Sirius" / mfr "Artosyn" / serial ZBBM5DZFMP.

## Firmware unpack (for diffing versions)
`tools/p1-ota-extract.py <img> <out>` → partitions. `userapp0.img` is a **UBI** wrapping a **squashfs in
lz4-block** format (patch the lz4 decompressor to `lz4.block.decompress(src, uncompressed_size=outsize)`).
v2.0.5 vs v2.0.6: `run.sh`, `usb_gadget_configfs.sh`, `cfg_rx_sys.json` are identical → the patch is portable.
