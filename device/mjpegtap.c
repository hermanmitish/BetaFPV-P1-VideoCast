/* mjpegtap.c — LD_PRELOAD shim: mirror the goggle's video as a low-latency MJPEG stream over
 * USB-ACM by becoming a PARALLEL CLIENT of the firmware's frame pipeline (the same multi-consumer
 * mechanism the recorder/RTSP encoder use). A single intra-only stream: no frame-sync, no dual-
 * strip stacking. Host:  cat /dev/cu.usbmodemXXXX | ffplay -f mjpeg -i -
 *
 * Why a pipeline client (not a VO_SendFrame tap): the encoder is fed by
 *   client = AR_LDRT_PIPELINE_CreateClientRx();
 *   AR_LDRT_PIPELINE_GetFrameByClientRx(client, frame_456B, 50ms);   0 = got a frame
 *   AR_MPI_VENC_SendFrame(chn, frame+8, ms);  AR_LDRT_PIPELINE_ReleaseFrameRx(client);
 * Each client gets its own frame ref, so we do not steal/lock the display's frame (an earlier
 * version teed the VO display frame straight into a VENC and starved the live video; this won't).
 * We add our own client, create our own MJPEG VENC channel sized to the real frame, feed it, and
 * pump JPEGs out /dev/ttyGS1.  All firmware funcs are dlsym'd, so it is version-proof.
 *
 * Build: zig cc -target aarch64-linux-gnu.2.25 -shared -fPIC -O2 -s -o build/mjpegtap.so device/mjpegtap.c -ldl -lpthread
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <termios.h>
#include <unistd.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <pthread.h>

#define ENTYPE_MJPEG 0x3ea
#define RC_MJPEGCBR  7         /* MJPEG needs an MJPEG-specific RC mode: 7=CBR 8=VBR 9=FIXQP */
#define TAP_BITRATE  8000      /* kbps-ish; tune */
#define TAP_FPS      30
#define VID_DEV      "/dev/ttyGS1"
#define FRAMEBUF     512       /* GetFrameByClientRx fills a 456 (0x1c8) byte struct */
#define VINFO_OFF    8         /* VENC_SendFrame is passed framebuf+8 */

/* VENC_STREAM_S / VENC_PACK_S (confirmed via /tmp/mjpeg_dbg on first run) */
#define ST_PSTPACK   0x00
#define ST_PACKCNT   0x08
#define PACK_SIZE    152
#define PK_ADDR      0x08
#define PK_LEN       0x18
#define PK_OFF       0x1c
#define MAXPACKS     16

static void* (*p_create)(void);
static int   (*p_getframe)(void*, void*, int);
static int   (*p_release)(void*);
static void  (*p_destroy)(void*);
static int   (*venc_init)(int,int,int,int,int,int,int,int);
static int   (*mpi_start)(int, void*);
static int   (*mpi_send)(int, void*, int);   /* real AR_MPI_VENC_SendFrame (via RTLD_NEXT) */
static int   (*mpi_get)(int, void*, int);
static int   (*mpi_rel)(int, void*);

static int g_chn = -1, g_vfd = -1, g_dumped = 0;
static volatile int g_ready = 0;       /* our MJPEG channel is created + started */
static volatile int g_app_ready = 0;   /* arlink_fpv is initialized & encoding (drone up) */

static int open_raw(const char *p){
    int fd = open(p, O_WRONLY | O_NONBLOCK | O_NOCTTY);
    if (fd < 0) return -1;
    struct termios t; if (tcgetattr(fd,&t)==0){ cfmakeraw(&t); tcsetattr(fd,TCSANOW,&t); }
    return fd;
}
static void wr(const uint8_t *p, uint32_t n){
    if (g_vfd < 0) g_vfd = open_raw(VID_DEV);
    if (g_vfd < 0) return;
    uint32_t off=0; int spins=0;
    while (off<n){ ssize_t w=write(g_vfd,p+off,n-off);
        if(w>0){off+=w;spins=0;continue;} if(++spins>40)break; usleep(200);} }

/* pull encoded JPEGs from our MJPEG channel and pump them out the ACM port */
static void *pull_thread(void *a){
    (void)a;
    uint8_t stream[64];
    void *packs = malloc((size_t)PACK_SIZE*MAXPACKS);
    while (!g_ready) usleep(5000);
    while (1){
        memset(stream,0,sizeof(stream));
        *(void   **)(stream+ST_PSTPACK) = packs;
        *(uint32_t *)(stream+ST_PACKCNT) = MAXPACKS;
        if (mpi_get(g_chn, stream, 200) != 0){ usleep(2000); continue; }
        uint32_t cnt = *(uint32_t*)(stream+ST_PACKCNT);
        for (uint32_t i=0;i<cnt && i<MAXPACKS;i++){
            uint8_t *pk = (uint8_t*)packs + (size_t)i*PACK_SIZE;
            uint8_t *addr = *(uint8_t**)(pk+PK_ADDR);
            uint32_t len  = *(uint32_t*)(pk+PK_LEN);
            uint32_t poff = *(uint32_t*)(pk+PK_OFF);
            if (addr && len>poff) wr(addr+poff, len-poff);
        }
        mpi_rel(g_chn, stream);
    }
    return NULL;
}

static int resolve(void){
    p_create   = dlsym(RTLD_DEFAULT,"AR_LDRT_PIPELINE_CreateClientRx");
    p_getframe = dlsym(RTLD_DEFAULT,"AR_LDRT_PIPELINE_GetFrameByClientRx");
    p_release  = dlsym(RTLD_DEFAULT,"AR_LDRT_PIPELINE_ReleaseFrameRx");
    p_destroy  = dlsym(RTLD_DEFAULT,"AR_LDRT_PIPELINE_DestroyClientRx");
    venc_init  = dlsym(RTLD_DEFAULT,"ar_lowdelay_module_venc_init");
    mpi_start  = dlsym(RTLD_DEFAULT,"AR_MPI_VENC_StartRecvFrame");
    mpi_send   = dlsym(RTLD_NEXT,   "AR_MPI_VENC_SendFrame");  /* real one, skip our interpose */
    mpi_get    = dlsym(RTLD_DEFAULT,"AR_MPI_VENC_GetStream");
    mpi_rel    = dlsym(RTLD_DEFAULT,"AR_MPI_VENC_ReleaseStream");
    return p_create&&p_getframe&&p_release&&venc_init&&mpi_start&&mpi_send&&mpi_get&&mpi_rel;
}

static void *feed_thread(void *a){
    (void)a;
    while (!resolve()) sleep(1);                 /* wait until libldrt/MPI symbols are up */
    while (!g_app_ready) usleep(100000);         /* wait until arlink is initialized & encoding */
    usleep(500000);                              /* small settle after first encode */
    void *client = NULL;
    while (!(client = p_create())) usleep(200000);   /* now safe: RX pipeline is up */
    uint8_t fb[FRAMEBUF];
    int dbgn = 0;
    while (1){
        memset(fb,0,456);
        if (p_getframe(client, fb, 50) != 0) continue;   /* no frame yet (no video) */
        void *vinfo = fb + VINFO_OFF;
        if (g_chn < 0){                                  /* first frame: size + create channel */
            uint32_t w = *(uint32_t*)((uint8_t*)vinfo+4);   /* fb+0x0c */
            uint32_t h = *(uint32_t*)((uint8_t*)vinfo+8);   /* fb+0x10 */
            if (w<160||w>4096||h<120||h>4096){ w=1920; h=1080; }
            int rc[6]; for(int k=0;k<6;k++) rc[k]=99;
            for (int c=10;c<=15;c++){
                rc[c-10]=venc_init(c,ENTYPE_MJPEG,RC_MJPEGCBR,(int)w,(int)h,TAP_BITRATE,0,TAP_FPS);
                if (rc[c-10]==0){ g_chn=c; break; } }
            if (!g_dumped){ g_dumped=1; FILE*f=fopen("/tmp/mjpeg_dbg","w");
                if(f){ fprintf(f,"client=%p w=%u h=%u chosen_chn=%d\n",client,w,h,g_chn);
                    fprintf(f,"venc_init rc[10..15]= %d %d %d %d %d %d\n",rc[0],rc[1],rc[2],rc[3],rc[4],rc[5]);
                    fprintf(f,"frame[0x60]:");
                    for(int i=0;i<0x60;i++){ if(i%16==0)fprintf(f,"\n %03x:",i); fprintf(f," %02x",fb[i]); }
                    fprintf(f,"\n"); fclose(f);} }
            if (g_chn>=0){ int32_t recv=-1; mpi_start(g_chn,&recv); g_ready=1; }
        }
        if (g_ready){
            int r = mpi_send(g_chn, vinfo, 100);
            if (!g_dumped || dbgn<1){ dbgn=1; FILE*f=fopen("/tmp/mjpeg_send","w");
                if(f){ fprintf(f,"chn=%d send_ret=%d (0x%x)\n",g_chn,r,r); fclose(f);} }
        }
        p_release(client);
    }
    return NULL;
}

/* Interpose AR_MPI_VENC_SendFrame purely as a readiness signal: arlink_fpv only calls it once it
 * is fully initialized and actually encoding video (i.e. a drone is up). We pass straight through;
 * our worker waits for this before touching the pipeline/VENC, so it never races boot-time init. */
int AR_MPI_VENC_SendFrame(int chn, void *frame, int ms){
    static int (*real)(int,void*,int) = NULL;
    if (!real) real = (int(*)(int,void*,int))dlsym(RTLD_NEXT,"AR_MPI_VENC_SendFrame");
    g_app_ready = 1;
    return real(chn, frame, ms);
}

__attribute__((constructor))
static void mjpegtap_start(void){
    pthread_t a,b;
    pthread_create(&a,NULL,feed_thread,NULL); pthread_detach(a);
    pthread_create(&b,NULL,pull_thread,NULL); pthread_detach(b);
}
