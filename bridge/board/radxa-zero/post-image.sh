#!/bin/sh
# Runs after the rootfs build. Produces a bootable output/images/sdcard.img:
#   1. compile the U-Boot boot script (boot.cmd -> boot.scr)
#   2. pack the Amlogic signed bootloader (FIP) around mainline u-boot.bin
#   3. genimage: bootloader-at-offset + FAT boot + ext4 rootfs
# $1 = path to genimage.cfg (from BR2_ROOTFS_POST_SCRIPT_ARGS).
set -e
BOARD_DIR="$(dirname "$0")"
GENIMAGE_CFG="${1:-$BOARD_DIR/genimage.cfg}"
MKIMAGE="${HOST_DIR}/bin/mkimage"

# 1) boot.cmd -> boot.scr
echo "[post-image] building boot.scr"
"$MKIMAGE" -C none -A arm64 -T script -d "$BOARD_DIR/boot.cmd" "$BINARIES_DIR/boot.scr"

# 2) Amlogic bootloader (FIP). Mainline u-boot.bin is NOT bootable alone on Amlogic — it must be
#    wrapped with the signed BL2/BL31/BL30 blobs. LibreELEC's amlogic-boot-fip has the Radxa Zero
#    blobs + packer and emits u-boot.bin.sd.bin (written to sector 1 of the card).
#
#    NOTE(apple-silicon): the FIP packer ships x86_64 tools; on an arm64 Docker host they need x86
#    emulation (Docker Desktop "Use Rosetta" / binfmt). If this step fails on your Mac, either run it
#    on an x86 box/CI, or drop a known-good Radxa/Armbian "u-boot.bin.sd.bin" into ${BINARIES_DIR}
#    beforehand — this script will then skip the packing.
if [ ! -f "${BINARIES_DIR}/u-boot.bin.sd.bin" ]; then
	echo "[post-image] packing Amlogic FIP (radxa-zero)"
	FIP="${BUILD_DIR}/amlogic-boot-fip"
	[ -d "$FIP/.git" ] || git clone --depth 1 https://github.com/LibreELEC/amlogic-boot-fip "$FIP"
	rm -rf "${BINARIES_DIR}/fip"
	"$FIP/build-fip.sh" radxa-zero "${BINARIES_DIR}/u-boot.bin" "${BINARIES_DIR}/fip"
	cp "${BINARIES_DIR}/fip/u-boot.bin.sd.bin" "${BINARIES_DIR}/u-boot.bin.sd.bin"
fi

# 3) assemble the SD image
echo "[post-image] running genimage"
GENIMAGE_TMP="${BUILD_DIR}/genimage.tmp"
rm -rf "${GENIMAGE_TMP}"
genimage \
	--rootpath "${TARGET_DIR}" \
	--tmppath "${GENIMAGE_TMP}" \
	--inputpath "${BINARIES_DIR}" \
	--outputpath "${BINARIES_DIR}" \
	--config "${GENIMAGE_CFG}"

echo "[post-image] wrote ${BINARIES_DIR}/sdcard.img"
