/* vencprobe.c — LD_PRELOAD diagnostic: can we create our OWN encoder channel, and how much MMZ
 * does it cost? Tries several configs on FREE channels (12-15) once the app is actually encoding
 * (gated on AR_MPI_VENC_SendFrame). Also records which channels the app itself uses. Each channel
 * is created then immediately destroyed (no frame feeding). Read /tmp/vencprobe over telnet.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <pthread.h>

static int (*venc_init)(int,int,int,int,int,int,int,int);
static int (*venc_destroy)(int);
static volatile int g_app_ready = 0;
static volatile unsigned g_app_chns = 0;     /* bitmask of channels the app sends frames to */

static void drop_caches(void){
    int fd=open("/proc/sys/vm/drop_caches",O_WRONLY); if(fd>=0){ (void)!write(fd,"3\n",2); close(fd);} }
static void cat_tail(FILE*out,const char*path,const char*tag){
    FILE*f=fopen(path,"r"); if(!f){ fprintf(out,"%s: (n/a)\n",tag); return; }
    fprintf(out,"%s:\n",tag); char b[256]; while(fgets(b,sizeof(b),f)){
        if(strstr(b,"MemFree")||strstr(b,"MemAvail")||strstr(b,"MMZ_USE_INFO")||strstr(b,"remain=")) fputs(b,out);} fclose(f); }

struct test { int chn,type,rc,w,h; const char*name; };

static void *probe(void *a){
    (void)a;
    int waited=0; while(!g_app_ready && waited<600){ usleep(100000); waited++; }   /* up to 60s for video */
    sleep(2);
    /* lazy helper: retry-resolve until present */
    for(int i=0;i<50 && !venc_init;i++){ venc_init=dlsym(RTLD_DEFAULT,"ar_lowdelay_module_venc_init"); if(!venc_init) usleep(100000); }
    venc_destroy = dlsym(RTLD_DEFAULT,"AR_MPI_VENC_DestroyChn");
    drop_caches();
    FILE*o=fopen("/tmp/vencprobe","w"); if(!o) return NULL;
    fprintf(o,"app_ready=%d app_chns_bitmask=0x%x venc_init=%p destroy=%p\n",
            g_app_ready,g_app_chns,(void*)venc_init,(void*)venc_destroy);
    fprintf(o,"app uses chns:"); for(int c=0;c<32;c++) if(g_app_chns&(1u<<c)) fprintf(o," %d",c); fprintf(o,"\n");
    cat_tail(o,"/proc/meminfo","mem(before)"); cat_tail(o,"/proc/media-mem","mmz(before)");
    struct test T[] = {
        {12, 0x3ea, 7, 1920,1080, "MJPEG 1080p"},
        {13, 0x3ea, 7, 1280,720,  "MJPEG 720p"},
        {14, 0x60,  0, 1920,1080, "H264  1080p"},
        {15, 0x109, 0, 1920,1080, "H265  1080p"},
    };
    for(unsigned i=0;i<sizeof(T)/sizeof(T[0]);i++){
        int rc = venc_init ? venc_init(T[i].chn,T[i].type,T[i].rc,T[i].w,T[i].h,8000,30,30) : -999;
        fprintf(o,"%-14s chn=%d enType=0x%-4x rc=%d (0x%x)\n",T[i].name,T[i].chn,T[i].type,rc,(unsigned)rc);
        cat_tail(o,"/proc/media-mem","  mmz-after");
        fflush(o);
        if(rc==0 && venc_destroy) venc_destroy(T[i].chn);
    }
    fprintf(o,"DONE\n"); fclose(o);
    return NULL;
}

int AR_MPI_VENC_SendFrame(int chn, void *frame, int ms){
    static int (*real)(int,void*,int)=NULL;
    if(!real) real=(int(*)(int,void*,int))dlsym(RTLD_NEXT,"AR_MPI_VENC_SendFrame");
    if(chn>=0&&chn<32) g_app_chns|=(1u<<chn);
    g_app_ready=1; return real(chn,frame,ms);
}

__attribute__((constructor))
static void start(void){ pthread_t t; pthread_create(&t,NULL,probe,NULL); pthread_detach(t); }
