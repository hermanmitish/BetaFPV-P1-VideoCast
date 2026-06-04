#!/bin/sh
# Hammer the dwc2 pull-up disconnect->reconnect edge until the host enumerates the UVC gadget.
# The Artosyn dwc2's OTG state machine makes a single edge intermittent; retrying catches the window.
DCTL=0x8000804
UDC=8000000.usb
: > /tmp/uvcr.log
i=0
while [ $i -lt 30 ]; do
	devmem $DCTL 32 0x00000002   # disconnect
	sleep 1
	devmem $DCTL 32 0x00000000   # reconnect / pull-up on
	sleep 2
	s=$(cat /sys/class/udc/$UDC/state)
	echo "try $i: state=$s" >> /tmp/uvcr.log
	if [ "$s" = "configured" ]; then echo "*** ENUMERATED at try $i ***" >> /tmp/uvcr.log; break; fi
	i=$((i+1))
done
echo "done: state=$(cat /sys/class/udc/$UDC/state)" >> /tmp/uvcr.log
