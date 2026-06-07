/* fbtap.c — tap the goggle's OSD framebuffer (/dev/fb0, 1920x1080 RGB565) over TCP.
 * Read-only + a listening socket — does NOT touch USB role, so the ECM/net link stays up.
 * Wire format:
 *   once per client: 16-byte header {u32 magic 'FBTP', u32 w, u32 h, u32 bpp=16}
 *   per frame:       u32 payload_len, then RLE pairs {u16 run_count, u16 rgb565} covering w*h px
 * RLE crushes the mostly-black OSD (~4MB raw -> ~tens of KB). Follows vinfo.yoffset for paging.
 * Build: zig cc -target aarch64-linux-gnu.2.25 -O2 -s -o build/fbtap device/fbtap.c
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdint.h>
#include <signal.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <linux/fb.h>

#define PORT 9001
#define FPS_US 250000   /* ~4 fps — OSD/telemetry changes slowly; keep it featherweight */

static int write_all(int fd, const void *p, size_t n){
    const uint8_t *b=(const uint8_t*)p; size_t o=0;
    while(o<n){ ssize_t w=write(fd,b+o,n-o); if(w<=0) return 1; o+=(size_t)w; }
    return 0;
}
static size_t rle_encode(const uint16_t *src, int n, uint8_t *out){
    size_t o=0; int i=0;
    while(i<n){
        uint16_t p=src[i]; int run=1;
        while(i+run<n && src[i+run]==p && run<65535) run++;
        out[o++]=run&0xFF; out[o++]=(run>>8)&0xFF;
        out[o++]=p&0xFF;   out[o++]=(p>>8)&0xFF;
        i+=run;
    }
    return o;
}

int main(void){
    signal(SIGPIPE, SIG_IGN);   /* a client disconnect must not kill us */
    int fbfd=open("/dev/fb0",O_RDONLY); if(fbfd<0) return 1;
    struct fb_var_screeninfo v; struct fb_fix_screeninfo f;
    if(ioctl(fbfd,FBIOGET_VSCREENINFO,&v)<0||ioctl(fbfd,FBIOGET_FSCREENINFO,&f)<0) return 2;
    int w=v.xres, h=v.yres, stride=f.line_length;
    size_t mapsize=(size_t)stride*v.yres_virtual;
    uint8_t *fb=(uint8_t*)mmap(0,mapsize,PROT_READ,MAP_SHARED,fbfd,0);
    if(fb==MAP_FAILED) return 3;
    uint16_t *fbuf=(uint16_t*)malloc((size_t)w*h*2);   /* contiguous visible page */
    uint8_t  *rle =(uint8_t*) malloc((size_t)w*h*4);   /* worst-case RLE */
    if(!fbuf||!rle) return 4;

    int ls=socket(AF_INET,SOCK_STREAM,0);
    int one=1; setsockopt(ls,SOL_SOCKET,SO_REUSEADDR,&one,sizeof(one));
    struct sockaddr_in sa; memset(&sa,0,sizeof(sa));
    sa.sin_family=AF_INET; sa.sin_addr.s_addr=INADDR_ANY; sa.sin_port=htons(PORT);
    if(bind(ls,(struct sockaddr*)&sa,sizeof(sa))<0) return 5;
    listen(ls,1);

    uint32_t hdr[4]={0x50544246u,(uint32_t)w,(uint32_t)h,16};
    for(;;){
        int c=accept(ls,0,0); if(c<0) continue;
        int o=1; setsockopt(c,IPPROTO_TCP,TCP_NODELAY,&o,sizeof(o));
        if(write_all(c,hdr,sizeof(hdr))){ close(c); continue; }
        int bad=0;
        while(!bad){
            struct fb_var_screeninfo vv; size_t pageoff=0;
            if(ioctl(fbfd,FBIOGET_VSCREENINFO,&vv)==0) pageoff=(size_t)vv.yoffset*stride;
            for(int y=0;y<h;y++) memcpy(fbuf+(size_t)y*w, fb+pageoff+(size_t)y*stride, (size_t)w*2);
            size_t rlen=rle_encode(fbuf,w*h,rle);
            uint32_t L=(uint32_t)rlen;
            if(write_all(c,&L,4)||write_all(c,rle,rlen)){ bad=1; break; }
            usleep(FPS_US);
        }
        close(c);
    }
}
