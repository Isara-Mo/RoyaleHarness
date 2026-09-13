// Exercise the production initializer observer against fake native objects.
// No game code is loaded and no UI, network state or game object is modified.
#define main impact_fixture_test_main
#include "native_impact_events.cpp"
#undef main
#include <vector>
#include "../probe/area_origin.inc"

char area_child[512],area_data[128],area_data_two[128];
void *area_live[8]={},*area_pending[8]={};
int area_original_calls=0,area_native_mode=0;
constexpr uintptr_t kAreaNativeResult=UINT64_C(0xfedcba9812345678);
bool area_exists(void *child=area_child) {
    AreaOriginBinding entry;bool current=false;return area_origin_lookup(child,entry,current);
}
uintptr_t fake_area_initializer(void *child,int level,void *parent) {
    ++area_original_calls;assert(level==11 && !area_exists(child));
    if(area_native_mode==1) {
        area_native_mode=0;
        assert(area_origin_initializer(child,level,parent)==kAreaNativeResult);
    }
    put(child,0x130,area_native_mode==2?static_cast<void*>(target):parent);
    if(area_native_mode==3)++tick;
    return kAreaNativeResult;
}
void prepare_area(void *child=area_child) {
    memset(child,0,512);put(child,0,g_libg_base+0x189c2e8);
    put(child,0x48,static_cast<void*>(area_data));
}
void area_setup() {
    reset();memset(area_data,0,sizeof(area_data));memset(area_data_two,0,sizeof(area_data_two));
    put(area_data,0x40,uint32_t(22000036));prepare_area();
    memset(area_live,0,sizeof(area_live));memset(area_pending,0,sizeof(area_pending));
    area_live[0]=projectile;area_live[1]=target;
    put(manager,8,area_live);put(manager,0x10,8);put(manager,0x14,2);
    put(manager,0x28,area_pending);put(manager,0x30,8);put(manager,0x34,0);
    g_area_origin_world=nullptr;area_origin_epoch(world,tick);g_area_origin_ready=true;
    g_area_initializer_original=fake_area_initializer;area_original_calls=area_native_mode=0;
}
void initialize_area(void *child=area_child,void *parent=projectile) {
    assert(area_origin_initializer(child,11,parent)==kAreaNativeResult);
}
void queue_area(void *child=area_child,uint32_t id=3000001) {
    put(child,8,id);put(child,0x10,static_cast<void*>(manager));put(child,0x78,0);put(child,0xac,26000084);
    area_pending[0]=child;put(manager,0x34,1);area_origin_registered(manager,child,0);
}
void commit_area(void *child=area_child) {
    area_live[2]=child;put(manager,0x14,3);put(manager,0x34,0);
    area_origin_registered(manager,child,1);
}
void fully_bind() {initialize_area();queue_area();commit_area();assert(area_exists());}
int main() {
    AreaOriginBinding entry;bool current=false;
    area_setup();initialize_area();
    assert(area_original_calls==1 && g_area_origin_calls==1 && g_area_origin_captured==1 && !area_exists());
    queue_area();assert(g_area_origin_queued==1 && !area_exists());
    ++tick;commit_area();assert(g_area_origin_promoted==1);
    assert(area_origin_lookup(area_child,entry,current) && current);
    assert(entry.source.id==7000001 && entry.source.gid==10000032 && entry.source.owner==0 &&
        entry.source.card==28000011 && entry.entity.id==3000001 && entry.child_gid==22000036 &&
        entry.captured_tick==entry.created_tick && entry.committed_tick==tick && entry.sequence==1);
    area_origin_registered(manager,area_child,1);assert(g_area_origin_promoted==1 && area_exists());
    char json[2048]={};int used=0;append_area_origin(json,used,sizeof(json),area_child);
    assert(strstr(json,"\"schema\":\"nulls-area-origin.v1\"") &&
        strstr(json,"\"parent_id\":7000001") && strstr(json,"\"current_parent\":true"));
    append_area_origin_diagnostics(json,used,sizeof(json));assert(strstr(json,"\"promoted\":1"));
    char small[16]="kept";used=4;append_area_origin(small,used,sizeof(small),area_child);
    assert(used==4 && strcmp(small,"kept")==0 && g_area_origin_overflow==1);
    append_area_origin_diagnostics(small,used,sizeof(small));assert(used==4 && strcmp(small,"kept")==0);

    // Removal need not coincide with polling. Keep the exact old source fact
    // whether the live object is absent or the engine already cleared +130.
    area_live[0]=nullptr;++tick;
    assert(area_origin_lookup(area_child,entry,current) && !current && entry.source.id==7000001);
    put(area_child,0x130,static_cast<void*>(nullptr));
    put(projectile,8,uint32_t(7000999));area_live[0]=projectile;
    assert(area_origin_lookup(area_child,entry,current) && !current && entry.source.id==7000001);

    // A source address reused while the non-null native reference still
    // claims it is invalid, and restoring old bytes does not rehabilitate it.
    area_setup();fully_bind();put(projectile,8,uint32_t(7000002));assert(!area_exists());
    put(projectile,8,uint32_t(7000001));assert(!area_exists());
    area_origin_registered(manager,area_child,1);assert(!area_exists());
    area_setup();fully_bind();put(projectile,0x78,1);assert(!area_exists());
    area_setup();fully_bind();put(projectile,0xac,26000000);assert(!area_exists());
    area_setup();fully_bind();put(pdata,0x40,uint32_t(10000033));assert(!area_exists());
    area_setup();fully_bind();put(area_child,0x130,static_cast<void*>(target));assert(!area_exists());
    area_setup();fully_bind();area_live[3]=projectile;put(manager,0x14,4);assert(!area_exists());
    area_setup();fully_bind();put(area_child,0x130,static_cast<void*>(nullptr));area_live[0]=nullptr;
    put(target,8,uint32_t(7000001));assert(!area_exists()); // ID recycled at a different pointer

    // Child manager/ID/asset/owner/card are continuous from queue admission,
    // through commit, through every serialized observation.
    area_setup();initialize_area();queue_area();put(area_child,8,uint32_t(3000002));commit_area();assert(!area_exists());
    area_setup();fully_bind();put(area_child,8,uint32_t(3000002));assert(!area_exists());
    area_setup();fully_bind();put(area_child,0x78,1);assert(!area_exists());
    area_setup();fully_bind();put(area_child,0xac,26000001);assert(!area_exists());
    area_setup();fully_bind();put(area_child,0x10,static_cast<void*>(world));assert(!area_exists());
    area_setup();fully_bind();memcpy(area_data_two,area_data,sizeof(area_data));
    put(area_child,0x48,static_cast<void*>(area_data_two));assert(!area_exists());
    area_setup();fully_bind();put(area_child,0,g_libg_base+0x189cc28);assert(!area_exists());
    area_setup();fully_bind();area_live[3]=area_child;put(manager,0x14,4);assert(!area_exists());
    area_setup();fully_bind();put(target,8,uint32_t(3000001));assert(!area_exists());
    area_setup();initialize_area();++tick;queue_area();commit_area();assert(!area_exists());
    area_setup();initialize_area();queue_area(area_child,7000001);commit_area();assert(!area_exists());
    area_setup();initialize_area();queue_area();put(area_child,0x78,1);commit_area();assert(!area_exists());

    // Pending/private/null parents never become a guessed live identity.
    area_setup();area_live[0]=nullptr;initialize_area();queue_area();area_live[0]=projectile;commit_area();
    assert(!area_exists() && g_area_origin_unknown_parent==1 && g_area_origin_captured==0);
    area_setup();initialize_area(area_child,reinterpret_cast<void*>(0x1234));queue_area();commit_area();
    assert(!area_exists() && g_area_origin_unknown_parent==1);
    area_setup();initialize_area(area_child,nullptr);queue_area();commit_area();assert(!area_exists());
    area_setup();initialize_area(area_child,area_child);queue_area();commit_area();assert(!area_exists());

    // An explicitly observed new initializer can establish another lifetime
    // at the address. Queries alone cannot do so; initialized live objects are
    // rejected, and an overlapping initializer cannot replace an outer call.
    area_setup();fully_bind();put(area_child,8,uint32_t(3000002));assert(!area_exists());
    area_live[2]=nullptr;put(manager,0x14,2);prepare_area();initialize_area();
    queue_area(area_child,3000002);commit_area();assert(area_exists() && g_area_origin_sequence==2);
    area_setup();fully_bind();initialize_area();assert(!area_exists() && area_original_calls==2);
    area_setup();area_native_mode=1;initialize_area();queue_area();commit_area();
    assert(!area_exists() && area_original_calls==2);
    area_setup();area_native_mode=2;initialize_area();queue_area();commit_area();assert(!area_exists());
    area_setup();area_native_mode=3;initialize_area();queue_area();commit_area();assert(!area_exists());

    area_setup();fully_bind();uint64_t old_epoch=g_area_origin_epoch;--tick;
    assert(!area_exists() && g_area_origin_epoch==old_epoch+1);
    area_setup();fully_bind();old_epoch=g_area_origin_epoch;
    char other_world[32]={};area_origin_epoch(other_world,tick);assert(g_area_origin_epoch==old_epoch+1);
    assert(!area_exists());
    area_setup();g_area_origin_ready=false;initialize_area();assert(area_original_calls==1 && g_area_origin_calls==0);

    // The fixed budget never evicts a prior identity/tombstone for a different
    // pointer. A fresh initializer for the same address reuses only its slot.
    area_setup();std::vector<unsigned char> many(size_t(kAreaOriginCapacity+1)*512);
    for(int i=0;i<=kAreaOriginCapacity;++i) {
        void *child=many.data()+size_t(i)*512;prepare_area(child);initialize_area(child);
    }
    assert(g_area_origin_captured==kAreaOriginCapacity && g_area_origin_overflow==1 &&
        area_original_calls==kAreaOriginCapacity+1);
    void *first=many.data();queue_area(first);commit_area(first);assert(area_exists(first));
    puts("Native AEO origin passed: initializer ABI, exact admission, historical parent, reuse, epochs, capacity.");
    return 0;
}
