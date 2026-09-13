// Reuse the fake object manager and run the actual damage observer ABI.
#define main impact_fixture_test_main
#include "native_impact_events.cpp"
#undef main
char health[128],health_two[128],target_two[512],health_vtable[64];
uint32_t health_getter[]={0x52800040,0xd65f03c0};
void *runtime_component(void *object,int index){
    if(index!=2)return nullptr;
    return object==target ? health : object==target_two ? health_two : nullptr;
}
#include "../probe/damage_events.inc"
int damage_mode=0;
int invoke_damage(int amount,int *out);
int damage_original(void *c,int requested,int m2,int m3,void *src,int m5,void *a6,void *a7,
                    uint64_t o8,int *out,uint64_t o10,uint64_t o11) {
    assert((c==health || c==health_two) && m2==2 && m3==3 && src==projectile && m5==5);
    assert(a6==pdata && a7==tdata && o8==0x1234567800000008ULL && o10==10 && o11==11);
    if(damage_mode==7) {damage_mode=0;invoke_damage(requested,out);damage_mode=7;return 0;}
    if(damage_mode==8 || damage_mode==9) {
        int saved=damage_mode;damage_mode=saved==8?0:10;int inner_out=-1;
        damage_observer(health_two,20,m2,m3,src,m5,a6,a7,o8,&inner_out,o10,o11);
        damage_mode=saved;
        if(saved==9)return 0; // A -> B -> A must suppress the outer A only.
    } else if(damage_mode==10) {
        damage_mode=0;int inner_out=-1;invoke_damage(10,&inner_out);damage_mode=10;
    }
    int hp=0,shield=0;safe_field(c,0x10,hp);safe_field(c,0x28,shield);
    if(damage_mode==2) {if(out)*out=requested;return 0;}
    if(shield>0)put(c,0x28,shield-requested);
    else put(c,0x10,damage_mode==5?1:hp-requested);
    if(out)*out=damage_mode==1?requested+1:damage_mode==5?hp:requested;
    if(damage_mode==3)put(target,8,uint32_t(123));
    return damage_mode==4 || damage_mode==8?1:0;
}
int invoke_damage(int amount,int *out) {
    return damage_observer(health,amount,2,3,projectile,5,pdata,tdata,0x1234567800000008ULL,out,10,11);
}
void setup(int selected=0,int shield=0) {
    reset();damage_mode=selected;g_damage_world=nullptr;damage_epoch_locked(world,tick);
    put(health,0,static_cast<void*>(health_vtable));put(health_vtable,0x20,static_cast<void*>(health_getter));
    put(health,8,static_cast<void*>(target));put(health,0x10,100);put(health,0x14,100);
    put(health,0x28,shield);put(health,0x2c,shield);g_damage_original=damage_original;g_damage_ready=true;
    memcpy(target_two,target,sizeof(target));put(target_two,8,uint32_t(5000002));
    memcpy(health_two,health,sizeof(health));put(health_two,8,static_cast<void*>(target_two));
    objects[2]=target_two;put(manager,0x10,3);put(manager,0x14,3);
}
bool collision_other_target=false,collision_append=true;
int collision_damage_original(void *p,void *t,int x,int y,int z) {
    assert(p==projectile && t==target && x==123 && y==456 && z==789);
    if(collision_append){processed[1]=5000001;put(projectile,0x6c,2);}
    int out=-1;
    damage_observer(collision_other_target?health_two:health,30,2,3,projectile,5,pdata,tdata,
        0x1234567800000008ULL,&out,10,11);
    return 0; // Piercing hit preserves the stock non-terminal return.
}
int main() {
    int out=-1;
    setup();assert(invoke_damage(30,&out)==0 && out==30 && g_damage_sequence==1);
    assert(g_damage_events[0].amount==30 && g_damage_events[0].projectile_id==7000001 && g_damage_events[0].source.id==7000001);
    setup(0,40);invoke_damage(30,&out);assert(g_damage_sequence==1 && g_damage_events[0].shield);
    setup(1);invoke_damage(30,&out);assert(g_damage_sequence==0 && g_damage_rejected==1);
    setup(2);invoke_damage(30,&out);assert(g_damage_sequence==0 && g_damage_no_change==1);
    setup(3);invoke_damage(30,&out);assert(g_damage_sequence==0 && g_damage_rejected==1);
    setup(4);assert(invoke_damage(100,&out)==1);assert(g_damage_events[0].lethal && g_damage_events[0].post_hp==0);
    setup(5);invoke_damage(200,&out);assert(out==100 && g_damage_events[0].amount==99);
    setup(7);invoke_damage(30,&out);assert(g_damage_sequence==1 && g_damage_rejected==1); // no nested double count
    setup(8);assert(invoke_damage(100,&out)==1 && g_damage_sequence==2 && g_damage_rejected==0);
    assert(g_damage_events[0].target.id==5000002 && g_damage_events[0].amount==20);
    assert(g_damage_events[1].target.id==5000001 && g_damage_events[1].amount==100 && g_damage_events[1].lethal);
    setup(9);invoke_damage(30,&out);assert(g_damage_sequence==2 && g_damage_rejected==1);
    assert(g_damage_events[0].target.id==5000001 && g_damage_events[0].amount==10);
    assert(g_damage_events[1].target.id==5000002 && g_damage_events[1].amount==20);
    setup();invoke_damage(30,nullptr);assert(g_damage_sequence==1);
    setup();put(health,0x14,10);invoke_damage(30,&out);assert(g_damage_sequence==1); // HP above static cap
    char json[16384]={};int used=0;append_damage_events(json,used,sizeof(json),world,tick);
    assert(strstr(json,"\"amount\":30") && strstr(json,"\"complete\":true"));
    used=0;append_damage_events(json,used,1024,world,tick);assert(used<1024 && strstr(json,"\"complete\":false"));
    setup();g_impact_original=collision_damage_original;
    assert(impact_observer(projectile,target,123,456,789)==0 && g_damage_sequence==1 && g_impact_sequence==1);
    assert(g_impacts[0].proof==2 && !g_impact_scope);
    setup();g_impact_original=collision_damage_original;collision_other_target=true;
    impact_observer(projectile,target,123,456,789);
    assert(g_damage_sequence==1 && g_impact_sequence==0 && !g_impact_scope); // Other victim is not proof.
    setup();g_impact_original=collision_damage_original;collision_other_target=false;collision_append=false;
    impact_observer(projectile,target,123,456,789);
    assert(g_damage_sequence==1 && g_impact_sequence==0 && g_impact_rejected==1); // Damage alone is insufficient.
    puts("Native damage observer passed: full ABI, HP, shield, lethal, clamp, stale out value, nesting, identity.");
}
