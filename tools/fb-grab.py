#!/usr/bin/env python3
"""Grab one frame from the goggle fb0 tap (device/fbtap.c on :9001) -> PNG."""
import socket, struct, sys
from PIL import Image
import numpy as np

HOST, PORT = '10.55.0.1', 9001
OUT = sys.argv[1] if len(sys.argv) > 1 else '/tmp/fb-grab.png'

s = socket.create_connection((HOST, PORT), timeout=8); s.settimeout(8)
def recvn(n):
    b = bytearray()
    while len(b) < n:
        d = s.recv(n - len(b))
        if not d: raise EOFError('short read')
        b += d
    return bytes(b)

magic, w, h, bpp = struct.unpack('<4I', recvn(16))
assert magic == 0x50544246, f'bad magic {magic:#x}'
print(f'fb {w}x{h} {bpp}bpp')
for _ in range(2):                       # let a couple frames pass, grab the latest
    (plen,) = struct.unpack('<I', recvn(4))
    runs = np.frombuffer(recvn(plen), dtype='<u2').reshape(-1, 2)   # (run_count, rgb565)
s.close()

a = np.repeat(runs[:, 1], runs[:, 0].astype(np.int64)).reshape(h, w)   # RGB565 LE
r = ((a >> 11) & 0x1f) << 3
g = ((a >> 5) & 0x3f) << 2
b = (a & 0x1f) << 3
Image.fromarray(np.dstack([r, g, b]).astype('uint8')).save(OUT)
print('saved', OUT)
