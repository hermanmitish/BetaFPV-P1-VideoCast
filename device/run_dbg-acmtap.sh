#!/bin/sh
# run_dbg-acmtap.sh — EXPERIMENT: low-latency video-out by teeing the received H.265
# straight to a USB CDC-ACM port (no venc8 re-encode, no RTSP).
#
# Gadget = two ACMs:  acm.usb0 -> /dev/ttyGS0 (left for the app),
#                     acm.usb1 -> /dev/ttyGS1 (our video tap; 2nd /dev/cu.usbmodem on host).
# arlink_fpv is started under LD_PRELOAD=/usrdata/libvideotap.so, which copies each received
# H.265 frame to /dev/ttyGS1. Host: cat that /dev/cu.usbmodem* | ffplay -i -
#
# (No ECM/RTSP here — this is the pure ACM-tap test. Console stays on the UART.)
servicemanager &
mount -t configfs none /sys/kernel/config 2>/dev/null
G=/sys/kernel/config/usb_gadget/g1
mkdir -p $G
echo 0x1d6b > $G/idVendor
echo 0x0104 > $G/idProduct
echo 0x0200 > $G/bcdUSB
mkdir -p $G/strings/0x409
echo ZBBM5DZFMP > $G/strings/0x409/serialnumber
echo Artosyn    > $G/strings/0x409/manufacturer
echo Sirius     > $G/strings/0x409/product
mkdir -p $G/configs/c.1/strings/0x409
echo ecm-acm-acm > $G/configs/c.1/strings/0x409/configuration
echo 250 > $G/configs/c.1/MaxPower
mkdir -p $G/functions/ecm.usb0
echo 02:00:00:55:00:01 > $G/functions/ecm.usb0/dev_addr
echo 02:00:00:55:00:02 > $G/functions/ecm.usb0/host_addr
mkdir -p $G/functions/acm.usb0
mkdir -p $G/functions/acm.usb1
mkdir -p $G/functions/acm.usb2
ln -s $G/functions/ecm.usb0 $G/configs/c.1/
ln -s $G/functions/acm.usb0 $G/configs/c.1/
ln -s $G/functions/acm.usb1 $G/configs/c.1/
ln -s $G/functions/acm.usb2 $G/configs/c.1/
echo 8000000.usb > $G/UDC
sleep 1
ifconfig usb0 10.55.0.1 netmask 255.255.255.0 up
echo "/tmp/sdcard/core-%e-%p-%s" > /proc/sys/kernel/core_pattern
ulimit -c unlimited
uinput_proxy -m 2 &
if [ -e "/usr/usrdata/start_fb.sh" ]; then
	source /usr/usrdata/start_fb.sh
fi
if [ -e "/usr/usrdata/ar813x/start_ar813x_gnd.sh" ]; then
	/usr/usrdata/ar813x/start_ar813x_gnd.sh
	if [ $? -ne 0 ]; then
		echo "start_ar813x_gnd.sh failed."
	else
		sleep 0.5
		LD_PRELOAD=/usrdata/libvideotap.so arlink_fpv -m 2 -t 0 >/dev/null &
		sleep 2
		arlink_daemon &
		ar_logcat -f /tmp/usrlog/rx.log 122880 10 &
	fi
	if [ -e "/usr/usrdata/start_ui.sh" ]; then
		source /usr/usrdata/start_ui.sh
	fi
fi
sleep 1
sync
echo 1 > /proc/sys/vm/drop_caches
ota_upgrade &
