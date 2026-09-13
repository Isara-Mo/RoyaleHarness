#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <pthread.h>
uintptr_t g_libg_base=0x100000000ULL;
char manager[128],world[128],projectile[512],target[512],pdata[128],tdata[128];
void *objects[]={projectile,target,nullptr};
int tick=50,mode=0;
int32_t processed[8]={};
template<class T> void put(void *p,size_t off,T v) { memcpy(static_cast<char*>(p)+off,&v,sizeof(v)); }
template<class T> bool safe_field(const void *p,size_t off,T &v) {
    if(!p) return false; memcpy(&v,static_cast<const char*>(p)+off,sizeof(v));return true;
}
bool safe_bytes(const void *p,void *v,size_t n) {if(!p)return false;memcpy(v,p,n);return true;}
struct LiveReference {bool known=false;uint32_t id=0;};
LiveReference live_reference(void *p,void **arr,int n) {
    uint32_t id=0; int matches=0;
    for(int i=0;i<n;++i) if(arr[i]==p) {safe_field(p,8,id);++matches;}
    return matches==1 ? LiveReference{true,id} : LiveReference{};
}
bool projectile_layout_attested(){return true;}
bool deployment_context(void *&w,void *&m,int &t){w=world;m=manager;t=tick;return true;}
bool install_inline_hook(uintptr_t,const uint8_t*,size_t,const void*,void**,const char*){return true;}
#include "../probe/impact_events.inc"
int original(void *p,void *t,int x,int y,int z) {
    assert(p==projectile && t==target && x==123 && y==456 && z==789);
    if(mode==1) return 0;
    processed[1]=mode==2 ? 999 : 5000001;
    put(projectile,0x6c,2);
    if(mode==3) {processed[2]=5000001;put(projectile,0x6c,3);}
    if(mode==4) put(target,8,uint32_t(5000002));
    return 3;
}
void reset(int selected=0) {
    mode=selected; ++tick;
    pthread_mutex_lock(&g_impact_mutex);g_impact_world=nullptr;impact_epoch_locked(world,tick);pthread_mutex_unlock(&g_impact_mutex);
    put(projectile,0,g_libg_base+0x189cc28);
    put(projectile,8,uint32_t(7000001));put(target,8,uint32_t(5000001));
    put(projectile,0x10,static_cast<void*>(manager));put(target,0x10,static_cast<void*>(manager));
    put(projectile,0x48,static_cast<void*>(pdata));put(target,0x48,static_cast<void*>(tdata));
    put(projectile,0x78,0);put(target,0x78,1);put(projectile,0xac,28000011);put(target,0xac,26000002);
    put(pdata,0x40,uint32_t(10000032));put(tdata,0x40,uint32_t(3565953160));
    put(manager,8,objects);put(manager,0x10,2);put(manager,0x14,2);
    processed[0]=42;put(projectile,0x60,processed);put(projectile,0x68,8);put(projectile,0x6c,1);
    g_impact_original=original;g_impact_ready=true;
}
int main() {
    reset();assert(impact_observer(projectile,target,123,456,789)==3);
    assert(g_impact_sequence==1 && g_impact_rejected==0 && g_impacts[0].target.gid==3565953160U);
    char json[16384]={};int used=0;append_impact_events(json,used,sizeof(json),world,tick);
    assert(strstr(json,"\"complete\":true") && strstr(json,"\"target_id\":5000001"));
    for(int i=1;i<=4;++i) {reset(i);impact_observer(projectile,target,123,456,789);assert(g_impact_sequence==0);assert(g_impact_rejected==(i==1?0:1));}
    assert(!impact_new_target(processed,-1,2,8,5000001));
    processed[0]=5000001;processed[1]=999;
    assert(!impact_new_target(processed,1,2,8,5000001)); // older match is insufficient
    reset();impact_observer(projectile,target,123,456,789);
    used=0;append_impact_events(json,used,1024,world,tick);
    assert(used<1024 && strstr(json,"\"complete\":false") && strstr(json,"\"events\":[]"));
    used=0;append_impact_events(json,used,sizeof(json),world,tick+11);
    assert(strstr(json,"\"events\":[]"));
    g_impact_lost_tick=tick;used=0;append_impact_events(json,used,sizeof(json),world,tick+11);
    assert(strstr(json,"\"complete\":true"));
    tick+=20;g_impact_lost_tick=tick;used=0;append_impact_events(json,used,sizeof(json),world,tick);
    assert(strstr(json,"\"complete\":false"));
    puts("Native impact capture passed: native result, appended target, identity, history, capacity.");
    return 0;
}
