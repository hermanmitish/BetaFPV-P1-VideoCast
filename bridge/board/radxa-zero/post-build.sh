#!/bin/sh
# Bridge post-build fixups on the assembled rootfs ($1 = TARGET_DIR), run before the image is packed.
set -e
TARGET="$1"

# --- WiFi nvram name fix -----------------------------------------------------
# The Radxa Zero's on-board module is the Ampak AP6212 (Broadcom BCM43430). linux-firmware ships its
# nvram as brcmfmac43430-sdio.AP6212.txt, but brcmfmac asks for brcmfmac43430-sdio.<board>.txt then
# brcmfmac43430-sdio.txt — neither of which exists, so the chip never inits ("HT Avail timeout") and
# there's no wlan0. Install the AP6212 nvram under the names the driver requests. (Firmware blob itself
# is fine: brcmfmac43430-sdio.bin -> cypress/cyfmac43430-sdio.bin, both present.)
BRCM="$TARGET/lib/firmware/brcm"
if [ -f "$BRCM/brcmfmac43430-sdio.AP6212.txt" ]; then
	cp -f "$BRCM/brcmfmac43430-sdio.AP6212.txt" "$BRCM/brcmfmac43430-sdio.txt"
	cp -f "$BRCM/brcmfmac43430-sdio.AP6212.txt" "$BRCM/brcmfmac43430-sdio.radxa,zero.txt"
	echo "[post-build] WiFi: installed AP6212 nvram as brcmfmac43430-sdio.txt (+ radxa,zero.txt)"
else
	echo "[post-build] WARN: AP6212 nvram missing — WiFi will not come up (check linux-firmware BRCM opts)"
fi

# --- Remove stale S91uvcfeed (its job moved into S30usbgadget) ----------------
# Buildroot's rootfs-overlay only adds/overwrites; a file deleted from the overlay still lingers in the
# target from an earlier build. S30 now starts the single uvc-gadget, so drop the leftover S91 service.
rm -f "$TARGET/etc/init.d/S91uvcfeed"
