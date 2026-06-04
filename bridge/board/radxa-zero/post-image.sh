#!/bin/sh
# Runs after the rootfs build. Produces a bootable output/images/sdcard.img:
#   1. compile the U-Boot boot script (boot.cmd -> boot.scr)
#   2. pack the Amlogic signed bootloader (FIP) around mainline u-boot.bin
#   3. genimage: bootloader-at-offset + FAT boot + ext4 rootfs
# $1 = path to genimage.cfg (from BR2_ROOTFS_POST_SCRIPT_ARGS).
set -e
BOARD_DIR="$(dirname "$0")"
# NB: for POST_IMAGE scripts Buildroot passes $1 = BINARIES_DIR (a directory), NOT our args — so use
# the genimage.cfg sitting next to this script directly (passing $1 as --config was the flex error).
GENIMAGE_CFG="$BOARD_DIR/genimage.cfg"
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
	mkdir -p "${BINARIES_DIR}/fip"          # bootmk writes its output here — must exist
	# build-fip.sh validates the board via `[ -d <board> ]` relative to its CWD, so run it from $FIP
	( cd "$FIP" && ./build-fip.sh radxa-zero "${BINARIES_DIR}/u-boot.bin" "${BINARIES_DIR}/fip" )
	cp "${BINARIES_DIR}/fip/u-boot.bin.sd.bin" "${BINARIES_DIR}/u-boot.bin.sd.bin"
fi

# 3) assemble the SD image
echo "[post-image] running genimage"
GENIMAGE_TMP="${BUILD_DIR}/genimage.tmp"
rm -rf "${GENIMAGE_TMP}"
# genimage's flex parser can fail reading the config straight off the macOS Docker bind mount
# ("input in flex scanner failed") — copy it to the native build dir first.
CFG_LOCAL="${BUILD_DIR}/genimage.cfg.local"
rm -rf "${CFG_LOCAL}"                    # clear any stale copy (an earlier bug made it a directory)
cp "${GENIMAGE_CFG}" "${CFG_LOCAL}"
genimage \
	--rootpath "${TARGET_DIR}" \
	--tmppath "${GENIMAGE_TMP}" \
	--inputpath "${BINARIES_DIR}" \
	--outputpath "${BINARIES_DIR}" \
	--config "${CFG_LOCAL}"

# Embed the Amlogic bootloader the way the boot ROM expects: rest-from-sector-1, then the first 444
# bytes at sector 0 (leaving the MBR partition table at 446..509 intact). A single genimage
# offset=512 partition only wrote the sector-1 half, so the ROM never found a valid bootloader.
SD="${BINARIES_DIR}/sdcard.img"
UBOOT_SD="${BINARIES_DIR}/u-boot.bin.sd.bin"
dd if="${UBOOT_SD}" of="${SD}" conv=fsync,notrunc bs=512 skip=1 seek=1
dd if="${UBOOT_SD}" of="${SD}" conv=fsync,notrunc bs=1 count=444
echo "[post-image] embedded Amlogic bootloader into ${SD}"

echo "[post-image] wrote ${BINARIES_DIR}/sdcard.img"
