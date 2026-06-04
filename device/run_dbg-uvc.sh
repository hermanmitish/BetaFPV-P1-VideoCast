#!/bin/sh
# run_dbg-uvc.sh — boot the non-Pro P1 HD goggle as a NATIVE USB webcam (UVC) to the host.
#
# Deploy at /usrdata/run_dbg.sh (run.sh runs it instead of stock boot when buildtimes match). This uses
# the goggle's BUILT-IN UVC support — arlink_fpv pumps the composited display (video+OSD+UI) into the UVC
# /dev/videoN when the uvc service is enabled — plus THE FIX that finally makes the dwc2 host-enumerate a
# UVC gadget: the controller parks its pull-up OFF when UVC binds (DCTL.SftDiscon=1, UDC stuck
# "not attached"); we force a disconnect->reconnect EDGE on DCTL so the OTG host re-detects us.
# UVC must be the ONLY gadget function here (the dwc2 won't enumerate a multi-function composite w/ UVC).
# Console/recovery is always the UART; to revert, restore device/run_dbg.sh as /usrdata/run_dbg.sh.

G=/sys/kernel/config/usb_gadget/g1
UDC=8000000.usb
DCTL=0x8000804
W=1920; H=1080; FPS=30; MAXPKT=1024

# enable the RX pipeline (same cfg as the ECM/RTSP boot; the app needs it to decode+composite the feed)
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

# ---- build a UVC-only gadget (MJPEG; match the goggle's native uvc_hdr default of 1080p) ----
mount -t configfs none /sys/kernel/config 2>/dev/null
mkdir -p $G
echo 0x1d6b > $G/idVendor
echo 0x4002 > $G/idProduct   # distinct PID so the host treats UVC as a NEW device (not the cached ECM 0x0104)
echo 0x0200 > $G/bcdUSB
echo 0xEF > $G/bDeviceClass; echo 0x02 > $G/bDeviceSubClass; echo 0x01 > $G/bDeviceProtocol
mkdir -p $G/strings/0x409
echo ZBBM5DZFMP      > $G/strings/0x409/serialnumber
echo Artosyn         > $G/strings/0x409/manufacturer
echo "ArtLynk Goggle" > $G/strings/0x409/product
mkdir -p $G/configs/c.1/strings/0x409
echo UVC > $G/configs/c.1/strings/0x409/configuration
echo 250 > $G/configs/c.1/MaxPower
U=$G/functions/uvc.0
mkdir -p $U/control/header/h
ln -s $U/control/header/h $U/control/class/fs/h
ln -s $U/control/header/h $U/control/class/ss/h
F=$U/streaming/mjpeg/m/f
mkdir -p $F
echo $W > $F/wWidth
echo $H > $F/wHeight
echo $((W*H*2)) > $F/dwMaxVideoFrameBufferSize
printf '%s\n' $((10000000/FPS)) > $F/dwFrameInterval
mkdir -p $U/streaming/header/h
ln -s $U/streaming/mjpeg/m $U/streaming/header/h/m
ln -s $U/streaming/header/h $U/streaming/class/fs/h
ln -s $U/streaming/header/h $U/streaming/class/hs/h
ln -s $U/streaming/header/h $U/streaming/class/ss/h
echo $MAXPKT > $U/streaming_maxpacket 2>/dev/null
ln -s $U $G/configs/c.1/uvc.0
echo $UDC > $G/UDC

# ---- THE FIX: dwc2 parks its pull-up OFF for UVC. The OTG state machine makes a single
#      disconnect->reconnect edge intermittent, so RETRY the edge until the host enumerates us. ----
sleep 2
i=0
while [ $i -lt 20 ]; do
	devmem $DCTL 32 0x00000002   # SftDiscon=1 (disconnect)
	sleep 1
	devmem $DCTL 32 0x00000000   # SftDiscon=0 (reconnect / pull-up ON)
	sleep 2
	[ "$(cat /sys/class/udc/$UDC/state)" = "configured" ] && { echo "uvc: ENUMERATED after $i retries"; break; }
	i=$((i+1))
done
echo "uvc: pull-up loop done, state=$(cat /sys/class/udc/$UDC/state)"

# ---- rest of the stock GND boot (so the receiver runs and produces video for the UVC feed) ----
echo "/tmp/sdcard/core-%e-%p-%s" > /proc/sys/kernel/core_pattern
ulimit -c unlimited
uinput_proxy -m 2 &
if [ -e "/usr/usrdata/start_fb.sh" ]; then
	source /usr/usrdata/start_fb.sh
fi
if [ -e "/usr/usrdata/ar813x/start_ar813x_gnd.sh" ]; then
	/usr/usrdata/ar813x/start_ar813x_gnd.sh
	if [ $? -eq 0 ]; then
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

# ---- enable the goggle's NATIVE UVC video feed: arlink_fpv pumps the composited display to /dev/videoN ----
sleep 3
bdcmd ar_lowdelay_rx -uvc_ctrl 1

sleep 1
sync
echo 1 > /proc/sys/vm/drop_caches
ota_upgrade &
