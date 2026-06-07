# Low-latency video-out: raw H.265 tap (no re-encode)

The [ECM+RTSP path](ecm-rtsp-videoout.md) is simple but adds ~3 s of latency, because it serves
the goggle's **venc8 re-encode** of the composited frame. This path instead tees the **raw
received H.265** straight to USB — no decode, no re-encode — for a much lower-latency full-frame
view on the host. Verified to reconstruct a clean **1920×1080**.

## How it works
- The air unit sends **two low-delay H.265 streams**; the goggle decodes them on **VDEC channel 0
  and channel 1** and composites them for display. They are wide strips that vertically **overlap**:
  chn0 = 1920×560 (top), chn1 = 1920×552 (bottom), 32 px overlap → crop to 1920×1080.
- An `LD_PRELOAD` shim (`device/videotap.c`) interposes the cross-library call libldrt makes into
  libmpi_vdec — `AR_MPI_VDEC_SendStream(chn, pstStream, ms)` — and copies each channel's H.265 to a
  dedicated USB **CDC-ACM** port (chn0→`/dev/ttyGS1`, chn1→`/dev/ttyGS2`). `VDEC_STREAM_S` layout
  (reverse-engineered by dumping the struct): **+0x00 uint32_t len, +0x1c uint8_t\* data** (virtual,
  userspace-readable, H.265 Annex-B). LD_PRELOAD catches it because libldrt resolves the symbol
  through the global scope. (The obvious `AR_LDRT_RX_WIRELESS_UsrStreamGet` is a dormant path — the
  received H.265 is otherwise only handled inside libldrt as decoded YUV.)
- The shim forwards each channel **starting at a clean keyframe** (VPS/SPS/PPS/IDR) so the client
  locks immediately, and uses a bounded-retry non-blocking write so it never stalls the decode
  thread. Read-only w.r.t. the app — display/recording are unaffected.
- The gadget (`device/run_dbg-acmtap.sh`) is `ecm + 3×acm`: `ttyGS0` (app), `ttyGS1`/`ttyGS2`
  (the two video taps). Host sees three `/dev/cu.usbmodem*` (lowest = app, next two = chn0, chn1).

## Deploy
```sh
tools/deploy-tap.sh          # builds the .so (zig), uploads over UART, reboots
```
Transfers go over the **DEBUG UART** (no ECM needed): the boot script as text, the `.so` as a
`printf`-hex binary upload (`goggle-uart.py bupload`). Requires a UART console (see
[ecm-rtsp-videoout.md](ecm-rtsp-videoout.md)) and `zig` (`brew install zig`).

## View
```sh
tools/view-tap.sh            # start this FIRST, THEN power the drone
```
`view-tap.py` drains both ACM ports continuously (one thread each), pipes them into one ffmpeg that
crops the overlap and `vstack`s them, and pipes that to ffplay. **Order matters**: start the viewer
before the link comes up, so the readers are already draining when the tap emits its first keyframe.
Tune the overlap crop if the seam looks off: `TAP_OVERLAP=40 tools/view-tap.sh`.

Eyeball one strip on its own:
```sh
.venv/bin/python tools/serial_to_pipe.py /dev/cu.usbmodemXXXX | ffplay -f hevc -i -
```

## Trade-offs vs RTSP
| | latency | frame | host setup | robustness |
|---|---|---|---|---|
| ECM+RTSP | ~3 s (re-encode) | full 1080p | VLC/ffplay, one URL | session-leak (connect once/boot) |
| Raw tap  | low (no re-encode) | full 1080p (two strips stacked) | start viewer before link | per-channel; works once synced |

## Known limitations
- **Start the viewer before powering the drone.** The tap syncs at the first keyframe of a link
  session; a client joining an already-streaming session lands mid-GOP (no params) and spams
  `PPS id out of range`. (A future improvement: re-emit parameter sets periodically.)
- macOS sometimes won't bind the **ECM** interface on the 4-function composite (`ecm+3acm`) — fine
  for this path, since the tap uses ACM (not the network), and deploys go over the UART.
- The two strips aren't PTS-synced; vstack just pairs current frames (good enough in practice).
- `device/videotap.c` has a `-DTAP_DEBUG`-style history; the shipped hook is the clean VDEC tap.
