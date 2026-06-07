#!/usr/bin/env python3
"""
ArtLynkStream — low-latency viewer + recorder for the BetaFPV P1 HD goggle's venc8 H.265 USB tap.

One window, three states:
  • GRAY  "Goggle not connected"  — TCP 10.55.0.1:9000 unreachable (USB unplugged / tap not running)
  • BLACK "Waiting for drone…"    — goggle reachable but no video yet (no drone bound)
  • VIDEO                          — streaming the composited feed (+OSD), low latency
Auto-reconnects, so leaving it running just works: plug in the goggle, bind a drone, video starts.
Record saves the H.265 (no re-encode) to ~/Movies and remuxes to .mp4 on stop.

Video is decoded in-process with PyAV (the same FFmpeg libraries ffplay uses), with frame-threading
disabled so frames are shown the instant they decode — matching ffplay's latency. (mpv buffered too
much on this raw stream.)  Requires:  pip install PySide6 av
Run:  app/.venv/bin/python app/artlynkstream.py
"""
import os, sys, time, socket, struct, datetime, subprocess, shutil, threading
import av
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QRect
from PySide6.QtGui import QImage, QColor, QPainter, QFont
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QPushButton, QLabel, QFrame)

HOST = os.environ.get("ARTLYNK_HOST", "10.55.0.1")
PORT = int(os.environ.get("ARTLYNK_PORT", "9000"))
URL  = f"tcp://{HOST}:{PORT}"
OSD_PORT = int(os.environ.get("ARTLYNK_OSD_PORT", "9001"))   # fb0 OSD tap (device/fbtap.c)
RECDIR = os.path.expanduser("~/Movies")
DISP_W, DISP_H = 1280, 720          # decode scaled to this for a smooth UI (record stays full-res)
READ_TIMEOUT = 2.0                  # s; a stalled read (drone dropped) trips this -> reconnect (forces IDR)

STATE_NOGOGGLE, STATE_WAITING, STATE_STREAMING = "nogoggle", "waiting", "streaming"


def reachable(host, port, timeout=0.25):
    try:
        socket.create_connection((host, port), timeout).close(); return True
    except OSError:
        return False


def resize_nn(img, new_h, new_w):
    """Nearest-neighbour resize of an HxWx3 uint8 array (no PIL/cv2 needed)."""
    h, w = img.shape[0], img.shape[1]
    yi = (np.arange(new_h) * h) // new_h
    xi = (np.arange(new_w) * w) // new_w
    return img[yi][:, xi]


class OSDThread(QThread):
    """Reads the goggle's fb0 OSD tap (device/fbtap.c, RLE RGB565 on :OSD_PORT) and keeps the
    latest frame as an alpha-keyed QImage (black -> transparent). VideoView paints it on top of
    the video OR the placeholder, GPU-composited. Only connects while .enabled (UI toggle)."""
    def __init__(self):
        super().__init__()
        self.running = True
        self.enabled = False
        self._latest = None
        self._lock = threading.Lock()

    def get(self):
        with self._lock:
            return self._latest

    def stop(self):
        self.running = False

    def run(self):
        while self.running:
            if not self.enabled:
                with self._lock: self._latest = None
                self.msleep(200); continue
            s = None
            try:
                s = socket.create_connection((HOST, OSD_PORT), timeout=2)
                s.settimeout(5)

                def recvn(n):
                    b = bytearray()
                    while len(b) < n:
                        d = s.recv(n - len(b))
                        if not d: raise EOFError("closed")
                        b += d
                    return bytes(b)

                magic, w, h, bpp = struct.unpack('<4I', recvn(16))
                if magic != 0x50544246:
                    raise ValueError("bad magic")
                while self.running and self.enabled:
                    (plen,) = struct.unpack('<I', recvn(4))
                    runs = np.frombuffer(recvn(plen), dtype='<u2').reshape(-1, 2)
                    a = np.repeat(runs[:, 1], runs[:, 0].astype(np.int64)).reshape(h, w)  # ARGB4444
                    alpha = (((a >> 12) & 0xF) * 17).astype('uint8')   # goggle's real OSD alpha (0=video-through, F=opaque)
                    r = (((a >> 8) & 0xF) * 17).astype('uint8')
                    g = (((a >> 4) & 0xF) * 17).astype('uint8')
                    b = ((a & 0xF) * 17).astype('uint8')
                    rgba = np.ascontiguousarray(np.dstack([r, g, b, alpha]))
                    img = QImage(rgba.tobytes(), w, h, 4 * w, QImage.Format_RGBA8888).copy()
                    with self._lock:
                        self._latest = img
            except Exception:
                pass
            finally:
                if s is not None:
                    try: s.close()
                    except Exception: pass
                with self._lock: self._latest = None
            if self.running and self.enabled:
                self.msleep(600)


class DecodeThread(QThread):
    frame_ready = Signal(QImage)
    state_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.running = True
        self.rec_path = None          # set by UI to start recording, None to stop
        self.force_reconnect = False  # set by UI on record start -> reconnect to land on a fresh IDR
        self._rec_file = None

    def stop(self):
        self.running = False

    def run(self):
        while self.running:
            if not reachable(HOST, PORT):
                self.state_changed.emit(STATE_NOGOGGLE)
                time.sleep(0.6); continue
            try:
                container = av.open(URL, format="hevc",
                                    options={"analyzeduration": "2000000", "probesize": "2000000"},
                                    timeout=READ_TIMEOUT)
            except Exception:
                self.state_changed.emit(STATE_NOGOGGLE); time.sleep(0.5); continue
            self.force_reconnect = False
            got = False
            try:
                vs = container.streams.video[0]
                vs.thread_type = "NONE"                 # frames out immediately -> low latency
                extradata = bytes(vs.codec_context.extradata or b"")   # VPS/SPS/PPS for the file header
                self.state_changed.emit(STATE_WAITING)
                # Open (or continue) the recording at this clean connection start. Because a record
                # start forces a reconnect, the very first packets here are params + a fresh IDR, so
                # the file always begins at a decodable keyframe. We also write the params explicitly.
                if self.rec_path:
                    if self._rec_file is None:
                        try: self._rec_file = open(self.rec_path, "wb")
                        except Exception: self._rec_file = None
                    if self._rec_file is not None and extradata:
                        self._rec_file.write(extradata)
                for packet in container.demux(vs):
                    if not self.running or self.force_reconnect:
                        break
                    if self._rec_file is not None:
                        if self.rec_path:
                            try: self._rec_file.write(bytes(packet))
                            except Exception: pass
                        else:                            # stop requested
                            try: self._rec_file.close()
                            except Exception: pass
                            self._rec_file = None
                    for frame in packet.decode():
                        img = self._to_qimage(frame)
                        if img is not None:
                            if not got:
                                got = True; self.state_changed.emit(STATE_STREAMING)
                            self.frame_ready.emit(img)
            except Exception as ex:
                if "Immediate exit" not in str(ex):    # read timeout (no drone) is expected/quiet
                    sys.stderr.write(f"[decode] {type(ex).__name__}: {ex}\n"); sys.stderr.flush()
            finally:
                try: container.close()
                except Exception: pass
            if not got:                                 # connected but no frames (no drone yet)
                self.state_changed.emit(STATE_WAITING); time.sleep(0.2)
        if self._rec_file is not None:
            try: self._rec_file.close()
            except Exception: pass

    def _to_qimage(self, frame):
        try:
            f = frame.reformat(width=DISP_W, height=DISP_H, format="rgb24")
            arr = f.to_ndarray()                       # H x W x 3, uint8
            h, w = arr.shape[0], arr.shape[1]
            img = QImage(arr.tobytes(), w, h, 3 * w, QImage.Format_RGB888)
            return img.copy()                          # own the pixels (arr is reused)
        except Exception as ex:
            sys.stderr.write(f"[to_qimage] {type(ex).__name__}: {ex}\n"); sys.stderr.flush()
            return None


class VideoView(QWidget):
    """Paints the latest frame (scaled, letterboxed) or a gray/black placeholder card."""
    def __init__(self):
        super().__init__()
        self.setMinimumSize(640, 360)
        self._img = None
        self._mode = "gray"
        self._title = "Starting…"
        self._sub = ""
        self._osd = None
        self._osd_on = False
        self._osd_timer = QTimer(self)             # repaint so the OSD updates over placeholders too
        self._osd_timer.timeout.connect(self._osd_tick); self._osd_timer.start(120)

    def set_frame(self, img):
        self._img = img; self._mode = "video"; self.update()

    def set_placeholder(self, mode, title, sub=""):
        self._mode = mode; self._title = title; self._sub = sub; self.update()

    def set_osd(self, osd):
        self._osd = osd

    def set_osd_on(self, on):
        self._osd_on = on; self.update()

    def _osd_tick(self):
        if self._osd_on and self._mode != "video":   # video state already repaints per frame
            self.update()

    def _content_rect(self):
        ww, wh = self.width(), self.height()
        s = min(ww / 16.0, wh / 9.0)                 # OSD + video are both 16:9
        dw, dh = int(16 * s), int(9 * s)
        return QRect((ww - dw) // 2, (wh - dh) // 2, dw, dh)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.fillRect(self.rect(), QColor("#000"))
        rect = self._content_rect()
        if self._mode == "video" and self._img is not None:
            p.drawImage(rect, self._img)
        else:
            p.fillRect(self.rect(), QColor("#6e6e6e" if self._mode == "gray" else "#000"))
            p.setPen(QColor("#ededed"))
            p.setFont(QFont("Helvetica Neue", 30, QFont.DemiBold))
            p.drawText(0, 0, self.width(), self.height() - 44, Qt.AlignCenter, self._title)
            if self._sub:
                p.setPen(QColor("#b8b8b8")); p.setFont(QFont("Helvetica Neue", 15))
                p.drawText(0, self.height() // 2 + 28, self.width(), 40,
                           Qt.AlignHCenter | Qt.AlignTop, self._sub)
        if self._osd_on and self._osd is not None:   # GPU-composited overlay (alpha-keyed), on top
            osd = self._osd.get()
            if osd is not None:
                p.drawImage(rect, osd)


def find_ffmpeg():
    return (shutil.which("ffmpeg")
            or next((p for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg")
                     if os.path.exists(p)), None))


class ArtLynkStream(QMainWindow):
    status_msg = Signal(str)          # thread-safe status updates (e.g. from the remux thread)

    def __init__(self):
        super().__init__()
        self.ffmpeg = find_ffmpeg()
        self.setWindowTitle("ArtLynkStream")
        self.resize(1280, 760)
        central = QWidget(); self.setCentralWidget(central)
        lay = QVBoxLayout(central); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)

        self.view = VideoView(); lay.addWidget(self.view, 1)

        bar = QFrame(); bar.setFixedHeight(46); bar.setStyleSheet("background:#161616;")
        hb = QHBoxLayout(bar); hb.setContentsMargins(14, 0, 12, 0)
        self.dot = QLabel("●"); self.dot.setStyleSheet("color:#666; font-size:16px;")
        self.status = QLabel("Starting…"); self.status.setStyleSheet("color:#ddd; font-size:14px;")
        self.rec = QPushButton("●  Record"); self.rec.setCheckable(True); self.rec.setEnabled(False)
        self.rec.setCursor(Qt.PointingHandCursor)
        self.rec.setStyleSheet(
            "QPushButton{color:#eee;background:#2a2a2a;border:1px solid #3a3a3a;border-radius:6px;padding:6px 14px;font-size:13px;}"
            "QPushButton:checked{color:#fff;background:#a01b1b;border-color:#c23030;}"
            "QPushButton:disabled{color:#666;background:#1e1e1e;border-color:#262626;}")
        self.rec.clicked.connect(self.toggle_record)
        self.osd_btn = QPushButton("OSD")
        self.osd_btn.setCheckable(True); self.osd_btn.setCursor(Qt.PointingHandCursor)
        self.osd_btn.setStyleSheet(
            "QPushButton{color:#eee;background:#2a2a2a;border:1px solid #3a3a3a;border-radius:6px;padding:6px 14px;font-size:13px;}"
            "QPushButton:checked{color:#fff;background:#1b5fa0;border-color:#3080c2;}")
        self.osd_btn.toggled.connect(self._toggle_osd)
        hb.addWidget(self.dot); hb.addSpacing(6); hb.addWidget(self.status)
        hb.addStretch(1); hb.addWidget(self.osd_btn); hb.addSpacing(8); hb.addWidget(self.rec)
        lay.addWidget(bar)

        self.state = None
        self.rec_path = None
        self.rec_started = 0.0

        self.status_msg.connect(self.status.setText)
        self.dec = DecodeThread()
        self.dec.frame_ready.connect(self.view.set_frame)
        self.dec.state_changed.connect(self.on_state)
        self.osd = OSDThread()
        self.view.set_osd(self.osd)
        self.osd.start()
        self.dec.start()

        self.rectimer = QTimer(self); self.rectimer.timeout.connect(self.update_rec_label); self.rectimer.start(500)

    def on_state(self, st):
        if st == self.state and st != STATE_STREAMING:
            return
        self.state = st
        if st == STATE_NOGOGGLE:
            self.view.set_placeholder("gray", "Goggle not connected",
                                      "Check USB-C · set the Mac adapter to 10.55.0.2")
            self.dot.setStyleSheet("color:#666; font-size:16px;")
            self.status.setText("Goggle not connected")
            if not self.rec.isChecked(): self.rec.setEnabled(False)
        elif st == STATE_WAITING:
            self.view.set_placeholder("black", "Waiting for drone…",
                                      "Goggle connected — bind a drone to see video")
            self.dot.setStyleSheet("color:#c9a227; font-size:16px;")
            self.status.setText("Goggle connected — waiting for drone video…")
            if not self.rec.isChecked(): self.rec.setEnabled(False)
        elif st == STATE_STREAMING:
            self.dot.setStyleSheet("color:#3aa655; font-size:16px;")
            self.status.setText("Streaming 1920×1080  ·  low latency")
            self.rec.setEnabled(True)

    def _toggle_osd(self, on):
        self.osd.enabled = on
        self.view.set_osd_on(on)

    # ---- recording: tee raw H.265 packets (from a fresh IDR), remux to .mp4 on stop ----
    def toggle_record(self):
        if self.rec.isChecked():
            os.makedirs(RECDIR, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            self.rec_path = os.path.join(RECDIR, f"ArtLynkStream-{ts}.h265")
            self.rec_started = time.time()
            self.dec.rec_path = self.rec_path
            self.dec.force_reconnect = True          # reconnect so recording starts at a fresh IDR
        else:
            self.dec.rec_path = None
            self._finalize_recording()

    def _finalize_recording(self):
        src = self.rec_path; self.rec_path = None
        if not src:
            return
        mp4 = src[:-5] + ".mp4"
        ff = self.ffmpeg

        def work():
            for _ in range(40):                      # wait for the decode thread to flush+close the file
                if os.path.exists(src) and os.path.getsize(src) > 0:
                    break
                time.sleep(0.05)
            if not ff:
                self.status_msg.emit(f"Saved {os.path.basename(src)} (ffmpeg not found for .mp4)")
                return
            try:
                r = subprocess.run([ff, "-y", "-loglevel", "error", "-fflags", "+genpts",
                                    "-r", "60", "-f", "hevc", "-i", src,
                                    "-c", "copy", "-tag:v", "hvc1", mp4],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                if r.returncode == 0 and os.path.exists(mp4) and os.path.getsize(mp4) > 0:
                    try: os.remove(src)
                    except Exception: pass
                    self.status_msg.emit(f"Saved {os.path.basename(mp4)}")
                else:
                    self.status_msg.emit(f"Saved {os.path.basename(src)} (raw H.265; .mp4 remux failed)")
            except Exception as ex:
                self.status_msg.emit(f"Saved {os.path.basename(src)} (raw; {ex})")

        threading.Thread(target=work, daemon=True).start()

    def update_rec_label(self):
        if self.rec.isChecked():
            el = int(time.time() - self.rec_started)
            self.rec.setText(f"●  REC  {el // 60:02d}:{el % 60:02d}")
        else:
            self.rec.setText("●  Record")

    def closeEvent(self, e):
        try: self.dec.rec_path = None
        except Exception: pass
        try: self.osd.stop()
        except Exception: pass
        try: self.dec.stop()
        except Exception: pass
        e.accept()
        os._exit(0)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ArtLynkStream")
    w = ArtLynkStream(); w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
