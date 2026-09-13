// Runs the actual probe queue-binding code against fake engine storage.
// No game process or engine functions are accessed by this executable.
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <pthread.h>
#include <array>

std::atomic<void*> g_live_manager{nullptr};
uintptr_t g_libg_base=0;
int test_tick=100;
int fake_tick(void*) { return test_tick; }
int (*g_game_state_tick)(void*)=fake_tick;
template<class T> bool safe_field(const void *p,size_t off,T &out) {
    if (!p) return false; memcpy(&out,static_cast<const char*>(p)+off,sizeof(T)); return true;
}
bool safe_bytes(const void *p,void *out,size_t n) { if (!p) return false; memcpy(out,p,n); return true; }
bool runtime_vector(const void *p,size_t off,int limit,void *&data,int &count) {
    int cap=0;
    return safe_field(p,off,data) && safe_field(p,off+8,cap) && safe_field(p,off+12,count) &&
        count>=0 && cap>=count && cap<=limit && (!count || data);
}
struct CardSelection { void *data=nullptr,*secondary=nullptr; uint32_t packed=0,padding=0; };
void cr_read_card_selection(void*,void*,int,void*,void*) {}
bool install_inline_hook(uintptr_t,const uint8_t*,size_t,const void*,void**,const char*) { return false; }
#include "../probe/causal_deployment.inc"

template<class T> void put(void *p,size_t off,T value) { memcpy(static_cast<char*>(p)+off,&value,sizeof(value)); }
alignas(16) char manager[512],world[512],player[512],object_manager[512],object[512],other[512];
void *live[2],*queue[2];
void fake_register(void*,void *p,int,int commit) {
    if (commit) { live[0]=p; put(object_manager,0x14,1); }
}
void reset() {
    memset(object,0,sizeof(object)); memset(other,0,sizeof(other));
    put(manager,0xa8,static_cast<void*>(world));
    put(world,0xe0,static_cast<void*>(player));
    put(player,0x10,static_cast<void*>(object_manager));
    put(object_manager,8,static_cast<void*>(live)); put(object_manager,0x10,2); put(object_manager,0x14,0);
    put(object_manager,0x28,static_cast<void*>(queue)); put(object_manager,0x30,2); put(object_manager,0x34,1);
    queue[0]=object;
    put(object,8,uint32_t(5000100)); put(object,0x10,static_cast<void*>(object_manager)); put(object,0x78,0);
    g_live_manager=manager; g_deployment_spawn_original=fake_register; g_deployment_hooks_ready=true;
    g_deployment_world=nullptr; deployment_epoch(world,test_tick);
    g_deployment_scope={world,object_manager,g_deployment_epoch,11,0,test_tick,0,0,26000012,0};
}
void enqueue() { deployment_spawn(object_manager,object,0,0); assert(g_deployment_queued==1); }
void absent() {
    char output[2048]={}; int used=0; append_deployment_member(output,used,sizeof(output),object); assert(used==0);
}
int main() {
    reset(); enqueue(); absent();
    // Promotion occurs after the original producer scope and at any forwarding
    // call site, with exact native identity and live membership preserved.
    g_deployment_scope={}; ++test_tick; deployment_spawn(object_manager,object,0,1);
    assert(g_deployment_promoted==1 && g_deployment_bound==1);
    char output[2048]={}; int used=0; append_deployment_member(output,used,sizeof(output),object);
    assert(strstr(output,"consume_card_queued_object") && strstr(output,"\"sequence\":11"));

    reset(); enqueue(); g_deployment_scope={}; put(object,8,uint32_t(5000101));
    deployment_spawn(object_manager,object,0,1); assert(g_deployment_promoted==0); absent();

    reset(); enqueue(); g_deployment_scope={}; memcpy(other,object,sizeof(object));
    deployment_spawn(object_manager,other,0,1); assert(g_deployment_promoted==0);

    reset(); enqueue(); g_deployment_scope={}; put(object,0x78,1);
    deployment_spawn(object_manager,object,0,1); assert(g_deployment_promoted==0); absent();

    reset(); enqueue(); g_deployment_scope={}; --test_tick;
    deployment_spawn(object_manager,object,0,1); assert(g_deployment_promoted==0); absent();

    reset(); enqueue(); g_deployment_scope.sequence=12;
    deployment_spawn(object_manager,object,0,1); assert(g_deployment_promoted==0); absent();

    reset(); g_deployment_spawn_depth=1; deployment_spawn(object_manager,object,0,0);
    g_deployment_spawn_depth=0; assert(g_deployment_queued==0); absent();
    puts("Native queue tests passed: continuity, pending visibility, ID/pointer/owner/epoch conflicts, root conflict, nesting.");
}
