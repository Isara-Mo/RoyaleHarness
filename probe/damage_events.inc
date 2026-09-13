// Exact HP / built-in shield transitions at FirstLight's f642c4 damage ABI.
// Requested amount alone is never damage evidence. Buff shield accounting,
// same-component nested outer calls and unresolved identities remain unknown.
struct DamageEvent {
    uint64_t sequence=0;
    int tick=-1,requested=0,amount=0,pre_hp=0,post_hp=0,pre_shield=0,post_shield=0;
    bool lethal=false,shield=false;
    ImpactFact source,target;
    uint32_t projectile_id=0,projectile_gid=0;
};
struct DamageScope { DamageScope *previous=nullptr; void *component=nullptr; bool nested=false; };
thread_local DamageScope *g_damage_scope=nullptr;
DamageEvent g_damage_events[512];
pthread_mutex_t g_damage_mutex=PTHREAD_MUTEX_INITIALIZER;
void *g_damage_world=nullptr;
int g_damage_tick=-1,g_damage_lost_tick=-1;
uint64_t g_damage_epoch=0,g_damage_sequence=0,g_damage_calls=0,g_damage_rejected=0,g_damage_no_change=0;
bool g_damage_ready=false;
using DamageFn=int(*)(void*,int,int,int,void*,int,void*,void*,uint64_t,int*,uint64_t,uint64_t);
DamageFn g_damage_original=nullptr;
void damage_epoch_locked(void *world,int tick) {
    if (world!=g_damage_world || tick<g_damage_tick) {
        for(auto &e:g_damage_events)e=DamageEvent{};
        ++g_damage_epoch;g_damage_sequence=0;g_damage_calls=0;g_damage_rejected=0;
        g_damage_no_change=0;g_damage_lost_tick=-1;
    }
    g_damage_world=world;g_damage_tick=tick;
}
bool damage_health(void *component,void *object,int &hp,int &shield) {
    uintptr_t vt=0,getter=0;void *owner=nullptr;uint32_t code[2]={};int maxhp=-1,maxshield=-1;
    return runtime_component(object,2)==component && safe_field(component,8,owner) && owner==object &&
        safe_field(component,0,vt) && safe_field(reinterpret_cast<void*>(vt),0x20,getter) &&
        safe_bytes(reinterpret_cast<void*>(getter),code,sizeof(code)) &&
        code[0]==0x52800040 && code[1]==0xd65f03c0 &&
        safe_field(component,0x10,hp) && safe_field(component,0x14,maxhp) &&
        safe_field(component,0x28,shield) && safe_field(component,0x2c,maxshield) &&
        hp>=0 && maxhp>=0 && shield>=0 && maxshield>=shield;
}
int damage_observer(void *component,int requested,int metadata_two,int metadata_three,
                    void *immediate,int metadata_five,void *aux_six,void *aux_seven,
                    uint64_t option_eight,int *applied_out,uint64_t option_ten,uint64_t option_eleven) {
    DamageScope scope{g_damage_scope,component,false};
    // A death explosion damages other components before the original lethal
    // call returns. Those independent victims do not contaminate this HP delta.
    // Mark every ancestor for the SAME component, including A -> B -> A chains.
    for(auto *ancestor=g_damage_scope;ancestor;ancestor=ancestor->previous)
        if(ancestor->component==component)ancestor->nested=true;
    g_damage_scope=&scope;
    void *world=nullptr,*manager=nullptr,*target=nullptr;void **objects=nullptr;
    int tick=-1,count=0;uint64_t epoch=0;DamageEvent event;
    bool context=deployment_context(world,manager,tick);
    if(context) {
        pthread_mutex_lock(&g_damage_mutex);damage_epoch_locked(world,tick);
        epoch=g_damage_epoch;++g_damage_calls;pthread_mutex_unlock(&g_damage_mutex);
    }
    bool capture=context && safe_field(component,8,target) && impact_vector(manager,objects,count) &&
        impact_fact(target,manager,objects,count,event.target) &&
        damage_health(component,target,event.pre_hp,event.pre_shield);
    if(capture) {
        if(!impact_fact(immediate,manager,objects,count,event.source)) event.source=ImpactFact{};
        uintptr_t vt=0;
        if(event.source.id && safe_field(immediate,0,vt) && vt==g_libg_base+0x189cc28) {
            event.projectile_id=event.source.id;
            event.projectile_gid=event.source.gid;
            void *creator=nullptr;ImpactFact controller;
            if(safe_field(immediate,0x100,creator) && impact_fact(creator,manager,objects,count,controller) &&
                controller.owner==event.source.owner) event.source=controller;
        }
    }
    int result=g_damage_original(component,requested,metadata_two,metadata_three,immediate,metadata_five,
        aux_six,aux_seven,option_eight,applied_out,option_ten,option_eleven);
    g_damage_scope=scope.previous;
    if(!context)return result;
    void *after_world=nullptr,*after_manager=nullptr;int after_tick=-1,reported=-1;
    bool valid=capture && !scope.nested && requested>=0 &&
        deployment_context(after_world,after_manager,after_tick) && world==after_world &&
        manager==after_manager && tick==after_tick && impact_same_identity(target,event.target) &&
        damage_health(component,target,event.post_hp,event.post_shield);
    event.lethal=(result&1)!=0;event.shield=event.pre_shield>0;
    int delta=event.shield ? event.pre_shield-event.post_shield : event.pre_hp-event.post_hp;
    bool no_change=valid && delta==0 && !event.lethal;
    if(valid && !no_change) {
        reported=delta;
        if(applied_out && !safe_field(applied_out,0,reported))valid=false;
        // Same narrowly proved one-HP survival clamp as upstream.
        if(!event.shield && !event.lethal && event.pre_hp>1 && event.post_hp==1 &&
            requested>=event.pre_hp && reported==event.pre_hp)reported=delta;
        valid=valid && delta>0 && reported==delta &&
            (event.shield ? event.pre_hp==event.post_hp : event.pre_shield==event.post_shield) &&
            (!event.lethal || (!event.shield && event.post_hp==0));
    }
    pthread_mutex_lock(&g_damage_mutex);
    if(world==g_damage_world && epoch==g_damage_epoch && tick>=g_damage_tick) {
        if(no_change)++g_damage_no_change;
        else if(valid) {
            impact_note_damage(immediate,target,world,manager,tick,event.projectile_id,event.projectile_gid,event.target);
            event.sequence=++g_damage_sequence;event.tick=tick;event.requested=requested;event.amount=delta;
            auto &slot=g_damage_events[(event.sequence-1)%512];
            if(slot.sequence)g_damage_lost_tick=slot.tick;
            slot=event;
        } else ++g_damage_rejected;
    }
    pthread_mutex_unlock(&g_damage_mutex);
    return result;
}
bool install_damage_observer() {
    const uint8_t bytes[]={0xff,0x43,0x03,0xd1,0xfd,0x7b,0x07,0xa9,
                          0xfc,0x6f,0x08,0xa9,0xfa,0x67,0x09,0xa9};
    return install_inline_hook(g_libg_base+0xf642c4,bytes,16,reinterpret_cast<void*>(&damage_observer),
        reinterpret_cast<void**>(&g_damage_original),"proved HP and shield damage");
}
void append_damage_events(char *json,int &offset,size_t size,void *world,int tick) {
    DamageEvent events[512];int n=0;
    pthread_mutex_lock(&g_damage_mutex);damage_epoch_locked(world,tick);
    uint64_t epoch=g_damage_epoch,last=g_damage_sequence,calls=g_damage_calls,
        rejected=g_damage_rejected,no_change=g_damage_no_change;
    bool complete=g_damage_lost_tick<0 || g_damage_lost_tick<tick-10;
    uint64_t begin=last>512?last-512+1:1;
    for(uint64_t seq=begin;seq<=last;++seq) {
        const auto &e=g_damage_events[(seq-1)%512];
        if(e.sequence==seq && e.tick<=tick && e.tick>=tick-10)events[n++]=e;
    }
    pthread_mutex_unlock(&g_damage_mutex);
    complete=complete && offset>=0 && size_t(offset)+size_t(n)*768+4096<size;
    offset+=snprintf(json+offset,size-offset,
        ",\"damage_runtime\":{\"schema\":\"nulls-damage.v1\",\"hooks_ready\":%s,\"complete\":%s,\"epoch\":%llu,\"tick\":%d,\"window_ticks\":10,\"calls\":%llu,\"captured\":%llu,\"rejected\":%llu,\"no_change\":%llu,\"events\":[",
        g_damage_ready?"true":"false",complete?"true":"false",(unsigned long long)epoch,tick,
        (unsigned long long)calls,(unsigned long long)last,(unsigned long long)rejected,(unsigned long long)no_change);
    if(complete)for(int i=0;i<n;++i) {
        const auto &e=events[i];
        offset+=snprintf(json+offset,size-offset,
            "%s{\"sequence\":%llu,\"tick\":%d,\"source_id\":%u,\"source_data_id\":%u,\"source_owner\":%d,\"source_card_id\":%d,\"projectile_id\":%u,\"projectile_data_id\":%u,\"target_id\":%u,\"target_data_id\":%u,\"target_owner\":%d,\"target_card_id\":%d,\"requested\":%d,\"amount\":%d,\"pre_hp\":%d,\"post_hp\":%d,\"pre_shield\":%d,\"post_shield\":%d,\"shield\":%s,\"lethal\":%s}",
            i?",":"",(unsigned long long)e.sequence,e.tick,e.source.id,e.source.gid,e.source.owner,e.source.card,
            e.projectile_id,e.projectile_gid,e.target.id,e.target.gid,e.target.owner,e.target.card,e.requested,e.amount,
            e.pre_hp,e.post_hp,e.pre_shield,e.post_shield,e.shield?"true":"false",e.lethal?"true":"false");
    }
    offset+=snprintf(json+offset,size-offset,"]}");
}
