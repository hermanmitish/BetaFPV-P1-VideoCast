# U-Boot boot script for the Radxa Zero (compiled to boot.scr by post-image.sh).
# U-Boot vs Linux number MMC differently: in U-Boot the microSD is "mmc 1" (load the kernel from
# there), but in Linux it comes up as "mmcblk0" (the eMMC probe is deferred). So root=mmcblk0p2.
setenv bootargs "console=ttyAML0,115200 root=/dev/mmcblk0p2 rootwait rw"
load mmc 1:1 ${kernel_addr_r} Image
load mmc 1:1 ${fdt_addr_r} meson-g12a-radxa-zero.dtb
booti ${kernel_addr_r} - ${fdt_addr_r}
