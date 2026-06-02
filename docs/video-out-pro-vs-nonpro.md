# Video-out on the goggles: Pro vs non-Pro firmware analysis

Goal: determine whether the **non-Pro** BetaFPV P1 HD goggles (GND/RX unit) can be made
to output video like the **Pro**, which exposes a real-time DisplayPort-over-USB-C stream.

Source material:
- `images/P1_GND_VR04_v2.0.5_20260330_a22d901.img` — non-Pro goggle firmware (HW v2.0)
- `images/P1_GND_VR04_PRO_v2.0.5_20260410_a16633a.img` — Pro goggle firmware (HW V2.1)
- `photos/` — BetaFPV VR01 goggle board + HGLRC Draco VRX (same Artosyn SoM family)

## How to reproduce the unpack

```sh
python3 -m venv /tmp/fwvenv && /tmp/fwvenv/bin/pip install python-lzo ubi_reader lz4 PySquashfsImage
# 1. OTA -> partitions
/tmp/fwvenv/bin/python p1-ota-extract.py images/<img> /tmp/fw_x
# 2. userapp partition is a UBI image wrapping a squashfs (lz4 *block* format)
/tmp/fwvenv/bin/ubireader_extract_images -o /tmp/ubi_x /tmp/fw_x/userapp0.img
# 3. squashfs is lz4-block compressed; stock unsquashfs / PySquashfsImage use lz4-frame,
#    so patch the lz4 decompressor to lz4.block.decompress(src, uncompressed_size=outsize)
```

## Key findings

### Hardware description is identical
- **DTB (`dtb0`) is byte-identical** between Pro and non-Pro. Same SoC peripherals enumerated.
- Relevant DT nodes: `artosyn,vo` (video-output controller @0x8810000), `artosyn,dsi`
  (MIPI-DSI @0x8850000). No dedicated HDMI/DP controller node — digital output goes
  VO/DSI → an **external bridge IC over I2C**.
- `kernel0` and `uboot0` differ only in build strings/hashes.
- `sdk_version.json`: non-Pro = `hardware_version v2.0`, Pro = `V2.1` → **board revision bump**.

### The firmware already ships every output driver
`/usr/lib/libpn_*.so` (present in **both** rootfs):
| Driver | Device | Role |
|---|---|---|
| `libpn_nd043fhdm30p.so` | ND043FHDM30P | internal 4.3" LCD panel |
| `libpn_qy45043a0.so` | QY45043A0 | internal 4.3" LCD panel |
| `libpn_hdmitx_it6612x.so` | ITE **IT66121 / IT66122** | **HDMI transmitter** (I2C + DDC/EDID) |
| `libpn_lt9711.so` | Lontium **LT9711** | **MIPI-DSI → DisplayPort** bridge |
| `libpn_gm8773c.so` | GM8773C | MIPI-DSI bridge |

The output device is **config/probe driven**: `arlink_fpv` reads
`/usr/usrdata/sensor_board_cfg.cjson`, matches `strDeviceType`/`panel_name`/`panel_id`,
and drives the corresponding bridge (`AR_RX_VO_HDMITX_It6612xInit`, `AR_LOWDELAY_RX_VoDeviceInit`).
`sensor_board_cfg.cjson` is **not** in the OTA rootfs → it lives in the per-device
`factory`/`usr_data` partition, written at the factory to describe that board's real hardware.

The factory `config.json` (identical in both) even contains an
`"HDMI display from camera"` test: `test_mpp_vio_fac ... -vo 3`.

### The actual app-level difference (the decisive bit)
Output-device name table compiled into each `arlink_fpv`:

- **non-Pro** knows: `nd043fhdm30p`, `qy45043a0`, `it6612x` (HDMI)
- **Pro** knows:     `nd043fhdm30p`, `gm8773c`, **`lt9711`** (DSI→DP), `it6612x` (HDMI)

So the Pro binary added the **LT9711 DisplayPort** path (and `gm8773c`). The non-Pro binary
has no `lt9711` token at all. Both binaries fully support the **IT66121/22 HDMI-TX** path.

Other Pro-only extras (consistent with a higher-end SKU, not strictly video-out):
- `libheif.so` (HEIF/HEVC still-image support)
- `test_uidesign` UI app +5.8 MB
- GT9711 = Goodix **touch** controller w/ FW-upgrade path → the Pro has a **touchscreen**.

## The display interface is MIPI-DSI (important correction)

Inspecting the panel/bridge drivers:
- `libpn_nd043fhdm30p.so`, `libpn_qy45043a0.so` → `AR_MPI_VO_Dsi_Cmd` ⇒ the internal panels
  are **MIPI-DSI**.
- `libpn_lt9711.so` → `"MIPI In"` / `"DP Out"` ⇒ LT9711 is a **DSI→DisplayPort** bridge.
- `libpn_hdmitx_it6612x.so` → IT66121/22, which take a **parallel RGB** input bus.

So on this board the SoC's only live video output is **MIPI-DSI**. That means the IT66121
HDMI path, although the driver ships, is **not** a cheap mod — it needs a parallel RGB bus the
board doesn't route. The Pro deliberately uses LT9711 because it matches the native DSI output.

## BIG RESULT: USB video-out is already a latent feature of the non-Pro firmware

The non-Pro `arlink_fpv` (GND) already contains **two** complete USB/network video-out paths,
plus the USB stack is already configured for them:

1. **UVC (USB Video Class webcam).** Strings/symbols: `-uvc_ctrl : open or close uvc`,
   `AR_MID_RX_UVC_SERVICE_ENABLE_CTRL/_IMPL`, `UvcVideoSwitch`, `UI_BUT_UVC_VIDEO`,
   `u8UvcVideoSwitch`, `/dev/video%d`, `V4L2_BUF_TYPE_PRIVATE`. The gadget script
   (`/etc/usb_gadget_configfs.sh`) has full UVC function support
   (`uvc_hdr_<maxpacket>_<interval>`) with H.265/H.264/MJPEG/NV12/I420 framebased formats.
   ⇒ the goggle can enumerate as a **plug-and-play USB webcam** outputting the FPV feed.

2. **RTSP server.** Symbols: `AR_RTSP_StartService`, `RxEncRtspEnable`, `RxRtspEnable`,
   `ar_lowdelay_rx_encode_rtsp_{enable,start,stop,disable}`. ⇒ serve the video as an RTSP
   stream over the network and open it in VLC/ffplay.

3. **USB networking is already up by default.** `run.sh` runs
   `usb_gadget_configfs.sh rndis+mtp+uart` — a host plugged into USB-C already gets an RNDIS
   network interface, so the RTSP stream (or any UDP/RTP) needs no extra plumbing.

The Pro/non-Pro difference for these is at most the **UI feature mask** (`...UvcVideo %u` in the
`ui enable function` log) that shows/hides the on-screen button — but the underlying service and
the `bdcmd`/command path exist in the non-Pro binary and bypass that gate. Product code (`-m`)
for the non-Pro is **C401** (Pro adds the LT9711 DP + touch).

## Verdict — ranked, easiest first

**A. Enable the built-in UVC webcam / RTSP (solder-free, likely no code).** Bring up the gadget
with a `uvc_hdr_*` function and toggle the service. Command pattern mirrors
`bdcmd ar_lowdelay_rx -rec_set fps 60`, so the UVC switch is:
`bdcmd ar_lowdelay_rx -uvc_ctrl 1` (and `0` to stop). RTSP has `..._encode_rtsp_enable/start`.
This is configuration, not custom code. **Try this first.**

**B. DIY re-stream (fallback if A is wired shut).** Hook `AR_LDRT_RX_WIRELESS_UsrStreamGet`
(imported PLT symbol; contract below) via `LD_PRELOAD` and RTP the H.265 out with the existing
`src/rtp_h265.c` + `src/udp_sender.c`. No hardware; reuses this repo.

**C. DisplayPort-over-USB-C (matches the Pro, hard).** Populate LT9711 + route DP to USB-C with a
DP-alt mux (V2.1 board change), run the Pro `arlink_fpv` + a `sensor_board_cfg.cjson` naming
`lt9711`. Fine-pitch rework; only if the non-Pro PCB shares the Pro layout with parts left DNP.

### Hook contract for Plan B (from DWARF + disasm of `ar_lowdelay_rx_recv_video_stream` @0x448118)
```c
// imported from the vendor lib; returns 0 on success, fills *out
int AR_LDRT_RX_WIRELESS_UsrStreamGet(struct { void *data; uint32_t len; /*... ~0x18B*/ } *out,
                                     int timeout_ms /* called with 1000 */);
// out->data (offset 0x00) = received H.265 NALU buffer, out->len (offset 0x08) = byte length
```
LD_PRELOAD shim: dlsym(RTLD_NEXT) the real fn, call it, and on ret==0 tee (out->data, out->len)
into the RTP packetizer before returning unchanged. The stock decode/display path is untouched.

### On-device test plan (Plan A)
1. `printf 'cat /proc/cmdline; ls /dev/video*; cat /etc/usb_gadget_configfs.sh | head\nexit\n' | nc ...`
   — confirm UVC kernel support / video nodes.
2. Re-run the gadget with UVC added, e.g. append a `uvc_hdr_3072_...` token to the `rndis+mtp+uart`
   function list (study the `uvc_hdr_*` case arm for the exact arg format).
3. `bdcmd ar_lowdelay_rx -uvc_ctrl 1` and check `dmesg`/logs for `u8UvcVideoSwitch: 1` and a UVC
   bind; plug into a PC and look for a new camera. For RTSP: invoke the rtsp enable command and
   open the advertised URL over the RNDIS IP.
4. If the service refuses because of the product/feature mask, set `u8UvcVideoSwitch=1` in the MID
   config (find its backing file in `/usr/usrdata` or the factory partition) and retry.
