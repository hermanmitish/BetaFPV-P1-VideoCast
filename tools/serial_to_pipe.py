#!/usr/bin/env python3
"""serial_to_pipe.py PORT — read a USB-ACM port raw and stream its bytes to stdout.
Used by view-tap.sh to feed the two raw-H.265 tap ports into ffmpeg."""
import serial, sys
s = serial.Serial(sys.argv[1], 115200, timeout=1)   # baud is irrelevant for USB-ACM
while True:
    d = s.read(65536)
    if d:
        sys.stdout.buffer.write(d)
        sys.stdout.flush()
