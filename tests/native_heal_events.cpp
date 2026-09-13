// Test the production observer and ARM64 entry against the existing fake live
// manager. No real game objects, game functions, or UI are touched.
#define main impact_fixture_test_main
#include "native_impact_events.cpp"
#undef main
char health[128],health_two[128],target_two[512],health_vtable[64];
char healing_buff_component[128],healing_buff[128],healing_buff_asset[128];
bool has_healing_buff=false;
uint32_t health_getter[]={0x52800040,0xd65f03c0};
void *runtime_component(void *object,int index){
    if(index==3)return has_healing_buff && object==target ? healing_buff_component : nullptr;
    if(index!=2)return nullptr;
    return object==target ? health : object==target_two ? health_two : nullptr;
}
#include "../probe/damage_events.inc"
#include "../probe/effect_origin.inc"
#include "../probe/heal_events.inc"
#include <climits>
#include <initializer_list>

int heal_mode=0,heal_original_calls=0;
void invoke_heal(int amount,void *c=health,const void *source=nullptr,
                 uintptr_t caller=0,const void *frame=nullptr);
void heal_original(void *c,int requested,int modifier,void *context,int m4,int m5,int m6) {
    ++heal_original_calls;
    assert((c==health || c==health_two) && modifier==13 && context==pdata && m4==14 && m5==15 && m6==16);
    if(heal_mode==1)return;
    if(heal_mode==4) {
        heal_mode=0;invoke_heal(requested,c);heal_mode=4;return;
    }
    if(heal_mode==5 || heal_mode==6) {
        int saved=heal_mode;heal_mode=saved==5?0:7;invoke_heal(20,health_two);heal_mode=saved;
        if(saved==6)return;
    } else if(heal_mode==7) {
        heal_mode=0;invoke_heal(10,health);heal_mode=7;
    }
    int hp=0,shield=0,maxhp=0,maxshield=0;
    safe_field(c,0x10,hp);safe_field(c,0x14,maxhp);safe_field(c,0x28,shield);safe_field(c,0x2c,maxshield);
    if(shield>0) {
        int64_t next=int64_t(shield)+requested;
        put(c,0x28,int(next>maxshield?maxshield:next));
    } else {
        int64_t next=int64_t(hp)+requested;
        put(c,0x10,int(next>maxhp?maxhp:next));
    }
    if(heal_mode==2)put(target,8,uint32_t(123));
    if(heal_mode==3)objects[1]=nullptr;
    if(heal_mode==8)put(c,0x28,1); // One call changing both pools is not attested.
    if(heal_mode==9)put(c,8,static_cast<void*>(target_two));
    if(heal_mode==10)++tick;
    if(heal_mode==11)put(c,0x10,hp+requested+1);
}
void invoke_heal(int amount,void *c,const void *source,uintptr_t caller,const void *frame) {
    cr_heal_bridge(c,amount,13,pdata,14,15,16,source,frame,
        caller ? reinterpret_cast<void*>(g_libg_base+caller) : nullptr);
}
void heal_setup(int selected=0,int shield=0) {
    has_healing_buff=false;g_effect_origin_ready=false;
    reset();objects[1]=target;objects[2]=target_two;put(manager,0x10,3);put(manager,0x14,3);
    put(health,0,static_cast<void*>(health_vtable));put(health_vtable,0x20,static_cast<void*>(health_getter));
    put(health,8,static_cast<void*>(target));
    memcpy(target_two,target,sizeof(target));put(target_two,8,uint32_t(5000002));
    memcpy(health_two,health,sizeof(health));put(health_two,8,static_cast<void*>(target_two));
    heal_mode=selected;heal_original_calls=0;
    g_heal_world=nullptr;heal_epoch_locked(world,tick);g_heal_original=heal_original;
    g_heal_ready=true;g_heal_sources_attested=true;
    put(health,0x10,50);put(health,0x14,100);put(health,0x28,shield);put(health,0x2c,100);
    put(health_two,0x10,50);put(health_two,0x14,100);put(health_two,0x28,0);put(health_two,0x2c,100);
    put(projectile,0x100,static_cast<void*>(nullptr));
}

extern "C" int heal_test_entry(void*,int,int,void*,int,int,int,const void*);
extern "C" char heal_test_after_entry;
asm(
    ".text\n.align 2\n.global heal_test_entry\n.type heal_test_entry,%function\n"
    "heal_test_entry:\n"
    "sub sp, sp, #48\n"
    "stp x19, x20, [sp, #16]\n"
    "stp x29, x30, [sp, #32]\n"
    "add x29, sp, #32\n"
    "str x7, [sp]\n"
    "mov x19, x7\n"
    "bl cr_heal_entry\n"
    ".global heal_test_after_entry\n"
    "heal_test_after_entry:\n"
    "ldr x9, [sp]\n"
    "cmp x19, x9\n"
    "cset w0, ne\n"
    "add x9, sp, #32\n"
    "cmp x29, x9\n"
    "cset w9, ne\n"
    "orr w0, w0, w9\n"
    "ldp x19, x20, [sp, #16]\n"
    "ldp x29, x30, [sp, #32]\n"
    "add sp, sp, #48\nret\n"
    ".size heal_test_entry,.-heal_test_entry\n"
);
int main() {
    heal_setup();invoke_heal(30);
    assert(heal_original_calls==1 && g_heal_sequence==1 && !g_heal_scope);
    assert(g_heal_events[0].amount==30 && g_heal_events[0].post_hp==80 && g_heal_events[0].source.owner==-1);
    heal_setup();invoke_heal(90);assert(g_heal_events[0].requested==90 && g_heal_events[0].amount==50);
    heal_setup(0,40);invoke_heal(80);
    assert(g_heal_events[0].shield && g_heal_events[0].amount==60 && g_heal_events[0].pre_hp==g_heal_events[0].post_hp);
    heal_setup(1);invoke_heal(30);assert(g_heal_no_change==1 && g_heal_sequence==0 && heal_original_calls==1);
    heal_setup();put(health,0x10,100);invoke_heal(30);assert(g_heal_no_change==1 && g_heal_sequence==0);
    heal_setup();invoke_heal(0);assert(g_heal_no_change==1 && g_heal_sequence==0);
    for(int selected:{2,3,8,9,10,11}) {
        heal_setup(selected);invoke_heal(30);assert(g_heal_sequence==0 && heal_original_calls==1);
        if(selected!=10)assert(g_heal_rejected==1);
    }
    heal_setup(4);invoke_heal(30);assert(g_heal_sequence==1 && g_heal_rejected==1 && heal_original_calls==2);
    heal_setup(5);invoke_heal(30);assert(g_heal_sequence==2 && g_heal_rejected==0 && heal_original_calls==2);
    heal_setup(6);invoke_heal(30);assert(g_heal_sequence==2 && g_heal_rejected==1 && heal_original_calls==3);
    assert(g_heal_events[0].target.id==5000001 && g_heal_events[1].target.id==5000002);
    heal_setup();invoke_heal(30,health,projectile,0xf2b374);
    assert(g_heal_events[0].source.id==7000001 && g_heal_events[0].projectile_id==7000001);
    heal_setup();put(projectile,0x100,static_cast<void*>(target_two));put(target_two,0x78,0);
    invoke_heal(30,health,projectile,0xf2b374);
    assert(g_heal_events[0].source.id==5000002 && g_heal_events[0].projectile_id==7000001);
    char effect[128]={},frame[128]={};
    heal_setup();put(effect,0x38,static_cast<void*>(target_two));invoke_heal(30,health,effect,0xf20df0);
    assert(g_heal_events[0].source.id==5000002 && !g_heal_events[0].projectile_id);
    heal_setup();put(frame,0,static_cast<void*>(target_two));invoke_heal(30,health,nullptr,0xf319a4,frame+32);
    assert(g_heal_events[0].source.id==5000002);
    heal_setup();invoke_heal(30,health,projectile,0xabcdef);
    assert(g_heal_events[0].source.id==0 && g_heal_events[0].source.owner==-1);
    heal_setup();g_heal_sources_attested=false;invoke_heal(30,health,projectile,0xf2b374);
    assert(g_heal_events[0].source.id==0 && !g_heal_events[0].projectile_id);
    heal_setup();put(health,0x10,INT_MAX-5);put(health,0x14,INT_MAX);invoke_heal(INT_MAX);
    assert(g_heal_events[0].amount==5);
    heal_setup();put(health,0x10,150);put(health,0x14,200);invoke_heal(30);
    assert(g_heal_events[0].amount==30);
    heal_setup();put(health,0x10,0);invoke_heal(30);assert(g_heal_sequence==0 && g_heal_rejected==1);
    heal_setup();invoke_heal(-20);assert(g_heal_sequence==0 && g_heal_rejected==1);
    // Exercise the actual assembly entry and prove caller x19, frame, LR and
    // the seven original arguments survive both source recovery routes.
    uintptr_t saved_base=g_libg_base;
    heal_setup();g_libg_base=reinterpret_cast<uintptr_t>(&heal_test_after_entry)-0xf2b374;
    put(projectile,0,g_libg_base+0x189cc28);
    assert(heal_test_entry(health,30,13,pdata,14,15,16,projectile)==0);
    assert(heal_original_calls==1 && g_heal_events[0].projectile_id==7000001);
    g_libg_base=saved_base;heal_setup();g_libg_base=reinterpret_cast<uintptr_t>(&heal_test_after_entry)-0xf319a4;
    assert(heal_test_entry(health,30,13,pdata,14,15,16,target_two)==0);
    assert(heal_original_calls==1 && g_heal_events[0].source.id==5000002);
    g_libg_base=saved_base;
    // Integrate the actual origin cache with the actual healing observer:
    // the native effect keeps restoring after its exact producer is removed.
    heal_setup();has_healing_buff=true;g_effect_origin_ready=true;
    g_effect_origin_world=nullptr;effect_origin_epoch(world,tick);
    void *buff_entries[]={healing_buff};
    put(healing_buff_component,0,g_libg_base+0x18a1d58);
    put(healing_buff_component,8,static_cast<void*>(target));
    put(healing_buff_component,0x18,buff_entries);put(healing_buff_component,0x20,1);put(healing_buff_component,0x24,1);
    put(healing_buff,0x20,static_cast<void*>(healing_buff_component));
    put(healing_buff,0x18,static_cast<void*>(healing_buff_asset));
    put(healing_buff,0x38,static_cast<void*>(target_two));
    put(healing_buff_asset,0,g_libg_base+0x1882500);put(healing_buff_asset,0x40,uint32_t(9000010));
    effect_origin_added(healing_buff_component,healing_buff);
    assert(g_effect_origin_captured==1);
    put(healing_buff,0x38,static_cast<void*>(nullptr));objects[2]=nullptr;
    invoke_heal(30,health,healing_buff,0xf20df0);
    assert(g_heal_sequence==1 && g_heal_events[0].source.id==5000002 && g_heal_events[0].amount==30);
    assert(g_heal_events[0].origin.sequence==1 && !g_heal_events[0].origin.current_source);
    effect_origin_invalidate(healing_buff);put(health,0x10,50);
    invoke_heal(30,health,healing_buff,0xf20df0);
    assert(g_heal_sequence==2 && !g_heal_events[1].source.id && !g_heal_events[1].origin.sequence);
    heal_setup();invoke_heal(30);
    char json[16384]={};int used=0;append_heal_events(json,used,sizeof(json),world,tick);
    assert(strstr(json,"\"amount\":30") && strstr(json,"\"complete\":true") && strstr(json,"nulls-heal.v2"));
    used=0;append_heal_events(json,used,1024,world,tick);
    assert(used<1024 && strstr(json,"\"complete\":false") && strstr(json,"\"events\":[]"));
    used=0;append_heal_events(json,used,1,world,tick);assert(used==0);
    used=0;append_heal_events(json,used,sizeof(json),world,tick+11);assert(strstr(json,"\"events\":[]"));
    heal_setup();for(int i=0;i<513;++i){put(health,0x10,50);invoke_heal(1);}
    assert(g_heal_sequence==513 && g_heal_lost_tick==tick);
    used=0;append_heal_events(json,used,sizeof(json),world,tick);assert(strstr(json,"\"complete\":false"));
    used=0;append_heal_events(json,used,sizeof(json),world,tick+11);assert(strstr(json,"\"complete\":true"));
    uint64_t old_epoch=g_heal_epoch;heal_epoch_locked(world,tick-1);
    assert(g_heal_epoch==old_epoch+1 && g_heal_sequence==0);
    heal_epoch_locked(manager,tick);assert(g_heal_epoch==old_epoch+2 && g_heal_calls==0);
    puts("Native heal observer passed: seven-argument ARM64 ABI and caller provenance, HP/shield caps, no-op, unknown source, nesting, identity, epochs, capacity.");
    return 0;
}
