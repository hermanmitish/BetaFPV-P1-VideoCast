/* venc8tap.c — LD_PRELOAD shim: tee the goggle's EXISTING composited H.265 encoder (chn8, the
 * stream RTSP already produces, OSD burned in) out the USB-ECM link over TCP, low latency, ZERO
 * extra memory (we create no encoder — a 2nd 1080p encoder doesn't fit the live-video MMZ budget).
 *
 * chn8 runs ~60fps with an effectively infinite GOP: it emits VPS/SPS/PPS+IDR once at start and then
 * only P-frames, so a viewer joining mid-stream can't decode. We fix that:
 *   - interpose AR_MPI_VENC_GetStream(chn8): tee each pack's H.265 (Annex-B) to a TCP client;
 *   - cache the parameter sets (VPS/SPS/PPS) whenever they appear;
 *   - on a new client (and once at startup) call AR_MPI_VENC_RequestIDR(8,1) to force a fresh IDR,
 *     send the cached params first, and only start forwarding at a keyframe NAL.
 * The real consumer still gets its stream untouched; writes are non-blocking/best-effort.
 *   VENC_STREAM_S: pstPack@+0x00, u32PackCount@+0x08.  VENC_PACK_S(152B): pu8Addr@+0x08, u32Len@+0x10.
 *
 * Host:  ffplay -fflags nobuffer -flags low_delay -f hevc tcp://10.55.0.1:9000
 * Build: zig cc -target aarch64-linux-gnu.2.25 -shared -fPIC -O2 -s -o build/venc8tap.so device/venc8tap.c -ldl -lpthread
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <unistd.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <fcntl.h>
#include <pthread.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>

#define CHN8       8
#define PORT       9000
#define ST_PSTPACK 0x00
#define ST_PACKCNT 0x08
#define PACK_SIZE  152
#define PK_ADDR    0x08
#define PK_LEN     0x10
#define MAXPACKS   32

static int  (*real_get)(int,void*,int);
static int  (*req_idr)(int,int);
static volatile int g_client = -1;
static volatile int g_stream = 0;          /* started forwarding (past a keyframe) for this client */
static int g_dumped = 0;
static uint8_t g_params[8192]; static volatile int g_plen = 0;   /* cached VPS/SPS/PPS blob */

static void wr_fd(int fd, const uint8_t *p, uint32_t n){
    if (fd < 0) return;
    uint32_t off=0; int spins=0;
    while (off<n){ ssize_t w=write(fd,p+off,n-off);
        if(w>0){off+=w;spins=0;continue;}
        if(++spins>750){ close(fd); if(g_client==fd)g_client=-1; return; }  /* ~150ms grace, then drop a dead client */
        usleep(200); }
}

/* classify NALs in an Annex-B buffer */
static void scan_nals(const uint8_t*d,uint32_t n,int*has_param,int*has_key,int*only_param){
    *has_param=0;*has_key=0;*only_param=1; int any=0;
    for(uint32_t i=0;i+4<n;i++){
        if(d[i]==0&&d[i+1]==0&&d[i+2]==1){
            int t=(d[i+3]>>1)&0x3f; any=1;
            if(t==32||t==33||t==34){*has_param=1;}
            else { *only_param=0; if(t==19||t==20||t==21)*has_key=1; }
        }
    }
    if(!any)*only_param=0;
}

static void on_new_client(void){
    g_stream=0;
    if(g_plen>0 && g_client>=0) wr_fd(g_client,g_params,g_plen);  /* prepend cached params */
    if(req_idr) req_idr(CHN8,1);                                  /* force a fresh IDR */
}

static void *accept_thread(void *a){
    (void)a;
    /* resolve RequestIDR up front so the very first client reliably gets a forced keyframe */
    for (int i=0;i<100 && !req_idr;i++){ req_idr=(int(*)(int,int))dlsym(RTLD_DEFAULT,"AR_MPI_VENC_RequestIDR"); if(!req_idr) usleep(100000); }
    int ls = socket(AF_INET, SOCK_STREAM, 0);
    int one=1; setsockopt(ls,SOL_SOCKET,SO_REUSEADDR,&one,sizeof(one));
    struct sockaddr_in sa; memset(&sa,0,sizeof(sa));
    sa.sin_family=AF_INET; sa.sin_addr.s_addr=INADDR_ANY; sa.sin_port=htons(PORT);
    if (bind(ls,(struct sockaddr*)&sa,sizeof(sa))<0) return NULL;
    listen(ls,1);
    while (1){
        int c = accept(ls,NULL,NULL);
        if (c<0){ usleep(100000); continue; }
        int one2=1; setsockopt(c,IPPROTO_TCP,TCP_NODELAY,&one2,sizeof(one2));
        if (g_client>=0) close(g_client);
        g_client = c;
        on_new_client();
        fcntl(c,F_SETFL,O_NONBLOCK);
    }
    return NULL;
}

int AR_MPI_VENC_GetStream(int chn, void *pstStream, int ms){
    if (!real_get) real_get = (int(*)(int,void*,int))dlsym(RTLD_NEXT,"AR_MPI_VENC_GetStream");
    int r = real_get(chn, pstStream, ms);
    if (chn==CHN8 && r==0 && pstStream){
        if (!req_idr) req_idr = (int(*)(int,int))dlsym(RTLD_DEFAULT,"AR_MPI_VENC_RequestIDR");
        uint8_t *st = (uint8_t*)pstStream;
        void   *packs = *(void**)(st+ST_PSTPACK);
        uint32_t cnt  = *(uint32_t*)(st+ST_PACKCNT);
        if (!g_dumped && packs){ g_dumped=1;
            FILE*f=fopen("/tmp/venc8tap_dbg","w");
            if(f){ fprintf(f,"packcount=%u\npack0[0x40]:",cnt); unsigned char*p=(unsigned char*)packs;
                for(int i=0;i<0x40;i++) fprintf(f," %02x",p[i]); fprintf(f,"\n"); fclose(f);} }
        for (uint32_t i=0;i<cnt && i<MAXPACKS;i++){
            uint8_t *pk = (uint8_t*)packs + (size_t)i*PACK_SIZE;
            uint8_t *addr = *(uint8_t**)(pk+PK_ADDR);
            uint32_t len  = *(uint32_t*)(pk+PK_LEN);
            if (!addr || !len) continue;
            int has_param,has_key,only_param; scan_nals(addr,len,&has_param,&has_key,&only_param);
            if (only_param && len<=sizeof(g_params)){ memcpy(g_params,addr,len); g_plen=len; }  /* cache params */
            if (g_client>=0){
                if (!g_stream){ if (has_param||has_key) g_stream=1; else continue; }
                wr_fd(g_client, addr, len);
            }
        }
    }
    return r;
}

__attribute__((constructor))
static void venc8tap_start(void){
    pthread_t t; pthread_create(&t,NULL,accept_thread,NULL); pthread_detach(t);
}
