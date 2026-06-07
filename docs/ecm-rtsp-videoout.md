# USB video-out on the non-Pro P1 HD goggles (working solution)

Get the goggle's live FPV feed onto a computer **over the existing USB-C port** — no extra
radio, no Wi-Fi module, no soldering beyond a one-time UART console. Verified on
`P1_GND_VR04` v2.0.5 and v2.0.6 (the relevant boot/gadget/config files are byte-identical
across those versions, so it's portable).

Result: `rtsp://10.55.0.1:554/venc8/stream` — H.265 1920x1080@60, playable in VLC/ffplay, or
fed into OBS → Virtual Camera to appear as a webcam in any app.

## Why not a UVC webcam?
We tried hard; it's a hardware dead end on this model (the USB port is device-role-locked — see the
git history for the investigation).
The non-Pro's USB-2 `dwc2` controller refuses to enumerate *any* UVC-containing gadget, while
every non-UVC gadget enumerates fine. The vendor's webcam feature targets their USB-3 (cdns3)
variants — almost certainly why BetaFPV hides the "UVC Switch" menu on the non-Pro.

## The approach
The firmware already has an RTSP server (`libar_minirtsp.so`). Its only network path out of the
box is **RNDIS**, which **macOS can't use**. But the kernel also has the **CDC-ECM** gadget
function, which macOS supports natively. So:

1. At boot, build a **CDC-ECM + ACM** USB gadget instead of RNDIS (goggle `usb0` = `10.55.0.1`,
   host adapter `10.55.0.2`).
2. Enable the firmware's **encode RTSP** server (`RxEncRtspEnable=1`).
3. Open the stream on the host.

This is all done by `device/run_dbg-venc8.sh` (installed as `/usrdata/run_dbg.sh`), via the stock
boot hook (persistent, signature-free — the SD-card OTA path is RSA-signed and can't be repacked).

## One-time: get a console (UART)
- The goggle `DEBUG` header is `GND / TX0 / RX0`, **3.3V TTL**, console **1228800 baud**
  (`::askfirst:-/bin/sh` → press Enter for a root shell).
- Use a 3.3V USB-TTL adapter (CP2102/CH340/FTDI, or an ESP32-CAM-MB's CH340). Wire crossed:
  adapter **RX←TX0**, **TX→RX0**, **GND→GND**; leave the adapter's VCC disconnected.
- **A solid ground is critical.** A flaky GND gives ~50% garbled bytes and a fake "shell" that
  only echoes your own typing (TX bleeding into RX). If reads are garbage, fix GND first.
- `screen` on macOS mangles 1228800; use the Python tools here (`tools/goggle-uart.py`).

## Deploy
```sh
tools/deploy-venc8.sh --uart      # build + deploy over the UART (drop --uart to use USB-ECM)
```
It uploads the taps + `device/run_dbg-venc8.sh`, copies `buildtime` (so `run.sh` honors it), and reboots.

After it boots:
1. On the Mac: **System Settings → Network →** the new ECM adapter → **Configure IPv4:
   Manually → 10.55.0.2 / 255.255.255.0** (router blank). `ping 10.55.0.1` to confirm.
2. **Bind a drone** (air unit) so there's video to encode — without it, RTSP `DESCRIBE`
   returns nothing (H.265 SDP needs the first frame's VPS/SPS/PPS).
3. `ffplay rtsp://10.55.0.1:554/venc8/stream` or VLC. For low latency, use the venc8 tap instead
   ([lowlatency-venc8-tap.md](lowlatency-venc8-tap.md)).

## Verify from the host
```sh
ffprobe -rtsp_transport tcp rtsp://10.55.0.1:554/venc8/stream     # -> hevc 1920x1080 60fps
ffmpeg  -rtsp_transport tcp -i rtsp://10.55.0.1:554/venc8/stream -frames:v 1 -update 1 frame.jpg
```

## Revert / recover
- Stock baseline: delete the hook and reboot —
  `tools/goggle-uart.py run "rm -f /usrdata/run_dbg.sh; reboot"`.
- A firmware upgrade auto-wipes `/usrdata` (buildtime mismatch) → reverts to stock on its own;
  just re-run `deploy-venc8.sh` to reinstall.

## Gotchas / known issues
- **Enable ONLY `RxEncRtspEnable`.** Setting `RxRtspEnable` too makes two servers fight over
  port 554; the loser spins `accept()` on a dead fd and floods the console with
  `Accept connection error: Bad file descriptor` (drowns the UART, spins CPU). If you hit a
  flood, the UART upload won't survive it — stop the source first, heavily byte-paced:
  `killall -9 arlink_daemon arlink_fpv`, then fix files.
- **Latency + slow start = venc8's long GOP.** venc8 emits keyframes only rarely, so an RTSP client
  joins mid-GOP and must wait for the next IDR (hence the seconds of latency; ffplay shows `Skipping
  undecodable NALU` until a keyframe arrives). The **venc8 tap**
  ([lowlatency-venc8-tap.md](lowlatency-venc8-tap.md)) fixes this by forcing
  `AR_MPI_VENC_RequestIDR(8,1)` on connect and serving the same stream over plain TCP — use it for low
  latency. VLC is the reliable viewer for plain RTSP.
- **Stops after a few connect/disconnect cycles** (needs a goggle reboot). The mini-RTSP server
  appears to leak the venc session across SETUP/TEARDOWN. Not yet fixed.
- USB-C: the goggle's port has finicky role/CC detection (a USB-C HDMI hub once made it reboot).
  A USB-C→USB-A cable to the host is the most reliable.

## How it was unpacked (for diffing firmware)
`tools/p1-ota-extract.py` → OTA partitions; `userapp0.img` is a UBI image wrapping a
**squashfs in lz4-block** format (stock `unsquashfs` defaults to lz4-frame and fails — patch the
lz4 decompressor to `lz4.block.decompress(src, uncompressed_size=outsize)`). See
[video-out-pro-vs-nonpro.md](video-out-pro-vs-nonpro.md).
