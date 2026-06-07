#!/usr/bin/env python3
"""
goggle-net.py — talk to the P1 HD goggle over the USB ECM network (no UART needed).

Far faster than the byte-paced UART (tools/goggle-uart.py): commands go over telnet, file pushes
go over HTTP (the goggle wget's from a tiny server we run on the Mac's ECM IP). Works alongside
RTSP on the same usb0 link.

Requires telnetd running on the goggle (the RTSP boot config `device/run_dbg.sh` starts it:
`telnetd -l /bin/sh -p 23 &`). The Mac's ECM adapter must be 10.55.0.2/24 (same as for RTSP).

Usage:
  goggle-net.py run "<cmd>"
  goggle-net.py push <localfile> <remotepath>
  goggle-net.py shell
Options: --host 10.55.0.1  --httpip 10.55.0.2
"""

import sys, socket, time, argparse, hashlib, os, tempfile, shutil, threading, re
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler


def _negotiate(sock, data, out):
    """Strip/answer telnet IAC sequences; append plain bytes to out."""
    i = 0
    while i < len(data):
        if data[i] == 255 and i + 2 < len(data):  # IAC
            ch, opt = data[i + 1], data[i + 2]
            if ch == 253:
                sock.sendall(bytes([255, 252, opt]))  # DO  -> WONT
            elif ch == 251:
                sock.sendall(bytes([255, 254, opt]))  # WILL-> DONT
            i += 3
            continue
        out.append(data[i])
        i += 1


class Net:
    def __init__(self, host, httpip):
        self.host = host
        self.httpip = httpip

    def _connect(self):
        s = socket.create_connection((self.host, 23), timeout=5)
        s.settimeout(0.5)
        return s

    def run(self, cmd, wait=8.0):
        """Run a command, return (stdout_text, rc). Uses an expanded sentinel to skip the echo."""
        s = self._connect()
        buf = bytearray()

        def pump(t):
            end = time.time() + t
            while time.time() < end:
                try:
                    d = s.recv(8192)
                    if not d:
                        break
                    _negotiate(s, d, buf)
                    if re.search(rb"RC=\d+=DONE", buf):
                        return True
                except socket.timeout:
                    pass
            return False

        pump(0.6)  # drain banner/prompt
        buf.clear()
        s.sendall((cmd + "; echo RC=$?=DONE\n").encode())
        ok = pump(wait)
        s.close()
        text = buf.decode("utf-8", "replace")
        m = re.search(r"RC=(\d+)=DONE", text)  # executed result marker (expanded $?)
        rc = int(m.group(1)) if m else -1
        if m:
            text = text[: m.start()]
        # output is between the echoed input marker (literal 'RC=$?=DONE') and the result marker
        e = text.find("RC=$?=DONE")
        if e != -1:
            nl = text.find("\n", e)
            text = text[nl + 1 :] if nl != -1 else text[e + len("RC=$?=DONE") :]
        return text, rc

    def push(self, local, remote, tries=3):
        want = hashlib.md5(open(local, "rb").read()).hexdigest()
        tmp = tempfile.mkdtemp()
        name = os.path.basename(local)
        shutil.copy(local, os.path.join(tmp, name))
        os.chdir(tmp)
        httpd = ThreadingHTTPServer(
            (self.httpip, 0),
            lambda *a, **k: SimpleHTTPRequestHandler(*a, directory=tmp, **k),
        )
        port = httpd.server_address[1]
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        try:
            url = f"http://{self.httpip}:{port}/{name}"
            for attempt in range(1, tries + 1):
                out, rc = self.run(
                    f"wget -q -O {remote} {url} && md5sum {remote}", wait=20.0
                )
                got = next(
                    (
                        t
                        for t in out.split()
                        if len(t) == 32 and all(c in "0123456789abcdef" for c in t)
                    ),
                    "",
                )
                print(
                    f"  push attempt {attempt}: {'OK' if got==want else 'mismatch, retrying'}"
                )
                if got == want:
                    return True
            return False
        finally:
            httpd.shutdown()
            shutil.rmtree(tmp, ignore_errors=True)

    def shell(self):
        print("type commands (Ctrl-D to quit):")
        for line in sys.stdin:
            out, rc = self.run(line.rstrip("\n"))
            print(out, end="")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="10.55.0.1")
    ap.add_argument("--httpip", default="10.55.0.2")
    sub = ap.add_subparsers(dest="action", required=True)
    r = sub.add_parser("run")
    r.add_argument("cmd")
    p = sub.add_parser("push")
    p.add_argument("local")
    p.add_argument("remote")
    sub.add_parser("shell")
    a = ap.parse_args()
    n = Net(a.host, a.httpip)
    if a.action == "run":
        out, rc = n.run(a.cmd)
        print(out, end="")
        sys.exit(rc if rc >= 0 else 1)
    elif a.action == "push":
        ok = n.push(a.local, a.remote)
        print("PUSH OK" if ok else "PUSH FAILED")
        sys.exit(0 if ok else 1)
    elif a.action == "shell":
        n.shell()


if __name__ == "__main__":
    main()
