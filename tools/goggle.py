#!/usr/bin/env python3
"""
goggle.py — talk to the P1 HD goggle's DEBUG UART console over a USB-TTL adapter.

The goggle console is ttyS0 @ 1228800 baud (the /proc/cmdline value of 1152000 is
nominal; the kernel/shell actually run at 1228800). inittab has `::askfirst:-/bin/sh`,
so pressing Enter gives a root shell.

The link drops bytes on long bursts, so uploads disable device echo and send
byte-paced, then verify with md5 (retried).

Usage:
  serial.py [--port P] [--baud 1228800] run "<cmd>"
  serial.py [--port P] upload <localfile> <remotepath>
  serial.py [--port P] reboot
  serial.py [--port P] shell        # interactive-ish: type a line, see output

If --port is omitted it auto-picks the first /dev/cu.usbserial* / /dev/cu.wchusbserial*.
"""
import sys, time, glob, hashlib, argparse
try:
    import serial
except ImportError:
    sys.exit("pip install pyserial  (e.g. in a venv)")

def find_port():
    for pat in ("/dev/cu.usbserial*", "/dev/cu.wchusbserial*", "/dev/ttyUSB*"):
        m = sorted(glob.glob(pat))
        if m:
            return m[0]
    sys.exit("no USB-serial port found; pass --port")

class Goggle:
    def __init__(self, port, baud):
        self.s = serial.Serial(port, baud, timeout=0.1)
    def _drain(self, t=0.8):
        out = b""; end = time.time() + t
        while time.time() < end:
            c = self.s.read(8192)
            if c:
                out += c; end = time.time() + 0.25
        return out.decode("utf-8", "replace")
    def _slow(self, b, per=0.0016):
        for i in range(len(b)):
            self.s.write(b[i:i+1]); self.s.flush(); time.sleep(per)
    def cmd(self, c, t=1.5):
        self.s.reset_input_buffer()
        self._slow(c.encode() + b"\r")
        return self._drain(t)
    def wake(self):
        self.s.write(b"\x03\r"); self.s.flush(); self._drain(0.4)
    def upload(self, local, remote):
        data = open(local, "rb").read()
        want = hashlib.md5(data).hexdigest()
        self.wake(); self.cmd("stty -echo")
        for attempt in range(1, 6):
            self.cmd(f"rm -f {remote}")
            self._slow(f"cat > {remote} << 'RDEOF'\r".encode()); time.sleep(0.2); self.s.read(8192)
            self._slow(data); self._slow(b"RDEOF\r"); self._drain(1.0)
            out = self.cmd(f"md5sum {remote}", 2.0)
            got = next((tok for tok in out.split()
                        if len(tok) == 32 and all(ch in "0123456789abcdef" for ch in tok)), "")
            print(f"  upload attempt {attempt}: {'OK' if got == want else 'mismatch, retrying'}")
            if got == want:
                self.cmd("stty echo")
                return True
        self.cmd("stty echo")
        return False
    def reboot(self):
        self.wake(); self._slow(b"reboot\r")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port"); ap.add_argument("--baud", type=int, default=1228800)
    sub = ap.add_subparsers(dest="action", required=True)
    sub.add_parser("reboot"); sub.add_parser("shell")
    r = sub.add_parser("run"); r.add_argument("cmd")
    u = sub.add_parser("upload"); u.add_argument("local"); u.add_argument("remote")
    a = ap.parse_args()
    g = Goggle(a.port or find_port(), a.baud)
    if a.action == "run":
        g.wake(); print(g.cmd(a.cmd, 2.5), end="")
    elif a.action == "upload":
        ok = g.upload(a.local, a.remote)
        print("UPLOAD OK" if ok else "UPLOAD FAILED"); sys.exit(0 if ok else 1)
    elif a.action == "reboot":
        g.reboot(); print("rebooting...")
    elif a.action == "shell":
        g.wake(); print("type commands (Ctrl-D to quit):")
        for line in sys.stdin:
            print(g.cmd(line.rstrip("\n"), 2.0), end="")

if __name__ == "__main__":
    main()
