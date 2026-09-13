// Runs the production observer against synthetic objects, never game state.
#define main impact_fixture_test_main
#include "native_impact_events.cpp"
#undef main
#include <vector>
#include "../probe/projectile_origin.inc"
char projectile_table[160],other_source[512],other_source_data[128];
int native_calls=0;
constexpr uintptr_t native_result=UINT64_C(0xfedcba9887654321);
bool lookup() {ProjectileOrigin out;return projectile_origin_lookup(projectile,world,manager,tick,out);}
void setup_origin() {
    reset();memset(projectile,0,sizeof(projectile));memset(target,0,sizeof(target));
    memset(pdata,0,sizeof(pdata));memset(tdata,0,sizeof(tdata));
    memset(projectile_table,0,sizeof(projectile_table));memset(other_source,0,sizeof(other_source));
    put(projectile,0,g_libg_base+0x189cc28);put(projectile,0x48,static_cast<void*>(pdata));
    put(projectile,0x78,1);put(projectile,0xac,26000084);put(projectile,0x100,static_cast<void*>(target));
    put(pdata,0x10,static_cast<void*>(projectile_table));put(projectile_table,0x84,10);
    put(pdata,0x40,uint32_t(10000075));
    put(target,8,uint32_t(5000001));put(target,0x10,static_cast<void*>(manager));
    put(target,0x48,static_cast<void*>(tdata));put(tdata,0x40,uint32_t(34000081));
    put(target,0x78,1);put(target,0xac,26000084);
    objects[0]=target;objects[1]=nullptr;put(manager,0x14,1);
    g_projectile_origin_world=nullptr;g_projectile_origin_ready=true;
    projectile_origin_epoch_locked(world,manager,tick);native_calls=0;
}
void queue_origin() {projectile_origin_queued(manager,projectile);assert(g_projectile_origin_captured==1);assert(!lookup());}
void commit_origin() {
    put(projectile,8,uint32_t(4000004));put(projectile,0x10,static_cast<void*>(manager));
    objects[1]=projectile;put(manager,0x14,2);
    projectile_origin_registered(manager,projectile,1);
}
void known_origin() {setup_origin();queue_origin();commit_origin();assert(lookup());}
uintptr_t fake_ctor(void *child,void *asset) {
    ++native_calls;assert(child==projectile && asset==pdata && !lookup());
    projectile_origin_queued(manager,child);assert(!lookup());put(child,0x100,static_cast<void*>(nullptr));return native_result;
}
uintptr_t fake_reset(void *child) {
    ++native_calls;assert(child==projectile && !lookup());put(child,0x100,static_cast<void*>(nullptr));return native_result;
}
uintptr_t fake_decode(void *child,void *decoder) {
    ++native_calls;assert(child==projectile && decoder==pdata && !lookup());
    put(child,0x100,static_cast<void*>(other_source));return native_result;
}
uintptr_t fake_init(void *child,void *emitter,int x,int y,int z,void *victim,int dx,int dy,
                    void *source,void *scaling,int level,int a,int b) {
    ++native_calls;assert(!lookup());
    assert(child==projectile && emitter==target && x==-123456789 && y==0x76543210 && z==-17 &&
        victim==other_source && dx==654321 && dy==-987654 && source==target && scaling==pdata &&
        level==17 && a==-2147483647 && b==0x12345678);
    put(child,0x100,source);return native_result;
}
uintptr_t fake_replace(void *child,void *removed,int id,int flag,void *replacement) {
    ++native_calls;assert(child==projectile && removed==target && id==5000001 && flag==0x12345678 && !lookup());
    void *current=nullptr;safe_field(child,0x100,current);
    if(current==removed)put(child,0x100,replacement);
    return native_result;
}
void clear_source(bool remove=true) {
    g_projectile_origin_replace_original=fake_replace;
    assert(projectile_origin_replace(projectile,target,5000001,0x12345678,nullptr)==native_result);
    if(remove)objects[0]=nullptr;
}
int main() {
    ProjectileOrigin first,out;
    known_origin();assert(projectile_origin_lookup(projectile,world,manager,tick,first));
    assert(first.child.id==4000004 && first.source.id==5000001 && first.source.gid==34000081 &&
        first.source.owner==1 && first.current_source && first.committed_tick==tick && first.captured_tick==tick);
    char json[2048]={};int offset=0;append_projectile_origin(json,offset,sizeof(json),projectile);
    assert(strstr(json,"\"entity_id\":4000004") && strstr(json,"\"evidence\":\"projectile_queued_source\""));
    offset=0;char small[32]={};append_projectile_origin(small,offset,sizeof(small),projectile);assert(offset==0);
    offset=0;append_projectile_origin_diagnostics(json,offset,sizeof(json),world,tick);
    assert(strstr(json,"\"captured\":1") && strstr(json,"\"promoted\":1"));
    clear_source();assert(native_calls==1 && projectile_origin_lookup(projectile,world,manager,tick,out));
    assert(!out.current_source && out.sequence==first.sequence && out.source.id==first.source.id);
    // A source address reused after the attested null replacement never changes history.
    put(target,8,uint32_t(5000009));objects[0]=target;assert(projectile_origin_lookup(projectile,world,manager,tick,out));
    assert(out.source.id==5000001 && !out.current_source);

    // The kamikaze source may disappear between pending and committed admission.
    setup_origin();queue_origin();clear_source();commit_origin();assert(lookup());
    assert(g_projectile_origin_promoted==1);
    // A missing route without an observed replacement is not proof of source death.
    known_origin();put(projectile,0x100,static_cast<void*>(nullptr));assert(!lookup());
    put(projectile,0x100,static_cast<void*>(target));assert(!lookup());
    known_origin();objects[0]=nullptr;assert(!lookup());objects[0]=target;assert(!lookup());
    known_origin();put(target,8,uint32_t(5000002));assert(!lookup());put(target,8,uint32_t(5000001));assert(!lookup());
    known_origin();put(target,0x78,0);assert(!lookup());
    known_origin();put(target,0xac,26000000);assert(!lookup());
    known_origin();put(tdata,0x40,uint32_t(34000000));assert(!lookup());
    known_origin();put(projectile,8,uint32_t(4000005));assert(!lookup());
    known_origin();put(projectile,0xac,26000000);assert(!lookup());
    known_origin();put(projectile,0x78,0);assert(!lookup());
    known_origin();put(pdata,0x40,uint32_t(10000076));assert(!lookup());
    known_origin();put(projectile_table,0x84,3);assert(!lookup());
    known_origin();put(projectile,0x100,static_cast<void*>(other_source));assert(!lookup());
    known_origin();objects[0]=projectile;assert(!lookup()); // duplicate membership / identity
    known_origin();put(target,0xac,26000000);clear_source();assert(!lookup());
    // Manager removal may happen before the callback, but exact old ID remains required.
    known_origin();objects[0]=nullptr;clear_source();assert(lookup());

    // Non-null replacement invalidates even if the engine later returns to the old pointer.
    known_origin();g_projectile_origin_replace_original=fake_replace;
    assert(projectile_origin_replace(projectile,target,5000001,0x12345678,other_source)==native_result);
    assert(native_calls==1 && !lookup());put(projectile,0x100,static_cast<void*>(target));assert(!lookup());
    // An unrelated removal notification may not alter the source binding.
    known_origin();put(projectile,0x100,static_cast<void*>(target));
    g_projectile_origin_replace_original=[](void *p,void *old,int id,int flag,void *r)->uintptr_t {
        ++native_calls;assert(p==projectile && old==other_source && id==555 && flag==7 && !r && !lookup());return native_result;
    };
    assert(projectile_origin_replace(projectile,other_source,555,7,nullptr)==native_result && native_calls==1 && lookup());

    known_origin();g_projectile_origin_ctor_original=fake_ctor;
    assert(projectile_origin_ctor(projectile,pdata)==native_result && native_calls==1 && !lookup());
    known_origin();g_projectile_origin_reset_original=fake_reset;
    assert(projectile_origin_reset(projectile)==native_result && native_calls==1 && !lookup());
    known_origin();g_projectile_origin_decode_original=fake_decode;
    assert(projectile_origin_decode(projectile,pdata)==native_result && native_calls==1 && !lookup());
    known_origin();g_projectile_origin_init_original=fake_init;
    assert(projectile_origin_init(projectile,target,-123456789,0x76543210,-17,other_source,654321,-987654,
        target,pdata,17,-2147483647,0x12345678)==native_result && native_calls==1 && !lookup());
    objects[1]=nullptr;put(manager,0x14,1);put(projectile,0x10,static_cast<void*>(nullptr));
    projectile_origin_queued(manager,projectile);commit_origin();assert(lookup() && g_projectile_origin_sequence==2);

    known_origin();uint64_t epoch=g_projectile_origin_epoch;--tick;assert(!lookup() && g_projectile_origin_epoch==epoch+1);
    known_origin();char world_two[8];assert(!projectile_origin_lookup(projectile,world_two,manager,tick,out));
    known_origin();g_projectile_origin_ready=false;assert(!lookup());
    setup_origin();put(projectile,0x100,static_cast<void*>(nullptr));projectile_origin_queued(manager,projectile);
    put(projectile,0x100,static_cast<void*>(target));projectile_origin_queued(manager,projectile);assert(g_projectile_origin_captured==0);
    setup_origin();queue_origin();projectile_origin_queued(manager,projectile);commit_origin();assert(!lookup());
    setup_origin();queue_origin();projectile_origin_registered(manager,projectile,0);assert(g_projectile_origin_promoted==0);
    put(projectile,0x78,0);commit_origin();assert(!lookup());
    setup_origin();queue_origin();objects[1]=projectile;put(manager,0x14,2);
    put(projectile,8,uint32_t(5000001));put(projectile,0x10,static_cast<void*>(manager));
    projectile_origin_registered(manager,projectile,1);assert(g_projectile_origin_promoted==0);

    // A bounded table does not evict old origins or invalid-pointer tombstones.
    setup_origin();std::vector<unsigned char> many(size_t(kProjectileOriginCapacity+1)*512);
    for(int i=0;i<=kProjectileOriginCapacity;++i) {
        void *p=many.data()+size_t(i)*512;memcpy(p,projectile,512);projectile_origin_queued(manager,p);
    }
    assert(g_projectile_origin_captured==kProjectileOriginCapacity && g_projectile_origin_overflow==1);
    assert(g_projectile_origins[0].child==many.data());
    projectile_origin_queued(manager,many.data());assert(!g_projectile_origins[0].valid);
    projectile_origin_queued(manager,many.data()+size_t(kProjectileOriginCapacity)*512);
    assert(g_projectile_origin_overflow==2 && g_projectile_origins[0].child==many.data());

    // Nested original calls cannot expose or rehabilitate the in-flight instance.
    known_origin();{
        ProjectileOriginMutationGuard outer(projectile,false);assert(!lookup());
        {ProjectileOriginMutationGuard inner(projectile,false);assert(!lookup());}
        assert(!lookup());
    }assert(lookup());
    puts("Native projectile origin passed: queued/commit, exact clearing, replacements, lifecycle ABI, epoch, identity and capacity.");
}
