#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <pthread.h>

uintptr_t g_libg_base=0;
uint64_t g_deployment_epoch=1;
bool g_deployment_hooks_ready=true;
void (*g_spawn_relation_observer)(void*,void*,int)=nullptr;
char world[512],objects[512],parent[512],child[512],data[512],other[512],component[512];
void *live[4]; int tick=100;
template<class T> void put(void *p,size_t off,T value) { memcpy(static_cast<char*>(p)+off,&value,sizeof(value)); }
template<class T> bool safe_field(const void *p,size_t off,T &out) {
    if (!p) return false; memcpy(&out,static_cast<const char*>(p)+off,sizeof(T)); return true;
}
bool safe_bytes(const void *p,void *out,size_t n) { if (!p) return false; memcpy(out,p,n); return true; }
bool runtime_vector(const void *p,size_t off,int limit,void *&v,int &count) {
    int cap=0; return safe_field(p,off,v) && safe_field(p,off+8,cap) && safe_field(p,off+12,count) &&
        count>=0 && count<=cap && cap<=limit && (!count || v);
}
struct LiveReference { bool known=false; uint32_t id=0; };
LiveReference live_reference(void *p,void **v,int count) {
    if (!p) return {true,0};
    for(int i=0;i<count;++i) if(v[i]==p) {
        uint32_t id=0; safe_field(p,8,id); int matches=0;
        for(int j=0;j<count;++j) { uint32_t other_id=0; safe_field(v[j],8,other_id); matches+=other_id==id; }
        return id && matches==1 ? LiveReference{true,id} : LiveReference{};
    }
    return {};
}
void *runtime_component(void *p,int index) { return p==parent && index==3 ? component : nullptr; }
void deployment_epoch(void*,int) {}
bool deployment_context(void *&w,void *&o,int &t) { w=world; o=objects; t=tick; return true; }
bool install_inline_hook(uintptr_t,const uint8_t*,size_t,void*,void**,const char*) { return false; }
#include "../probe/spawn_relations.inc"
extern "C" void summon_mode1_entry() {}
void reset() {
    ++g_deployment_epoch; summon_epoch(); tick=100;
    memset(child,0,sizeof(child)); memset(parent,0,sizeof(parent));
    live[0]=parent; live[1]=child;
    put(objects,8,static_cast<void*>(live)); put(objects,0x10,4); put(objects,0x14,1);
    put(parent,8,uint32_t(100)); put(parent,0x78,0);
    put(child,8,uint32_t(200)); put(child,0x10,static_cast<void*>(objects)); put(child,0x78,0);
    put(child,0x48,static_cast<void*>(data)); put(data,0x40,uint32_t(139000010));
    g_summon_scope={}; auto &s=g_summon_scope;
    s.world=world; s.objects=objects; s.source=parent; s.expected_data=data;
    s.epoch=g_deployment_epoch; s.action_sequence=1; s.source_id=100; s.source_gid=139000010;
    s.source_card=13000010; s.source_owner=0; s.action_gid=143000000; s.tick=100;
    g_summon_hooks_ready=true;
}
void admit() { put(objects,0x14,2); summon_registered(objects,child,1); }
bool published() {
    char json[4096]={}; int used=0; append_spawn_relation(json,used,sizeof(json),child); return used>0;
}
void capture() { summon_capture_child(objects,child,1); summon_registered(objects,child,0); }
uint32_t observed_tag=0;
void tagged_original(void*,void*,uint32_t tag) { observed_tag=tag; }
int main() {
    reset(); capture(); assert(!published()); g_summon_scope={}; admit(); assert(published());
    assert(g_summon_promoted==1);
    // Historical parent survives removal within the exact production tick.
    reset(); capture(); live[0]=child; put(objects,0x14,1);
    summon_registered(objects,child,1); assert(published());
    reset(); capture(); ++tick; admit(); assert(!published());
    reset(); capture(); put(parent,8,uint32_t(101)); admit(); assert(!published());
    reset(); capture(); live[2]=other; put(other,8,uint32_t(100)); put(objects,0x14,3);
    summon_registered(objects,child,1); assert(!published());
    reset(); capture(); put(child,8,uint32_t(201)); admit(); assert(!published());
    reset(); capture(); put(child,0x78,1); admit(); assert(!published());
    reset(); capture(); put(child,0x48,static_cast<void*>(other)); admit(); assert(!published());
    reset(); capture(); ++g_deployment_epoch; admit(); assert(!published());
    reset(); capture(); summon_capture_child(objects,child,1); admit(); assert(!published());
    reset(); summon_capture_child(objects,parent,1); assert(g_summon_count==0);
    reset(); g_summon_scope={}; summon_capture_child(objects,child,1); assert(g_summon_count==0);
    reset(); for(auto &m:g_summons) m={}; g_summon_count=4096;
    summon_capture_child(objects,child,1); assert(g_summon_overflow==1);
    // Exact mode-1 caller/source/request joins before commit.
    reset(); g_summon_scope.mode=1; char request[128]={};
    put(request,0,static_cast<void*>(data)); put(request,8,static_cast<void*>(parent));
    summon_mode1_bridge(objects,child,1,0,reinterpret_cast<void*>(0xf24bb0),parent,request+0x48);
    assert(g_summon_count==1); admit(); assert(published());
    reset(); g_summon_scope.mode=1;
    summon_mode1_bridge(objects,child,1,0,reinterpret_cast<void*>(0xf24bb4),parent,request+0x48);
    assert(g_summon_count==0);
    reset(); char buff[512]={},entry[128]={}; void *entries[]={entry};
    put(parent,0x48,static_cast<void*>(data)); put(parent,0xac,13000010);
    put(component,8,static_cast<void*>(parent)); put(component,0x18,static_cast<void*>(entries));
    put(component,0x20,1); put(component,0x24,1);
    put(entry,0x20,static_cast<void*>(component)); put(entry,0x18,static_cast<void*>(buff));
    put(buff,0,uintptr_t(0x1882500)); put(buff,0x40,uint32_t(143000000));
    put(buff,0x170,static_cast<void*>(data));
    SummonScope scope; assert(summon_buff_scope(entry,scope)); assert(scope.source_id==100 && scope.kind==1);
    g_summon_scope=scope; capture(); admit(); assert(published());
    put(component,0x24,0); SummonScope unknown; assert(!summon_buff_scope(entry,unknown));
    reset(); g_summon_tagged_original=tagged_original;
    summon_tagged(objects,child,4000000000U); assert(observed_tag==4000000000U);
    assert(g_summons[0].route==3); admit(); assert(published());
    puts("Native spawn relations passed: pending admission, historical source, pointer/ID/owner/epoch/tick conflicts, unknown source, capacity, mode-1 request.");
}
