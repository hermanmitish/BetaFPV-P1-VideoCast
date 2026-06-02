# UVC (USB webcam) on the non-Pro P1 HD goggles — DEAD END (kept for the record)

> **VERDICT: not achievable on this hardware.** We unhid the menu, set the flags, and created
> the `uvc` gadget function (`/dev/video0` appears), but the goggle's **USB-2 `dwc2` controller
> will not enumerate ANY UVC-containing gadget** — the UDC stays `not attached` and the host
> sees no device. Tested 5 ways (full composite, `uart+uvc`, `uvc` alone, minimal `uvc_hdr`
> MJPEG-only, bulk-valid `maxpacket=512`); all fail, while every non-UVC gadget enumerates fine.
> The gadget script's special `cdns3` (USB-3) UVC path implies the vendor's webcam feature is
> for their USB-3 variants — almost certainly why it's hidden on the non-Pro. **Use the working
> ECM+RTSP path instead: [ecm-rtsp-videoout.md](ecm-rtsp-videoout.md).** The rest below documents
> what we found while confirming the dead end.

The non-Pro GND firmware fully contains the UVC webcam feature; BetaFPV only **hides the menu
item** and ships the USB gadget without the `uvc` function. (The hiding is enable-able, but the
gadget won't enumerate — see verdict above.)

## Why not just repack the .img?
The SD-card "Update" path is **signature-locked**: `artosyn_upgrade` runs `rsa_verify_image`
(RSA-2048 + SHA-256) against `/etc/artosyn_upgrade_public_rsa2048.pem`. We don't have the private
key, so a modified image won't flash. ⇒ persist via `/usrdata` instead (unsigned, writable).

## What actually gates UVC (from RE of `arlink_fpv`, C401 `rbox` build)
1. **Menu visibility:** `AR_MID_RX_GET_UI_ABILITY_IMPL` copies a byte from `MID_cfg+0x154`
   (`u8UvcVideoSwitch` ability) into the UI-ability struct (`+0xb0`). The C401 default is 0, so the
   "UVC Switch" menu item is hidden. Config source: `cfg_mid.json` / `cfg_ability.json` via
   `AR_CFG_ABILITY_Load` (falls back to hardcoded defaults if the json is absent).
2. **The USB gadget must carry a `uvc` function.** The app only opens `/dev/video%d`; it does NOT
   reconfigure the gadget. Normal boot brings up `rndis+mtp+uart` (no uvc), so UVC can't bind.
3. **The service switch:** `bdcmd ar_lowdelay_rx -uvc_ctrl 1` (`-uvc_ctrl : open or close uvc`),
   backed by `AR_MID_RX_UVC_SERVICE_ENABLE_CTRL`.

⇒ "showing the menu" alone is insufficient (gadget still lacks uvc). The robust goal is to
**bring up the gadget with uvc + flip the service on at boot**, and optionally also unhide the menu.

## Console access (UART)
- Kernel/console = **`ttyS0 @ 1152000` baud**; inittab has `::askfirst:-/bin/sh` ⇒ press Enter for
  a root shell. Use the BetaFPV USB-UART on the goggle's JST/DEBUG header (TX↔RX crossed, GND↔GND,
  leave VCC off): `screen /dev/cu.usbserial-XXXX 1152000`.

## USB gadget UVC token
`usb_gadget_configfs.sh` case arm `uvc_hdr_<maxpacket>_<interval>` (split on `_`: idx2=maxpacket,
idx3=interval). The default `uvc_hdr_*` arm registers **MJPEG 1920x1080**
(`make_format_mjpeg 1 1920 1080 333333 400000`); h265/h264 format links are present but commented.
So the simplest first try exposes an MJPEG 1080p camera, e.g. token `uvc_hdr_3072_5000000`.

## Procedure (staged)
### Phase 1 — shell
Get the UART root shell as above. (telnet over USB-RNDIS is NOT usable from macOS — Mac lacks an
RNDIS driver; UART is the reliable console on a Mac.)

### Phase 2 — runtime test (does not survive reboot)
```sh
# stop the app + watchdog so we can reconfigure USB
killall -9 arlink_daemon arlink_fpv 2>/dev/null
(while :; do printf '\0' >/dev/watchdog0; sleep 2; done) &
# tear down + rebuild the gadget WITH uvc (exact arg tail per the installed script)
/etc/usb_gadget_configfs.sh rndis+uvc_hdr_3072_5000000+mtp+uart 0 dwc2_9311 0x1d6b 0x0101
ls -l /dev/video*            # expect a UVC gadget video node
# restart app, then enable the uvc service
arlink_fpv -m 2 -t 0 >/dev/null & ; sleep 3
bdcmd ar_lowdelay_rx -uvc_ctrl 1
# plug into a Mac/PC -> look for a new camera (QuickTime / Photo Booth / OBS)
```
Watch the log for `pstMidCfg->u8UvcVideoSwitch: 1` and a UVC bind. If the camera shows MJPEG vs
H.265 mismatch, adjust which format the `uvc_hdr_*` arm links (h265 vs mjpeg) to match what the app
feeds.

### Phase 3 — persist via /usrdata (no re-sign)
Put the working gadget+enable sequence in `/usrdata/run_dbg.sh`, then:
```sh
cp /usr/usrdata/buildtime /usrdata/buildtime   # makes run.sh honor run_dbg.sh
chmod +x /usrdata/run_dbg.sh
```
`run.sh` sources `/usrdata/run_dbg.sh` at boot when buildtimes match.

### Phase 4 (optional) — also unhide the on-screen "UVC Switch"
Set the UVC ability byte so `AR_MID_RX_GET_UI_ABILITY_IMPL` reports it enabled — provide a
`cfg_mid.json`/`cfg_ability.json` (under `/usrdata/lowdelay/lowdelay_cfg/`) with the UVC ability
field = 1, or set `u8UvcVideoSwitch` ability at runtime. Verify against the live config schema once
in the shell (the default json may be absent → confirm the exact path the loader checks).

## Open items to confirm live
- Exact installed `usb_gadget_configfs.sh` arg tail / whether `mtp` coexists with `uvc`.
- Whether the app feeds MJPEG or H.265 to `/dev/videoN` (which format link to enable).
- Exact `cfg_*.json` path + UVC ability key for Phase 4.
- Whether `bdcmd ar_lowdelay_rx -uvc_ctrl 1` is the live invocation or needs a different target.
