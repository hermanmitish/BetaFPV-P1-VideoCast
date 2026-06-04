# UVC (USB webcam) on the non-Pro P1 HD goggles — SOLVABLE (the dwc2 pullup fix)

> **UPDATE 2026-06: the "dead end" verdict was WRONG — there's one lever we never pulled.**
> The identical symptom (the `uvc` gadget binds, `/sys/class/udc/8000000.usb/state` stays
> `not attached`, the host sees no device) was hit AND THEN SOLVED on a Radxa Zero's dwc2. Root cause:
> **the dwc2 controller parks its USB pull-up OFF the moment a UVC function is bound** — device
> register `DCTL` (dwc2 base + 0x804) bit1 `SftDiscon` is left = 1, so the host never sees a connect.
> ECM/RNDIS-only gadgets connect fine. **The fix is a DIRECT register write after binding:**
> ```sh
> devmem <dwc2_base + 0x804> 32 0x00000000   # clear SftDiscon -> pull-up ON
> ```
> On the goggle the dwc2 base is `0x8000000` (UDC `8000000.usb`) → **`DCTL = 0x8000804`**. Script:
> **[device/uvc-up.sh](../device/uvc-up.sh)** builds a minimal MJPEG UVC gadget and applies this.
>
> **Why we missed it:** we had tried `echo connect > /sys/class/udc/8000000.usb/soft_connect`, but that
> goes through the dwc2 driver's pull-up path — which is **gated by the OTG state machine** (`is_otg=1`),
> so it silently did nothing (see the 2026-06 note below). The **direct `devmem` write bypasses the
> driver/OTG gate** and forces the pull-up on. On the Radxa, `soft_connect` was likewise non-functional
> but `devmem` to DCTL worked → composite ECM+UVC enumerated, "ArtLynk Bridge" appeared as a webcam.
> Everything else in the analysis below still holds (not a FIFO/endpoint error, descriptors are fine,
> isoc hardware is present) — it was purely the pull-up/connect being parked off for UVC. The signed
> firmware is NOT a blocker for this: the fix is a userspace `devmem`, no kernel/devicetree patch needed.
> (The ECM+RTSP path [ecm-rtsp-videoout.md](ecm-rtsp-videoout.md) still works as a fallback.)

> **Precise mechanism (re-confirmed 2026-06, dmesg captured over the telnet workflow).** It is NOT
> an endpoint/FIFO allocation failure: binding the uvc gadget to the UDC *succeeds* cleanly —
> `configfs-gadget gadget: uvc_function_bind` / `BULK transfer` / `dwc2 8000000.usb: bound driver
> configfs-gadget`, no error. But the controller then **never leaves `not attached`**: held bound
> for 20 s, `/sys/class/udc/8000000.usb/state` stayed `not attached` the whole time, there were
> **zero USB reset/enumeration events** in dmesg, and the **host (macOS) saw no USB device and no
> camera**. Rebinding the ECM gadget attaches instantly. So the gadget *binds* but the bus
> connection/enumeration never starts with UVC descriptors present (it even falls back to BULK,
> maxpacket 1024). The failure is at the attach/enumerate layer — UVC-specific and reproducible —
> consistent with the webcam feature being wired only for the `cdns3` USB-3 controller.
>
> Re-runnable over USB (no UART): a detached script unbinds ECM, builds a `uvc` gadget as g2, binds
> + holds it while the Mac polls `system_profiler SPUSBDataType`, then rebinds ECM (see git history
> of this session for `/tmp/uvc_test2.sh`).
>
> **⚠️ SUPERSEDED (see the fix at top): we did NOT exhaust the userspace levers — the missing one was a
> direct `devmem` write to `DCTL` (0x8000804). The `soft_connect` attempt below goes through the driver's
> OTG-gated pull-up path and silently no-ops; `devmem` bypasses it.** Original (now-corrected) note:
> **Thought we'd exhausted the userspace levers (2026-06), incl. the OTG angle.** Matches a known `dwc2`
> device-mode "not attached" bug class (RPi kernel issues #5942/#2684/#5532, PR #3151 — VBUS/session +
> connect/disconnect state machine). The UDC is in **OTG/dual-role mode** (`/sys/class/udc/8000000.usb/
> is_otg = 1`), so the pull-up/connect is gated by the OTG state machine. We tried everything reachable
> from userspace, all → `not attached`, no reset/enumeration, host sees nothing: **(a)** clean boot at
> uptime 2 s (fresh VBUS) — not just mid-session; **(b)** forcing the pull-up via
> `echo disconnect/connect > /sys/class/udc/8000000.usb/soft_connect` — no change (← THIS is the gated
> path; the working fix is `devmem 0x8000804 32 0` instead). Meanwhile the ECM gadget attaches
> (`state = configured`) in the *same* OTG mode. We concluded the block was in the kernel `dwc2`
> OTG/connect path below userspace — but it's reachable from userspace after all via the raw DCTL
> register. ~~Definitive dead end~~ → **solved by the direct pull-up write; no kernel/devicetree patch
> needed, so the RSA-signed firmware is not a blocker.** ([device/uvc-up.sh](../device/uvc-up.sh))

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
