/* videotap.c — LD_PRELOAD shim for arlink_fpv on the P1 HD goggle (GND).
 *
 * Tees the *received* H.265 (pre-display, no re-encode) to USB CDC-ACM ports for
 * low-latency viewing. The air unit sends TWO low-delay streams; the goggle decodes
 * them on VDEC channel 0 and channel 1 and composites them for display. We forward:
 *     VDEC chn 0 -> /dev/ttyGS1   (top strip,  ~1920x560)
 *     VDEC chn 1 -> /dev/ttyGS2   (bottom strip, ~1920x552)
 * On the host, decode both and vstack them for the full frame (see tools/view-tap.sh).
 *
 * Interposes the cross-library call libldrt makes into libmpi_vdec (LD_PRELOAD catches it
 * because libldrt resolves it through the global scope):
 *     int AR_MPI_VDEC_SendStream(VDEC_CHN chn, const VDEC_STREAM_S *pstStream, int ms);
 * VDEC_STREAM_S (reverse-engineered): +0x00 uint32_t len, +0x1c uint8_t* data
 * (virtual, userspace-readable; H.265 Annex-B with start codes).
 *
 * Read-only w.r.t. the app; ACM writes are non-blocking/best-effort (drop rather than
 * stall the decode thread; the client resyncs on the next start code).
 *
 * Build: zig cc -target aarch64-linux-gnu.2.25 -shared -fPIC -O2 -s -o build/libvideotap.so device/videotap.c -ldl
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <termios.h>
#include <unistd.h>
#include <stdint.h>

static int (*real_send)(int, void *, int) = NULL;
static int vfd[2]    = { -1, -1 };             /* index = VDEC channel (0,1) */
static int synced[2] = {  0,  0 };
static const char *dev[2] = { "/dev/ttyGS1", "/dev/ttyGS2" };

static int open_raw(const char *path)
{
    int fd = open(path, O_WRONLY | O_NONBLOCK | O_NOCTTY);
    if (fd < 0)
        return -1;
    struct termios t;
    if (tcgetattr(fd, &t) == 0) {
        cfmakeraw(&t);
        tcsetattr(fd, TCSANOW, &t);
    }
    return fd;
}

/* H.265 NAL type of the frame's first NALU (after the Annex-B start code), or -1 */
static int first_nal_type(const uint8_t *d, uint32_t len)
{
    for (uint32_t i = 0; i + 4 < len; i++) {
        if (d[i] == 0 && d[i + 1] == 0 && d[i + 2] == 1)
            return (d[i + 3] >> 1) & 0x3F;
    }
    return -1;
}

int AR_MPI_VDEC_SendStream(int chn, void *pstStream, int ms)
{
    if (!real_send)
        real_send = (int (*)(int, void *, int))dlsym(RTLD_NEXT, "AR_MPI_VDEC_SendStream");

    if (pstStream && (chn == 0 || chn == 1)) {
        uint32_t len  = *(uint32_t *)((char *)pstStream + 0x00);
        uint8_t *data = *(uint8_t **)((char *)pstStream + 0x1c);
        if (data && len) {
            /* only begin at a clean GOP boundary so the client locks immediately:
               32=VPS 33=SPS 34=PPS 19/20=IDR */
            if (!synced[chn]) {
                int nt = first_nal_type(data, len);
                if (nt == 32 || nt == 33 || nt == 34 || nt == 19 || nt == 20)
                    synced[chn] = 1;
            }
            if (synced[chn]) {
                if (vfd[chn] < 0)
                    vfd[chn] = open_raw(dev[chn]);
                if (vfd[chn] >= 0) {
                    uint32_t off = 0;
                    int spins = 0;
                    while (off < len) {
                        ssize_t w = write(vfd[chn], data + off, len - off);
                        if (w > 0) { off += (uint32_t)w; spins = 0; continue; }
                        if (++spins > 40) break;   /* ~8ms cap, then drop rest (P-frame glitch) */
                        usleep(200);               /* let the ACM buffer drain */
                    }
                }
            }
        }
    }
    return real_send(chn, pstStream, ms);
}
