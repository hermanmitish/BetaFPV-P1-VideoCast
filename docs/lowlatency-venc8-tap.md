# Low-latency video-out: venc8 H.265 TCP tap (composited + OSD)

The best of both worlds: the **composited picture with OSD** (like RTSP) but at **low latency**
(like the raw tap), as a single stream. It tees the goggle's *existing* H.265 encoder out the USB
link, so it costs **zero extra memory** and re-encodes nothing.

## Why this exists
- **RTSP** ([ecm-rtsp-videoout.md](ecm-rtsp-videoout.md)) serves the goggle's `venc8` (chn8) H.265 —
  composited video with OSD burned in — but RTSP adds ~3 s of buffering.
- A **raw H.265 tap** *before* compositing would be low latency too, but it has **no OSD** and needs
  the two decoded strips stacked — so we tap chn8 (post-composite) instead.
- We tried to make our **own** MJPEG/low-latency encoder: impossible here — during live 1080p video
  the media-memory pool (`/proc/media-mem`) is ~98 MB used / ~10 MB free (fragmented), so a second
  1080p encoder can't allocate (`EN_ERR_NOMEM`). Nothing is trimmable (decode buffers, VbPool,
  framebuffer are all essential).

So instead of making a new encoder, we **tap the one chn8 already runs**.

## How it works
- `device/venc8tap.c` (`LD_PRELOAD`) interposes `AR_MPI_VENC_GetStream`. Whenever the app pulls
  chn8's encoded stream, we copy each pack's H.265 (Annex-B) to a connected **TCP client** on
  `0.0.0.0:9000` (over the ECM link, alongside RTSP/telnet on the same `usb0`). The real consumer
  still gets its stream untouched; writes are non-blocking/best-effort so the encode thread never
  stalls. `VENC_STREAM_S`: `pstPack @+0x00`, `u32PackCount @+0x08`; `VENC_PACK_S` (152 B):
  `pu8Addr @+0x08`, `u32Len @+0x10`.
- **Keyframe on demand.** chn8 runs ~60 fps with an effectively infinite GOP — it emits VPS/SPS/PPS
  + IDR once at start, then only P-frames. A viewer joining mid-stream would have nothing to decode.
  So the shim caches the parameter sets when they appear and, on each new client, sends the cached
  params and calls `AR_MPI_VENC_RequestIDR(8,1)` to force a fresh keyframe, only forwarding from a
  keyframe NAL. The client locks within a frame.

## Deploy
```sh
tools/deploy-venc8.sh          # builds the .so (zig), deploys over USB-ECM (telnet), reboots
tools/deploy-venc8.sh --uart   # ... or over the UART for the first/recovery deploy
```
Boot config `device/run_dbg-venc8.sh` = the RTSP config (so chn8 exists, ECM is up, telnetd runs)
plus the `LD_PRELOAD`. Requires `zig` (`brew install zig`).

## View
```sh
tools/view-venc8.sh            # ffplay -flags low_delay -f hevc tcp://10.55.0.1:9000
```
Bind a drone first (chn8 only encodes when there's video). The connect forces a keyframe, so it
locks immediately.

> **Do NOT add `-fflags nobuffer` or `-framedrop`.** On a raw (timestamp-less) HEVC stream those
> make ffplay drop the initial IDR and it never locks. Plain `-flags low_delay` is already very low
> latency. (ffplay's `fd=` / A-V clock readouts are bogus for raw HEVC — ignore them; the picture is
> live.)

## Trade-offs
| | latency | picture | OSD | host | memory cost |
|---|---|---|---|---|---|
| venc8 TCP tap | low | full 1080p | **yes** | `ffplay -f hevc tcp://…` | none |
| ECM + RTSP | ~3 s | full 1080p | yes | one URL, VLC/OBS | none |
| Raw H.265 tap | low | 1080p (2 strips stacked) | no | start before link | none |

## Known limitations
- Needs a drone bound (chn8 doesn't encode without video) — same as RTSP.
- One client at a time (a new connection replaces the old); each connect forces an IDR (a brief blip
  for any other consumer, e.g. RTSP).
- H.265 elementary stream has no timestamps; pace/seek in the player is meaningless — it's a live
  feed.
