/* uvcfb.c — goggle "UVC monitor" probe/viewer.
 * Flips the goggle's one USB port from gadget(ECM) to HOST, waits for a UVC webcam to enumerate,
 * then streams it (YUYV) to the framebuffer /dev/fb0 (1920x1080 RGB565, stride 4096, 3 pages).
 * Because going host tears down the ECM net link, progress is shown as fullscreen colors:
 *   BLUE started · YELLOW host forced (PLUG CAMERA) · GREEN camera enumerated · then video
 *   RED no camera · MAGENTA camera not YUYV (would need MJPEG decode)
 * Recover by rebooting the goggle. Build: zig cc -target aarch64-linux-gnu.2.25 -O2 -s -o build/uvcfb device/uvcfb.c
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <stdint.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <linux/fb.h>
#include <linux/videodev2.h>

#define DWC2_BASE   0x8000000
#define GUSBCFG_OFF 0x0C
#define BLUE   0x001F
#define YELLOW 0xFFE0
#define GREEN  0x07E0
#define RED    0xF800
#define MAGENTA 0xF81F

static uint8_t *fb; static int fbw, fbh, fbvh, fbstride;

static void fb_fill(uint16_t c){
    for(int y=0;y<fbvh;y++){ uint16_t *row=(uint16_t*)(fb+(size_t)y*fbstride); for(int x=0;x<fbw;x++) row[x]=c; }
}
static int xioctl(int fd,unsigned long req,void*a){ int r; do{r=ioctl(fd,req,a);}while(r==-1&&errno==EINTR); return r; }
static inline uint16_t yuv565(int Y,int U,int V){
    int C=Y-16,D=U-128,E=V-128;
    int R=(298*C+409*E+128)>>8, G=(298*C-100*D-208*E+128)>>8, B=(298*C+516*D+128)>>8;
    if(R<0)R=0; if(R>255)R=255; if(G<0)G=0; if(G>255)G=255; if(B<0)B=0; if(B>255)B=255;
    return ((R>>3)<<11)|((G>>2)<<5)|(B>>3);
}

int main(void){
    /* 1. framebuffer */
    int fbfd=open("/dev/fb0",O_RDWR); if(fbfd<0) return 1;
    struct fb_var_screeninfo v; struct fb_fix_screeninfo f;
    ioctl(fbfd,FBIOGET_VSCREENINFO,&v); ioctl(fbfd,FBIOGET_FSCREENINFO,&f);
    fbw=v.xres; fbh=v.yres; fbvh=v.yres_virtual; fbstride=f.line_length;
    fb=mmap(0,(size_t)fbstride*fbvh,PROT_READ|PROT_WRITE,MAP_SHARED,fbfd,0);
    if(fb==MAP_FAILED) return 2;
    fb_fill(BLUE);
    sleep(2);                                   /* let the launch shell return before we kill the net */

    /* 2. tear down the ECM gadget */
    int g=open("/sys/kernel/config/usb_gadget/g1/UDC",O_WRONLY);
    if(g>=0){ if(write(g,"\n",1)<0){} close(g); }
    usleep(400000);

    /* 3. force dwc2 HOST mode: GUSBCFG.FrcHstMode=bit29 on, FrcDevMode=bit30 off */
    int mem=open("/dev/mem",O_RDWR|O_SYNC);
    if(mem>=0){
        uint8_t *r=mmap(0,0x1000,PROT_READ|PROT_WRITE,MAP_SHARED,mem,DWC2_BASE);
        if(r!=MAP_FAILED){ volatile uint32_t *gu=(uint32_t*)(r+GUSBCFG_OFF);
            uint32_t val=*gu; val|=(1u<<29); val&=~(1u<<30); *gu=val; }
    }
    fb_fill(YELLOW);                            /* PLUG THE CAMERA NOW */

    /* 4. wait up to ~45s for the camera node */
    int vfd=-1;
    for(int i=0;i<100;i++){ vfd=open("/dev/video0",O_RDWR); if(vfd>=0) break; usleep(300000); }
    if(vfd<0){
        system("{ echo '== devmem GUSBCFG =='; devmem 0x800000c; echo '== regdump =='; cat /sys/kernel/debug/8000000.usb/regdump; echo '== state =='; head -8 /sys/kernel/debug/8000000.usb/state; echo '== usb devices =='; ls -la /sys/bus/usb/devices/; echo '== video nodes =='; ls -la /dev/video* 2>&1; echo '== dmesg =='; dmesg | tail -60; } > /usrdata/uvc-dbg.log 2>&1");
        fb_fill(RED); return 3;
    }
    fb_fill(GREEN); sleep(1);

    /* 5. request YUYV */
    struct v4l2_format fmt; memset(&fmt,0,sizeof(fmt));
    fmt.type=V4L2_BUF_TYPE_VIDEO_CAPTURE;
    fmt.fmt.pix.width=640; fmt.fmt.pix.height=480;
    fmt.fmt.pix.pixelformat=V4L2_PIX_FMT_YUYV; fmt.fmt.pix.field=V4L2_FIELD_ANY;
    if(xioctl(vfd,VIDIOC_S_FMT,&fmt)<0){ fb_fill(RED); return 4; }
    if(fmt.fmt.pix.pixelformat!=V4L2_PIX_FMT_YUYV){ fb_fill(MAGENTA); return 5; }
    int cw=fmt.fmt.pix.width, ch=fmt.fmt.pix.height;

    /* 6. mmap buffers + stream on */
    struct v4l2_requestbuffers rb; memset(&rb,0,sizeof(rb));
    rb.count=4; rb.type=V4L2_BUF_TYPE_VIDEO_CAPTURE; rb.memory=V4L2_MEMORY_MMAP;
    if(xioctl(vfd,VIDIOC_REQBUFS,&rb)<0||rb.count<2){ fb_fill(RED); return 6; }
    void *bufs[8];
    for(unsigned i=0;i<rb.count;i++){
        struct v4l2_buffer b; memset(&b,0,sizeof(b));
        b.type=V4L2_BUF_TYPE_VIDEO_CAPTURE; b.memory=V4L2_MEMORY_MMAP; b.index=i;
        xioctl(vfd,VIDIOC_QUERYBUF,&b);
        bufs[i]=mmap(0,b.length,PROT_READ|PROT_WRITE,MAP_SHARED,vfd,b.m.offset);
        xioctl(vfd,VIDIOC_QBUF,&b);
    }
    int type=V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if(xioctl(vfd,VIDIOC_STREAMON,&type)<0){ fb_fill(RED); return 7; }

    /* 7. capture loop: YUYV -> RGB565 -> fb0, centered, all 3 pages (panning-proof) */
    int ox=(fbw-cw)/2; if(ox<0)ox=0; int oy=(fbh-ch)/2; if(oy<0)oy=0;
    int npages=fbvh/fbh; if(npages<1)npages=1;
    for(;;){
        struct v4l2_buffer b; memset(&b,0,sizeof(b));
        b.type=V4L2_BUF_TYPE_VIDEO_CAPTURE; b.memory=V4L2_MEMORY_MMAP;
        if(xioctl(vfd,VIDIOC_DQBUF,&b)<0){ usleep(1000); continue; }
        uint8_t *yuyv=(uint8_t*)bufs[b.index];
        for(int p=0;p<npages;p++){
            for(int y=0;y<ch && (oy+y)<fbh;y++){
                uint8_t *s=yuyv+(size_t)y*cw*2;
                uint16_t *d=(uint16_t*)(fb+(size_t)(p*fbh+oy+y)*fbstride)+ox;
                for(int x=0;x+1<cw;x+=2){ int Y0=s[0],U=s[1],Y1=s[2],V=s[3]; s+=4;
                    d[x]=yuv565(Y0,U,V); d[x+1]=yuv565(Y1,U,V); }
            }
        }
        xioctl(vfd,VIDIOC_QBUF,&b);
    }
}
