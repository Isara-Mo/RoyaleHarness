// Exercise the production historical-BUFF binding against fake native objects.
// This executable does not load game code or touch game state/UI.
#define main impact_fixture_test_main
#include "native_impact_events.cpp"
#undef main
#include <vector>

char effect_component[128],effect_component_two[128],effect_asset[512],effect_asset_two[512];
char effect_entry[128],effect_entry_two[128];
void *effect_members[64]={};
void *selected_component=effect_component;
void *runtime_component(void *object,int type) {
    return object==target && type==3 ? selected_component : nullptr;
}
#include "../probe/effect_origin.inc"

int effect_original_calls=0;
bool origin_exists(void *entry=effect_entry,void *host=target) {
    EffectOrigin out;
    return effect_origin_lookup(entry,host,world,manager,tick,out);
}
uintptr_t fake_ctor(void *entry,void *context,void *component) {
    ++effect_original_calls;
    assert(entry==effect_entry && context==pdata && component==effect_component && !origin_exists());
    put(entry,0x38,static_cast<void*>(nullptr));
    return UINT64_C(0xfedcba9876543210);
}
uintptr_t fake_reset(void *entry) {
    ++effect_original_calls;assert(entry==effect_entry && !origin_exists());
    put(entry,0x38,static_cast<void*>(nullptr));
    return UINT64_C(0x8765432112345678);
}
uintptr_t fake_decode(void *entry,void *decoder) {
    ++effect_original_calls;assert(entry==effect_entry && decoder==pdata && !origin_exists());
    put(entry,0x38,static_cast<void*>(nullptr));
    return UINT64_C(0x1234567887654321);
}
uintptr_t fake_setter(void *entry,void *source,int metadata) {
    ++effect_original_calls;
    assert(entry==effect_entry && source==projectile && metadata==123 && !origin_exists());
    put(entry,0x38,source);
    return UINT64_C(0xabcd01239876fedc);
}
void prepare_entry(void *entry) {
    memset(entry,0,128);
    put(entry,0x18,static_cast<void*>(effect_asset));
    put(entry,0x20,static_cast<void*>(effect_component));
    put(entry,0x38,static_cast<void*>(projectile));
    put(entry,8,200);put(entry,0xc,200);
}
void effect_setup() {
    reset();objects[0]=projectile;objects[1]=target;objects[2]=nullptr;
    selected_component=effect_component;
    memset(effect_component,0,sizeof(effect_component));
    memset(effect_asset,0,sizeof(effect_asset));
    memset(effect_members,0,sizeof(effect_members));
    put(effect_component,0,g_libg_base+0x18a1d58);
    put(effect_component,8,static_cast<void*>(target));
    put(effect_component,0x18,effect_members);put(effect_component,0x20,64);put(effect_component,0x24,1);
    put(effect_asset,0,g_libg_base+0x1882500);put(effect_asset,0x40,uint32_t(9000102));
    prepare_entry(effect_entry);effect_members[0]=effect_entry;
    g_effect_origin_world=nullptr;effect_origin_epoch(world,tick);g_effect_origin_ready=true;
    effect_original_calls=0;
    g_effect_origin_ctor_original=fake_ctor;g_effect_origin_reset_original=fake_reset;
    g_effect_origin_decode_original=fake_decode;g_effect_origin_setter_original=fake_setter;
}
void add_origin() {effect_origin_added(effect_component,effect_entry);assert(origin_exists());}
int main() {
    EffectOrigin first,out;
    effect_setup();add_origin();
    assert(effect_origin_lookup(effect_entry,target,world,manager,tick,first));
    assert(first.current_source && first.source.id==7000001 && first.source.gid==10000032 &&
        first.source.owner==0 && first.source.card==28000011 && first.target.id==5000001 &&
        first.buff_gid==9000102 && first.captured_tick==tick && first.sequence==1);
    char json[1024]={};int used=0;append_effect_origin(json,used,sizeof(json),first);
    assert(strstr(json,"\"schema\":\"nulls-effect-origin.v1\"") &&
        strstr(json,"\"target_data_id\":3565953160") && strstr(json,"\"current_source\":true"));
    used=0;char small[16]={};append_effect_origin(small,used,sizeof(small),first);
    assert(used==4 && strcmp(small,"null")==0);

    // The engine refreshes time/level but keeps the actual source binding.
    put(effect_entry,8,400);put(effect_entry,0xc,450);put(effect_entry,0x28,17);++tick;
    assert(effect_origin_lookup(effect_entry,target,world,manager,tick,out));
    assert(out.sequence==first.sequence && out.captured_tick==first.captured_tick && out.source.id==first.source.id);
    effect_origin_added(effect_component,effect_entry);assert(g_effect_origin_sequence==1);

    // Source removal clears its pointer while the same BUFF continues healing.
    put(effect_entry,0x38,static_cast<void*>(nullptr));objects[0]=nullptr;
    assert(effect_origin_lookup(effect_entry,target,world,manager,tick,out));
    assert(!out.current_source && out.sequence==first.sequence && out.source.id==first.source.id);
    used=0;append_effect_origin(json,used,sizeof(json),out);assert(strstr(json,"\"current_source\":false"));
    // Once the reference is null, an unrelated new entity at the old source
    // address cannot alter this already observed historical identity.
    put(projectile,8,uint32_t(7000002));objects[0]=projectile;
    assert(effect_origin_lookup(effect_entry,target,world,manager,tick,out));
    assert(out.source.id==first.source.id && !out.current_source);

    effect_setup();add_origin();
    assert(effect_origin_ctor(effect_entry,pdata,effect_component)==UINT64_C(0xfedcba9876543210));
    assert(effect_original_calls==1 && !origin_exists());
    prepare_entry(effect_entry);add_origin();assert(g_effect_origin_sequence==2);
    assert(effect_origin_reset(effect_entry)==UINT64_C(0x8765432112345678));
    assert(effect_original_calls==2 && !origin_exists());
    prepare_entry(effect_entry);add_origin();assert(effect_origin_decode(effect_entry,pdata)==UINT64_C(0x1234567887654321));
    assert(effect_original_calls==3 && !origin_exists());
    prepare_entry(effect_entry);add_origin();uint64_t before_setter=g_effect_origin_sequence;
    assert(effect_origin_setter(effect_entry,projectile,123)==UINT64_C(0xabcd01239876fedc));
    assert(effect_original_calls==4 && origin_exists());
    assert(g_effect_origin_sequence==before_setter+1); // fresh observation after explicit rebind

    // Real missed-source case: an AEO applies the BUFF before its own manager
    // admission. The first fully live observation, not the earlier add, is the
    // timestamped binding retained after that AEO disappears.
    effect_setup();objects[0]=nullptr;int attached_tick=tick;
    effect_origin_added(effect_component,effect_entry);
    assert(g_effect_origin_captured==0 && g_effect_origin_sequence==0);
    ++tick;assert(!origin_exists());assert(g_effect_origin_captured==0);
    objects[0]=projectile;++tick;
    assert(effect_origin_lookup(effect_entry,target,world,manager,tick,first));
    assert(first.captured_tick==tick && first.captured_tick>attached_tick && first.current_source);
    assert(first.source.id==7000001 && first.sequence==1);
    put(effect_entry,0x38,static_cast<void*>(nullptr));objects[0]=nullptr;++tick;
    assert(effect_origin_lookup(effect_entry,target,world,manager,tick,out));
    assert(!out.current_source && out.source.id==first.source.id && out.sequence==first.sequence &&
        out.captured_tick==first.captured_tick);

    // Entry mutation/reuse without an observed reset fails closed permanently.
    effect_setup();add_origin();put(projectile,8,uint32_t(7000002));
    assert(!origin_exists());put(projectile,8,uint32_t(7000001));assert(!origin_exists());
    effect_origin_added(effect_component,effect_entry);assert(!origin_exists());
    effect_setup();add_origin();put(projectile,0x78,1);assert(!origin_exists());
    effect_setup();add_origin();put(projectile,0xac,26000000);assert(!origin_exists());
    effect_setup();add_origin();put(pdata,0x40,uint32_t(10000033));assert(!origin_exists());
    effect_setup();add_origin();objects[0]=nullptr;assert(!origin_exists());
    effect_setup();add_origin();put(effect_entry,0x38,static_cast<void*>(target));assert(!origin_exists());

    effect_setup();effect_members[1]=effect_entry;put(effect_component,0x24,2);
    effect_origin_added(effect_component,effect_entry);assert(g_effect_origin_captured==0 && !origin_exists());
    effect_setup();effect_members[0]=effect_entry_two;
    effect_origin_added(effect_component,effect_entry);assert(g_effect_origin_captured==0 && !origin_exists());
    effect_setup();add_origin();effect_members[1]=effect_entry;put(effect_component,0x24,2);
    assert(!origin_exists());put(effect_component,0x24,1);assert(!origin_exists());
    effect_setup();add_origin();effect_members[0]=effect_entry_two;assert(!origin_exists());
    effect_setup();add_origin();selected_component=effect_component_two;assert(!origin_exists());
    effect_setup();add_origin();put(effect_component,8,static_cast<void*>(projectile));assert(!origin_exists());
    effect_setup();add_origin();put(effect_entry,0x20,static_cast<void*>(effect_component_two));assert(!origin_exists());
    effect_setup();add_origin();memcpy(effect_asset_two,effect_asset,sizeof(effect_asset));
    put(effect_entry,0x18,static_cast<void*>(effect_asset_two));assert(!origin_exists());
    effect_setup();add_origin();put(effect_asset,0x40,uint32_t(9000103));assert(!origin_exists());
    effect_setup();add_origin();put(target,8,uint32_t(5000002));assert(!origin_exists());
    effect_setup();add_origin();assert(!origin_exists(effect_entry,projectile));assert(!origin_exists());
    effect_setup();put(effect_entry,0x38,static_cast<void*>(nullptr));
    effect_origin_added(effect_component,effect_entry);assert(g_effect_origin_captured==0);

    effect_setup();add_origin();uint64_t epoch=g_effect_origin_epoch;
    --tick;assert(effect_origin_lookup(effect_entry,target,world,manager,tick,out));
    assert(g_effect_origin_epoch==epoch+1 && out.epoch==epoch+1 && out.captured_tick==tick);
    effect_setup();add_origin();char different_world[16];epoch=g_effect_origin_epoch;
    assert(!effect_origin_lookup(effect_entry,target,different_world,manager,tick,out));
    assert(g_effect_origin_epoch==epoch+1);
    effect_setup();add_origin();g_effect_origin_ready=false;assert(!origin_exists());
    prepare_entry(effect_entry_two);effect_members[0]=effect_entry_two;
    effect_origin_added(effect_component,effect_entry_two);assert(g_effect_origin_captured==1);

    // More live records than the fixed budget never overwrites an earlier
    // origin. A lifecycle invalidation frees a slot and assigns a new sequence.
    effect_setup();std::vector<unsigned char> many(size_t(kEffectOriginCapacity+1)*128);
    for(int i=0;i<=kEffectOriginCapacity;++i) {
        void *entry=many.data()+size_t(i)*128;prepare_entry(entry);effect_members[0]=entry;
        effect_origin_added(effect_component,entry);
    }
    assert(g_effect_origin_captured==kEffectOriginCapacity && g_effect_origin_overflow==1 &&
        g_effect_origin_sequence==kEffectOriginCapacity);
    effect_members[0]=many.data();assert(effect_origin_lookup(many.data(),target,world,manager,tick,out));
    assert(out.sequence==1);
    effect_origin_invalidate(many.data());
    void *last=many.data()+size_t(kEffectOriginCapacity)*128;effect_members[0]=last;
    effect_origin_added(effect_component,last);
    assert(effect_origin_lookup(last,target,world,manager,tick,out) && out.sequence==kEffectOriginCapacity+1);
    assert(g_effect_origin_captured==kEffectOriginCapacity+1);

    // Tombstones occupy their slot until a real lifecycle invalidation. Fill
    // every other slot, overflow, then restore the old bytes: neither add nor
    // lookup may forget the observed conflict and bind this generation again.
    effect_setup();add_origin();put(projectile,8,uint32_t(7000002));assert(!origin_exists());
    put(projectile,8,uint32_t(7000001));
    for(int i=0;i<kEffectOriginCapacity;++i) {
        void *entry=many.data()+size_t(i)*128;prepare_entry(entry);effect_members[0]=entry;
        effect_origin_added(effect_component,entry);
    }
    assert(g_effect_origin_captured==kEffectOriginCapacity && g_effect_origin_overflow==1);
    effect_members[0]=effect_entry;assert(!origin_exists());
    effect_origin_added(effect_component,effect_entry);assert(!origin_exists());
    effect_origin_invalidate(many.data());assert(!origin_exists());
    effect_origin_invalidate(effect_entry);++tick;
    assert(effect_origin_lookup(effect_entry,target,world,manager,tick,out));
    assert(out.captured_tick==tick && out.sequence==kEffectOriginCapacity+1);

    // Lookup is blocked while a native mutation is still executing, even if
    // its old bytes look valid. Removing scopes in non-LIFO order also models
    // overlapping mutation callbacks on separate native threads.
    effect_setup();add_origin();
    auto *outer=new EffectOriginMutationGuard(effect_entry);
    assert(!origin_exists());
    auto *inner=new EffectOriginMutationGuard(effect_entry);
    assert(!origin_exists());delete outer;assert(!origin_exists());
    delete inner;assert(origin_exists() && !g_effect_origin_mutations);
    puts("Native effect origin passed: exact binding, historical source, lifecycle ABI, reuse, membership, capacity.");
    return 0;
}
