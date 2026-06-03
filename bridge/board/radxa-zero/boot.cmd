# U-Boot boot script for the Radxa Zero (compiled to boot.scr by post-image.sh).
# Mainline U-Boot's distro-boot scans the FAT boot partition and runs this.
# NOTE(bring-up): verify the mmc device index on hardware — the SD card may be mmc 0 or mmc 1,
# and the rootfs may be mmcblk0p2 or mmcblk1p2. Adjust here + in bridge.conf once you see the console.
setenv bootargs "console=ttyAML0,115200 root=/dev/mmcblk0p2 rootwait rw"
load mmc 0:1 ${kernel_addr_r} Image
load mmc 0:1 ${fdt_addr_r} meson-g12a-radxa-zero.dtb
booti ${kernel_addr_r} - ${fdt_addr_r}
