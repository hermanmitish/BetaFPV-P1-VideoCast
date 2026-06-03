#!/bin/sh
# run_dbg.sh — custom GND-goggle boot that adds USB video-out to the Mac.
#
# Placed at /usrdata/run_dbg.sh on the goggle; /usr/usrdata/run.sh runs THIS
# instead of the normal boot when /usrdata/buildtime == /usr/usrdata/buildtime
# (the deploy script copies buildtime so that matches).
#
# What it changes vs stock boot:
#   1. Replaces the stock RNDIS USB gadget with a CDC-ECM one (macOS speaks ECM
#      natively; it does NOT speak RNDIS). Goggle = 10.55.0.1, host gets 10.55.0.2.
#   2. Enables ONLY the firmware's encode-RTSP server (RxEncRtspEnable). Do NOT
#      also set RxRtspEnable — enabling both makes two servers fight over port 554
#      and one spins accept() on a dead fd, flooding the log ("Accept connection
#      error: Bad file descriptor").
# Then it runs the rest of the normal GND boot so the goggle works as usual.
#
# Stream URL once a drone is bound:  rtsp://10.55.0.1:554/venc8/stream
#
# Tunables:
GOGGLE_IP=10.55.0.1          # host should be 10.55.0.2 / 255.255.255.0
HOST_MAC=02:00:00:55:00:02   # fixed so the macOS adapter keeps its IP across reboots
DEV_MAC=02:00:00:55:00:01

# Enable ONLY the encode-RTSP server. Write the whole cfg_rx_sys.json rather than sed it,
# so it works even on a fresh /usrdata (just wiped by a firmware upgrade) where the app
# hasn't created the file yet. The app honors an existing config and won't overwrite it.
CFGDIR=/usrdata/lowdelay/lowdelay_cfg
mkdir -p $CFGDIR
cat > $CFGDIR/cfg_rx_sys.json <<'JSON'
{
	"RX_SYS":	{
		"LdPipeEnable":	1,
		"RxPipeEnable":	1,
		"RxRtspEnable":	0,
		"RxEncRtspEnable":	1,
		"u32CryptoEnable":	0,
		"u32CryptoType":	0,
		"u32CryptoSlice":	2
	}
}
JSON

servicemanager &

# ---- build the ECM + ACM USB gadget by hand (vendor script has no 'ecm' verb) ----
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
echo ecm-acm > $G/configs/c.1/strings/0x409/configuration
echo 250 > $G/configs/c.1/MaxPower
mkdir -p $G/functions/ecm.usb0
mkdir -p $G/functions/acm.usb0
echo $DEV_MAC  > $G/functions/ecm.usb0/dev_addr
echo $HOST_MAC > $G/functions/ecm.usb0/host_addr
ln -s $G/functions/ecm.usb0 $G/configs/c.1/
ln -s $G/functions/acm.usb0 $G/configs/c.1/
echo 8000000.usb > $G/UDC
sleep 1
ifconfig usb0 $GOGGLE_IP netmask 255.255.255.0 up

# ---- remote shell over the same USB link (telnet @ 10.55.0.1:23, root /bin/sh) ----
# Lets us deploy/run without the UART: `tools/goggle-net.py run/push`. Safe on this
# point-to-point USB net; BusyBox telnetd, login shell directly (no password).
telnetd -l /bin/sh -p 23 &

# ---- rest of the stock GND boot ----
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
		arlink_fpv -m 2 -t 0 >/dev/null &
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
