#!/usr/bin/env python3
"""
P1 Video Cast — low-latency viewer + recorder for the BetaFPV P1 HD goggle's H.265 USB tap.

One window, three states:
  • GRAY  "Goggle not connected"  — tcp://10.55.0.1:9000 unreachable (USB unplugged / tap not running)
  • BLACK "Waiting for drone…"    — goggle reachable but no video yet (no drone bound)
  • VIDEO                          — the composited feed, low latency

Buttons:  OSD overlays the goggle's telemetry (its fb0 layer, tapped over :9001) onto the video;
OBS re-streams a clean H.264/MPEG-TS on tcp://127.0.0.1:9200 for an OBS Media Source; Record saves
an .mp4 to ~/Movies (raw H.265 teed live, OSD composited offline on stop — no live slowdown).
Auto-reconnects, so leaving it running just works: plug in the goggle, bind a drone, video starts.

Video is decoded in-process with PyAV (the FFmpeg libraries ffplay uses) with frame-threading off,
so frames show the instant they decode (matches ffplay's latency; mpv buffered too much).
Run:  app/.venv/bin/python app/p1cast.py        (deps: PySide6, av, numpy)
"""
import os, sys, time, socket, struct, queue, datetime, subprocess, shutil, threading
import av
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QRect
from PySide6.QtGui import QImage, QColor, QPainter, QFont
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QPushButton, QLabel, QFrame)

HOST = os.environ.get("P1CAST_HOST", "10.55.0.1")
PORT = int(os.environ.get("P1CAST_PORT", "9000"))
URL  = f"tcp://{HOST}:{PORT}"
OSD_PORT = int(os.environ.get("P1CAST_OSD_PORT", "9001"))   # fb0 OSD tap (device/fbtap.c)
RECDIR = os.path.expanduser("~/Movies")
DISP_W, DISP_H = 1280, 720          # decode scaled to this for a smooth UI (record stays full-res)
READ_TIMEOUT = 2.0                  # s; a stalled read (drone dropped) trips this -> reconnect (forces IDR)

STATE_NOGOGGLE, STATE_WAITING, STATE_STREAMING = "nogoggle", "waiting", "streaming"


def reachable(host, port, timeout=0.25):
    try:
        socket.create_connection((host, port), timeout).close(); return True
    except OSError:
        return False


def osd_rle_to_rgba(payload, w, h):
    """Decode one fbtap RLE frame (ARGB4444) to an HxWx4 RGBA uint8 array."""
    runs = np.frombuffer(payload, dtype='<u2').reshape(-1, 2)
    a = np.repeat(runs[:, 1], runs[:, 0].astype(np.int64)).reshape(h, w)
    alpha = (((a >> 12) & 0xF) * 17).astype('uint8')   # 0 = video-through, F = opaque
    r = (((a >> 8) & 0xF) * 17).astype('uint8')
    g = (((a >> 4) & 0xF) * 17).astype('uint8')
    b = ((a & 0xF) * 17).astype('uint8')
    return np.ascontiguousarray(np.dstack([r, g, b, alpha]))


def _new_encoder(out, w, h, fps):
    try:
        st = out.add_stream('h264_videotoolbox', rate=fps)   # Mac HW encoder
    except Exception:
        st = out.add_stream('libx264', rate=fps)
        try: st.options = {'preset': 'fast'}
        except Exception: pass
    st.width = w; st.height = h; st.pix_fmt = 'yuv420p'
    try: st.bit_rate = 12_000_000
    except Exception: pass
    return st


_FINALIZE_LOCK = threading.Lock()   # serialize saves so concurrent recordings don't pile onto the CPU


def finalize_recording(h265_path, osd_path, mp4_path, duration=0.0, progress=None):
    """OFFLINE (background) finalize of a recording. If OSD frames were logged, decode the raw H.265,
    alpha-composite the time-aligned OSD onto each frame, and HW-re-encode -> MP4. If no OSD was
    logged, just losslessly remux H.265 -> MP4 (ffmpeg -c copy). Deletes the intermediates."""
    osd_frames = []                                    # list of (time_sec, rgba)
    if os.path.exists(osd_path):
        try:
            with open(osd_path, 'rb') as f:
                hdr = f.read(8)
                if len(hdr) == 8:
                    ow, oh = struct.unpack('<II', hdr)
                    while True:
                        fh = f.read(8)
                        if len(fh) < 8: break
                        t, plen = struct.unpack('<fI', fh)
                        pl = f.read(plen)
                        if len(pl) < plen: break
                        osd_frames.append((t, osd_rle_to_rgba(pl, ow, oh)))
        except Exception:
            osd_frames = []

    # raw H.265 has no reliable timing -> derive real fps from packet count / recording duration
    npkt = 0
    try:
        c = av.open(h265_path, format='hevc')
        for _ in c.demux(c.streams.video[0]):
            npkt += 1
        c.close()
    except Exception:
        pass
    fps = round(npkt / duration) if (duration > 0.2 and npkt) else 60
    for std in (24, 25, 30, 48, 50, 60):               # snap to a clean rate (tolerate timing slop)
        if abs(fps - std) <= 2:
            fps = std; break
    fps = max(1, min(120, fps))

    if not osd_frames:                                 # no OSD -> lossless remux
        ff = shutil.which('ffmpeg') or next((p for p in ('/opt/homebrew/bin/ffmpeg',
                '/usr/local/bin/ffmpeg') if os.path.exists(p)), None)
        if ff:
            r = subprocess.run([ff, '-y', '-loglevel', 'error', '-fflags', '+genpts', '-r', str(fps),
                                '-f', 'hevc', '-i', h265_path, '-c', 'copy', '-tag:v', 'hvc1', mp4_path],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if r.returncode == 0 and os.path.exists(mp4_path) and os.path.getsize(mp4_path) > 0:
                for p in (h265_path, osd_path):
                    try: os.remove(p)
                    except Exception: pass
                return mp4_path
        # ffmpeg missing/failed -> fall through to the re-encode path

    inp = av.open(h265_path, format='hevc')
    vs = inp.streams.video[0]; vs.thread_type = "AUTO"
    out = av.open(mp4_path, mode='w')
    ost = None; n = 0; oi = 0
    try:
        for packet in inp.demux(vs):
            for frame in packet.decode():
                rgb = frame.to_ndarray(format='rgb24')
                if osd_frames:
                    t = n / fps                        # align OSD to the real frame time
                    while oi + 1 < len(osd_frames) and osd_frames[oi + 1][0] <= t:
                        oi += 1
                    osd = osd_frames[oi][1]
                    if osd.shape[:2] == rgb.shape[:2]:
                        a = osd[:, :, 3:4].astype(np.uint16)
                        rgb = ((rgb.astype(np.uint16) * (255 - a) + osd[:, :, :3].astype(np.uint16) * a) // 255).astype('uint8')
                if ost is None:
                    ost = _new_encoder(out, rgb.shape[1], rgb.shape[0], fps)
                vf = av.VideoFrame.from_ndarray(np.ascontiguousarray(rgb), format='rgb24')
                vf.pts = n; n += 1
                if progress and n % 30 == 0:
                    progress(n, npkt)
                for pkt in ost.encode(vf):
                    out.mux(pkt)
        if ost is not None:
            for pkt in ost.encode():
                out.mux(pkt)
    finally:
        try: out.close()
        except Exception: pass
        try: inp.close()
        except Exception: pass
    for p in (h265_path, osd_path):
        try: os.remove(p)
        except Exception: pass
    return mp4_path


class OSDThread(QThread):
    """Reads the goggle's fb0 OSD tap (device/fbtap.c, RLE ARGB4444 on :OSD_PORT) and keeps the
    latest frame as an alpha-keyed QImage (alpha 0 -> transparent). VideoView paints it on top of
    the video OR the placeholder, GPU-composited. Only connects while .enabled (UI toggle)."""
    def __init__(self):
        super().__init__()
        self.running = True
        self.enabled = False
        self.rec_path = None          # set by UI to log OSD frames (+timestamps) during a recording
        self.rec_start = 0.0
        self._rec_f = None
        self._latest = None
        self._latest_np = None
        self._lock = threading.Lock()

    def get(self):
        with self._lock:
            return self._latest

    def get_np(self):
        with self._lock:
            return self._latest_np

    def stop(self):
        self.running = False

    def run(self):
        while self.running:
            if not self.rec_path:                  # recording stopped (or never started) -> close log
                self._close_log()
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
                    payload = recvn(plen)
                    rgba = osd_rle_to_rgba(payload, w, h)
                    img = QImage(rgba.tobytes(), w, h, 4 * w, QImage.Format_RGBA8888).copy()
                    with self._lock:
                        self._latest = img
                        self._latest_np = rgba
                    if self.rec_path:              # log this OSD frame (RLE + timestamp) for the recording
                        try:
                            if self._rec_f is None:
                                self._rec_f = open(self.rec_path, 'wb')
                                self._rec_f.write(struct.pack('<II', w, h))
                            self._rec_f.write(struct.pack('<fI', time.time() - self.rec_start, len(payload)) + payload)
                        except Exception: pass
                    else:
                        self._close_log()
            except Exception:
                pass
            finally:
                if s is not None:
                    try: s.close()
                    except Exception: pass
                with self._lock: self._latest = None; self._latest_np = None
            if self.running and self.enabled:
                self.msleep(600)
        self._close_log()

    def _close_log(self):
        if self._rec_f is not None:
            try: self._rec_f.close()
            except Exception: pass
            self._rec_f = None


class _BroadcastIO:
    """A write-only file object PyAV muxes MPEG-TS into; each write is fanned out to all clients."""
    def __init__(self, sendfn): self._send = sendfn
    def write(self, data): self._send(bytes(data)); return len(data)
    def seekable(self): return False


class RestreamServer:
    """Lazy local H.264/MPEG-TS re-stream for OBS. Listens on tcp://127.0.0.1:PORT and encodes ONLY
    while .enabled AND a client is connected (zero cost otherwise). Frequent keyframes (gop=15) so
    OBS syncs fast with a tiny buffer. OBS: Media Source, Input tcp://127.0.0.1:9200."""
    def __init__(self, port=9200):
        self.port = port
        self.enabled = False
        self.osd = None               # OSDThread — composited into the OBS feed whenever it has a frame
        self.running = True
        self._q = queue.Queue(maxsize=1)
        self._clients = []
        self._lock = threading.Lock()
        threading.Thread(target=self._accept_loop, daemon=True).start()
        threading.Thread(target=self._encode_loop, daemon=True).start()

    def stop(self): self.running = False

    def feed(self, frame):
        if not self.enabled:
            return
        try:
            self._q.put_nowait(frame)
        except queue.Full:
            try: self._q.get_nowait()          # drop the stale frame, keep the newest
            except queue.Empty: pass
            try: self._q.put_nowait(frame)
            except queue.Full: pass

    def _nclients(self):
        with self._lock:
            return len(self._clients)

    def _accept_loop(self):
        try:
            ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            ls.bind(("127.0.0.1", self.port)); ls.listen(2)
        except Exception as ex:
            sys.stderr.write(f"[restream] bind: {ex}\n"); return
        while self.running:
            try:
                c, _ = ls.accept()
                c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                with self._lock: self._clients.append(c)
            except Exception:
                time.sleep(0.2)

    def _broadcast(self, data):
        with self._lock:
            dead = []
            for c in self._clients:
                try: c.sendall(data)
                except Exception: dead.append(c)
            for c in dead:
                self._clients.remove(c)
                try: c.close()
                except Exception: pass

    def _encode_loop(self):
        out = st = None; n = 0
        while self.running:
            if not (self.enabled and self._nclients() > 0):
                if out is not None:
                    try:
                        for p in st.encode(): out.mux(p)
                        out.close()
                    except Exception: pass
                    out = st = None
                try: self._q.get(timeout=0.2)
                except queue.Empty: pass
                continue
            try: frame = self._q.get(timeout=0.5)
            except queue.Empty: continue
            try:
                osd = self.osd.get_np() if self.osd is not None else None
                if osd is not None:                        # composite OSD (cheap; matches the app view)
                    rgb = frame.to_ndarray(format='rgb24')
                    if osd.shape[:2] == rgb.shape[:2]:
                        a = osd[:, :, 3:4].astype(np.uint16)
                        rgb = ((rgb.astype(np.uint16) * (255 - a) + osd[:, :, :3].astype(np.uint16) * a) // 255).astype('uint8')
                    yuv = av.VideoFrame.from_ndarray(np.ascontiguousarray(rgb), format='rgb24').reformat(format='yuv420p')
                else:
                    yuv = frame.reformat(format='yuv420p')
                if out is None:
                    out = av.open(_BroadcastIO(self._broadcast), mode='w', format='mpegts')
                    try: st = out.add_stream('h264_videotoolbox', rate=30)
                    except Exception:
                        st = out.add_stream('libx264', rate=30)
                        try: st.options = {'preset': 'ultrafast', 'tune': 'zerolatency'}
                        except Exception: pass
                    st.width = yuv.width; st.height = yuv.height; st.pix_fmt = 'yuv420p'
                    try: st.codec_context.gop_size = 15
                    except Exception: pass
                    try: st.bit_rate = 6_000_000
                    except Exception: pass
                    n = 0
                yuv.pts = n; n += 1
                for p in st.encode(yuv): out.mux(p)
            except Exception as ex:
                sys.stderr.write(f"[restream] {type(ex).__name__}: {ex}\n"); sys.stderr.flush()
                try: out.close()
                except Exception: pass
                out = st = None


class DecodeThread(QThread):
    frame_ready = Signal(QImage)
    state_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.running = True
        self.rec_path = None          # set by UI to tee raw H.265 to this .h265 file, None to stop
        self.force_reconnect = False  # set on record start -> reconnect so the file begins at an IDR
        self._rec_f = None
        self.restream = None          # RestreamServer — fed each decoded frame for the OBS re-stream

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
                extradata = bytes(vs.codec_context.extradata or b"")   # VPS/SPS/PPS header
                self.state_changed.emit(STATE_WAITING)
                if self.rec_path and self._rec_f is None:   # this connection started at a fresh IDR
                    try:
                        self._rec_f = open(self.rec_path, "wb")
                        if extradata: self._rec_f.write(extradata)
                    except Exception: self._rec_f = None
                for packet in container.demux(vs):
                    if not self.running or self.force_reconnect:
                        break
                    if self.rec_path:                   # tee raw H.265 — cheap, lossless, no live load
                        if self._rec_f is not None:
                            try: self._rec_f.write(bytes(packet))
                            except Exception: pass
                    elif self._rec_f is not None:       # stop requested -> close the tee file
                        try: self._rec_f.close()
                        except Exception: pass
                        self._rec_f = None
                    for frame in packet.decode():
                        img = self._to_qimage(frame)
                        if img is not None:
                            if not got:
                                got = True; self.state_changed.emit(STATE_STREAMING)
                            self.frame_ready.emit(img)
                        if self.restream is not None:
                            self.restream.feed(frame)
            except Exception as ex:
                if "Immediate exit" not in str(ex):    # read timeout (no drone) is expected/quiet
                    sys.stderr.write(f"[decode] {type(ex).__name__}: {ex}\n"); sys.stderr.flush()
            finally:
                try: container.close()
                except Exception: pass
            if not got:                                 # connected but no frames (no drone yet)
                self.state_changed.emit(STATE_WAITING); time.sleep(0.2)
        if self._rec_f is not None:
            try: self._rec_f.close()
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


class P1VideoCast(QMainWindow):
    status_msg = Signal(str)          # thread-safe connection-status updates
    save_msg = Signal(str)            # thread-safe save-progress updates (a separate label)

    def __init__(self):
        super().__init__()
        self.ffmpeg = find_ffmpeg()
        self.setWindowTitle("P1 Video Cast")
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
        self.obs_btn = QPushButton("OBS")
        self.obs_btn.setCheckable(True); self.obs_btn.setCursor(Qt.PointingHandCursor)
        self.obs_btn.setToolTip("Re-stream H.264 for OBS — Media Source, Input: tcp://127.0.0.1:9200")
        self.obs_btn.setStyleSheet(
            "QPushButton{color:#eee;background:#2a2a2a;border:1px solid #3a3a3a;border-radius:6px;padding:6px 14px;font-size:13px;}"
            "QPushButton:checked{color:#fff;background:#7a3da0;border-color:#9a5dc2;}")
        self.obs_btn.toggled.connect(self._toggle_obs)
        self.save_status = QLabel(""); self.save_status.setStyleSheet("color:#c9a227; font-size:13px;")
        hb.addWidget(self.dot); hb.addSpacing(6); hb.addWidget(self.status)
        hb.addStretch(1); hb.addWidget(self.save_status); hb.addSpacing(14)
        hb.addWidget(self.obs_btn); hb.addSpacing(8); hb.addWidget(self.osd_btn); hb.addSpacing(8); hb.addWidget(self.rec)
        lay.addWidget(bar)

        self.state = None
        self.rec_base = None
        self.rec_started = 0.0

        self.status_msg.connect(self.status.setText)
        self.save_msg.connect(self.save_status.setText)
        self.dec = DecodeThread()
        self.dec.frame_ready.connect(self.view.set_frame)
        self.dec.state_changed.connect(self.on_state)
        self.osd = OSDThread()
        self.view.set_osd(self.osd)
        self.osd.start()
        self.restream = RestreamServer()
        self.restream.osd = self.osd
        self.dec.restream = self.restream
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

    def _toggle_obs(self, on):
        self.restream.enabled = on
        self.save_msg.emit("OBS re-stream ON  ·  Media Source → tcp://127.0.0.1:9200" if on else "")

    # ---- recording: tee raw H.265 + log OSD live (cheap); overlay+encode (or remux) offline on stop ----
    def toggle_record(self):
        if self.rec.isChecked():
            os.makedirs(RECDIR, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            base = os.path.join(RECDIR, f"P1Cast-{ts}")
            self.rec_base = base
            self.rec_started = now = time.time()
            self.osd.rec_start = now
            self.osd.rec_path = base + ".osd"         # OSD logger writes frames only while OSD is on
            self.dec.rec_path = base + ".h265"        # raw video tee
            self.dec.force_reconnect = True           # reconnect so the .h265 begins at an IDR
        else:
            self.dec.rec_path = None
            self.osd.rec_path = None
            dur = max(0.1, time.time() - self.rec_started)
            self.save_msg.emit("⏳ Saving…  (keep the app open)")
            self._finalize_async(self.rec_base, dur)

    def _finalize_async(self, base, dur):
        if not base:
            return
        h265, osd, mp4 = base + ".h265", base + ".osd", base + ".mp4"

        def work():
            last = None                               # wait until the tee files stop growing (closed)
            for _ in range(120):
                cur = (os.path.getsize(h265) if os.path.exists(h265) else 0,
                       os.path.getsize(osd) if os.path.exists(osd) else 0)
                if cur == last and cur[0] > 0:
                    break
                last = cur; time.sleep(0.1)
            cb = lambda n, tot: self.save_msg.emit(f"⏳ Saving…  {min(99, int(n * 100 / max(1, tot)))}%  (keep the app open)")
            with _FINALIZE_LOCK:                      # one save at a time -> no CPU pile-up
                try:
                    finalize_recording(h265, osd, mp4, dur, cb)
                    self.save_msg.emit(f"✓ Saved {os.path.basename(mp4)}")
                except Exception as ex:
                    self.save_msg.emit(f"⚠ Save failed: {ex}")

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
        try: self.restream.stop()
        except Exception: pass
        try: self.osd.stop()
        except Exception: pass
        try: self.dec.stop()
        except Exception: pass
        e.accept()
        os._exit(0)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("P1 Video Cast")
    w = P1VideoCast(); w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
