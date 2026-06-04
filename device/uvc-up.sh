#!/bin/sh
# uvc-up.sh — present the BetaFPV P1 HD goggle (Artosyn AR9301, dwc2) as a USB webcam (UVC) to the host.
#
# *** THE KEY FINDING (2026-06) ***
# The Artosyn/Amlogic dwc2 controller PARKS ITS USB PULLUP OFF the moment a UVC function is bound
# (device register DCTL bit1 SftDiscon=1), so the host never sees a connect and never enumerates it —
# this is the "uvc gadget binds but UDC stays 'not attached'" dead-end we hit for months. ECM/RNDIS-only
# gadgets connect fine. THE FIX: after binding the gadget, assert the pullup by clearing SftDiscon:
#     devmem <dwc2_base + 0x804> 32 0x00000000
# Proven on the Radxa Zero's dwc2; this script applies the same fix on the goggle.
#
# Usage (from a UART/serial shell, since this replaces the ECM/telnet gadget):
#     sh /usrdata/uvc-up.sh
# Then look for "ArtLynk Goggle" in the Mac's camera list (Photo Booth / QuickTime / Zoom).
# NOTE: the camera enumerates but shows BLACK until a frame feeder pumps MJPEG into the f_uvc node
# (/dev/videoN) — that's the next step (see device/mjpegtap.c + a uvc-gadget handler).

W=1280; H=720; FPS=30; MAXPKT=1024
G=/sys/kernel/config/usb_gadget/g1

# Stop the app + keep the watchdog fed BEFORE touching the gadget. Live-unbinding the UDC while
# arlink_fpv is running has oopsed the kernel before; killing it first is the safe pattern. (This
# also stops video output — fine for an enumeration test; frame-feeding is a later step.)
# The feeder is detached with setsid so it persists after this script returns (else the watchdog,
# no longer fed by arlink, reboots the goggle).
echo "uvc-up: stopping app + holding watchdog..."
killall -9 arlink_daemon arlink_fpv 2>/dev/null || true
[ -e /dev/watchdog0 ] && setsid sh -c 'while :; do printf "\0" > /dev/watchdog0; sleep 2; done' </dev/null >/dev/null 2>&1 &
sleep 1

UDC=$(ls /sys/class/udc/ 2>/dev/null | head -n1)
[ -z "$UDC" ] && { echo "uvc-up: no UDC found"; exit 1; }
# DCTL = dwc2 base + 0x804; the UDC name encodes the base, e.g. "8000000.usb" -> 0x8000000
BASE=0x$(echo "$UDC" | sed 's/\.usb$//')
DCTL=$(printf '0x%x' $(( BASE + 0x804 )))
echo "uvc-up: UDC=$UDC  dwc2 base=$BASE  DCTL=$DCTL"
command -v devmem >/dev/null 2>&1 || echo "uvc-up: WARNING — no 'devmem'; the pullup fix needs it (or a small mmap helper)"

mount -t configfs none /sys/kernel/config 2>/dev/null || true
# unbind whatever gadget is currently bound (run_dbg's ECM+ACM), then (re)build as UVC ONLY
[ -e "$G/UDC" ] && echo "" > "$G/UDC" 2>/dev/null || true
# strip existing function links from the config — the goggle's dwc2 won't enumerate a 3-function
# ecm+acm+uvc composite (too many endpoints/FIFO); present UVC alone.
[ -d "$G/configs/c.1" ] && for l in "$G"/configs/c.1/*; do [ -L "$l" ] && rm -f "$l"; done
mkdir -p "$G"
echo 0x1d6b > "$G/idVendor"
echo 0x0104 > "$G/idProduct"
echo 0x0200 > "$G/bcdUSB"
# UVC interfaces are grouped by an Interface Association Descriptor — the DEVICE descriptor must declare
# it (class 0xEF / 0x02 / 0x01) or strict hosts balk.
echo 0xEF > "$G/bDeviceClass"
echo 0x02 > "$G/bDeviceSubClass"
echo 0x01 > "$G/bDeviceProtocol"
mkdir -p "$G/strings/0x409"
echo ZBBM5DZFMP      > "$G/strings/0x409/serialnumber"
echo Artosyn         > "$G/strings/0x409/manufacturer"
echo "ArtLynk Goggle" > "$G/strings/0x409/product"
mkdir -p "$G/configs/c.1/strings/0x409"
echo "ECM+UVC" > "$G/configs/c.1/strings/0x409/configuration"
echo 250 > "$G/configs/c.1/MaxPower"

# --- ECM function (this is the key difference from UVC-only: matches the WORKING Radxa composite. The
#     ECM connect makes the dwc2 actually drive D+/assert the pull-up; UVC rides along. UVC-ALONE on the
#     goggle's OTG dwc2 leaves the pull-up parked and never enumerates. Just 2 functions = fits the EPs.) ---
mkdir -p "$G/functions/ecm.usb0"
echo "02:00:00:55:00:01" > "$G/functions/ecm.usb0/dev_addr"
echo "02:00:00:55:00:02" > "$G/functions/ecm.usb0/host_addr"
ln -sf "$G/functions/ecm.usb0" "$G/configs/c.1/"

# --- UVC function: one MJPEG format, one frame ---
U="$G/functions/uvc.0"
mkdir -p "$U"
mkdir -p "$U/control/header/h"
ln -sf "$U/control/header/h" "$U/control/class/fs/h"
ln -sf "$U/control/header/h" "$U/control/class/ss/h"
F="$U/streaming/mjpeg/m/f"
mkdir -p "$F"
echo "$W" > "$F/wWidth"
echo "$H" > "$F/wHeight"
echo $(( W * H * 2 )) > "$F/dwMaxVideoFrameBufferSize"
printf '%s\n' $(( 10000000 / FPS )) > "$F/dwFrameInterval"
mkdir -p "$U/streaming/header/h"
ln -sf "$U/streaming/mjpeg/m" "$U/streaming/header/h/m"
ln -sf "$U/streaming/header/h" "$U/streaming/class/fs/h"
ln -sf "$U/streaming/header/h" "$U/streaming/class/hs/h"
ln -sf "$U/streaming/header/h" "$U/streaming/class/ss/h"
echo "$MAXPKT" > "$U/streaming_maxpacket" 2>/dev/null || true
ln -sf "$U" "$G/configs/c.1/uvc.0"

echo "uvc-up: binding $UDC ..."
echo "$UDC" > "$G/UDC"
sleep 1
echo "uvc-up: state after bind = $(cat /sys/class/udc/$UDC/state)  DCTL=$(devmem $DCTL 2>/dev/null)"
# *** THE FIX ***  the dwc2 parks its pull-up OFF when UVC binds (DCTL.SftDiscon=1). Force a clean
# disconnect->reconnect EDGE so the host (OTG state machine) re-detects us — clearing the bit once is
# enough on some dwc2, but the goggle's needs the explicit edge with a real pause.
devmem "$DCTL" 32 0x00000002   # SftDiscon=1 -> disconnect
sleep 2
devmem "$DCTL" 32 0x00000000   # SftDiscon=0 -> reconnect / pull-up ON
sleep 3
echo "uvc-up: state AFTER cycle = $(cat /sys/class/udc/$UDC/state)  DCTL=$(devmem $DCTL 2>/dev/null)"
echo "uvc-up: video node(s):"; for v in /sys/class/video4linux/video*; do [ -e "$v" ] && echo "    $(basename "$v"): $(cat "$v/name" 2>/dev/null)"; done
echo "uvc-up: done — look for 'ArtLynk Goggle' in the Mac camera list (state should be 'configured')"
