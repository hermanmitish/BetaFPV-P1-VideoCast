#!/usr/bin/env python3
"""Live viewer for the goggle fb0 OSD tap (device/fbtap.c on :9001)."""
import sys, socket, struct
import numpy as np
from PySide6.QtWidgets import QApplication, QLabel
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtCore import QThread, Signal, Qt

HOST, PORT = (sys.argv[1] if len(sys.argv) > 1 else '10.55.0.1'), 9001


class Reader(QThread):
    frame = Signal(object)
    status = Signal(str)

    def run(self):
        while not self.isInterruptionRequested():
            s = None
            try:
                s = socket.create_connection((HOST, PORT), timeout=5)
                s.settimeout(10)

                def recvn(n):
                    b = bytearray()
                    while len(b) < n:
                        d = s.recv(n - len(b))
                        if not d:
                            raise EOFError('closed')
                        b += d
                    return bytes(b)

                magic, w, h, bpp = struct.unpack('<4I', recvn(16))
                if magic != 0x50544246:
                    raise ValueError(f'bad magic {magic:#x}')
                self.status.emit(f'fb0 tap {w}x{h} {bpp}bpp')
                while not self.isInterruptionRequested():
                    (plen,) = struct.unpack('<I', recvn(4))
                    runs = np.frombuffer(recvn(plen), dtype='<u2').reshape(-1, 2)
                    a = np.repeat(runs[:, 1], runs[:, 0].astype(np.int64)).reshape(h, w)
                    r = ((a >> 11) & 0x1f) << 3
                    g = ((a >> 5) & 0x3f) << 2
                    b = (a & 0x1f) << 3
                    self.frame.emit(np.dstack([r, g, b]).astype('uint8'))
            except Exception as e:
                self.status.emit(f'reconnecting… ({e})')
            finally:
                if s is not None:
                    try: s.close()
                    except Exception: pass
            self.msleep(800)


class Viewer(QLabel):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('goggle fb0 OSD tap')
        self.resize(960, 540)
        self.setScaledContents(True)
        self.setStyleSheet('background:#111;color:#888;')
        self.setAlignment(Qt.AlignCenter)
        self.setText('connecting…')
        self.r = Reader()
        self.r.frame.connect(self.on_frame)
        self.r.status.connect(lambda m: (print(m), self.setText(m)))
        self.r.start()

    def on_frame(self, rgb):
        h, w, _ = rgb.shape
        img = QImage(rgb.tobytes(), w, h, 3 * w, QImage.Format_RGB888).copy()
        self.setPixmap(QPixmap.fromImage(img))

    def closeEvent(self, e):
        self.r.requestInterruption()
        self.r.wait(1000)
        e.accept()


app = QApplication(sys.argv)
v = Viewer(); v.show()
sys.exit(app.exec())
