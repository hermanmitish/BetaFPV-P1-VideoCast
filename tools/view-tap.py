#!/usr/bin/env python3
"""view-tap.py [CHN0_PORT CHN1_PORT] — low-latency full-frame view from the raw VDEC tap.

The air unit sends two low-delay strips (VDEC chn0 -> ttyGS1, chn1 -> ttyGS2). This drains
BOTH USB-ACM ports continuously from the moment it starts (each in its own thread, like the
working single-stream case), pipes them into one ffmpeg that vstacks them, and pipes that to
ffplay. No FIFOs, so no startup race.

Workflow: start this FIRST, then power the drone — the tap begins each channel at a clean
keyframe when the link comes up, and the readers are already draining to catch it.

Auto-detects ports: with the ecm/acm gadget the lowest /dev/cu.usbmodem* is the app port,
the next two are chn0 then chn1. Pass both explicitly to override. Requires pyserial + ffmpeg.
"""
import os, sys, glob, threading, subprocess, serial

def pick_ports():
    p = sorted(glob.glob("/dev/cu.usbmodem*"))
    if len(p) < 3:
        sys.exit(f"expected >=3 usbmodem ports (gadget with 2 video ACMs); found {len(p)}: {p}")
    return p[-2], p[-1]

P0, P1 = (sys.argv[1], sys.argv[2]) if len(sys.argv) >= 3 else pick_ports()
# chn0 (top, 1920x560) and chn1 (bottom, 1920x552) share a vertical overlap band; crop it
# off the bottom of chn0 so the stack is a clean 1920x1080. Tune with TAP_OVERLAP if needed.
OVERLAP = int(os.environ.get("TAP_OVERLAP", "32"))
filt = (f"[0:v]crop=iw:ih-{OVERLAP}:0:0[t];[t][1:v]vstack=inputs=2,format=yuv420p"
        if OVERLAP > 0 else "[0:v][1:v]vstack=inputs=2,format=yuv420p")
print(f"[*] chn0={P0}  chn1={P1}  overlap={OVERLAP}px   (q/Esc in the window to quit)")

r0, w0 = os.pipe(); r1, w1 = os.pipe()
for fd in (r0, r1):
    os.set_inheritable(fd, True)

ff = subprocess.Popen(
    ["ffmpeg", "-hide_banner", "-loglevel", "warning",
     "-f", "hevc", "-i", f"pipe:{r0}",
     "-f", "hevc", "-i", f"pipe:{r1}",
     "-filter_complex", filt,
     "-c:v", "rawvideo", "-f", "nut", "pipe:1"],
    stdout=subprocess.PIPE, pass_fds=(r0, r1))
play = subprocess.Popen(
    ["ffplay", "-hide_banner", "-fflags", "nobuffer", "-flags", "low_delay", "-i", "-"],
    stdin=ff.stdout)
os.close(r0); os.close(r1); ff.stdout.close()

def pump(port, wfd):
    s = serial.Serial(port, 115200, timeout=0.2)   # baud irrelevant for USB-ACM
    while True:
        d = s.read(65536)
        if d:
            try:
                os.write(wfd, d)
            except (BrokenPipeError, OSError):
                break

for port, wfd in ((P0, w0), (P1, w1)):
    threading.Thread(target=pump, args=(port, wfd), daemon=True).start()

try:
    play.wait()
finally:
    ff.terminate()
