// Exact f64d14 healing transitions, adapted from FirstLight combat telemetry.
// Includes after impact_events.inc and damage_events.inc. Unknown producers
// stay unknown; only actual HP / built-in shield restoration is published.
struct HealEvent {
    uint64_t sequence=0;
    int tick=-1,requested=0,amount=0,pre_hp=0,post_hp=0,pre_shield=0,post_shield=0;
    bool shield=false;
    ImpactFact source,target;
    uint32_t projectile_id=0,projectile_gid=0;
    uintptr_t caller_offset=0;
    EffectOrigin origin;
};
struct HealScope { HealScope *previous=nullptr; void *component=nullptr; bool nested=false; };
thread_local HealScope *g_heal_scope=nullptr;
HealEvent g_heal_events[512];
pthread_mutex_t g_heal_mutex=PTHREAD_MUTEX_INITIALIZER;
void *g_heal_world=nullptr;
int g_heal_tick=-1,g_heal_lost_tick=-1;
uint64_t g_heal_epoch=0,g_heal_sequence=0,g_heal_calls=0,g_heal_rejected=0,g_heal_no_change=0;
bool g_heal_ready=false,g_heal_sources_attested=false;
using HealFn=void(*)(void*,int,int,void*,int,int,int);
HealFn g_heal_original=nullptr;
extern "C" void cr_heal_entry();

void heal_epoch_locked(void *world,int tick) {
    if(world!=g_heal_world || tick<g_heal_tick) {
        for(auto &event:g_heal_events)event=HealEvent{};
        ++g_heal_epoch;g_heal_sequence=0;g_heal_calls=0;g_heal_rejected=0;
        g_heal_no_change=0;g_heal_lost_tick=-1;
    }
    g_heal_world=world;g_heal_tick=tick;
}
bool heal_same_fact(const ImpactFact &a,const ImpactFact &b) {
    return a.id==b.id && a.gid==b.gid && a.owner==b.owner && a.card==b.card;
}
void heal_source(HealEvent &event,void *world,void *manager,int tick,void *target,void **objects,int count,
                 const void *caller_x19,const void *caller_frame) {
    if(!g_heal_sources_attested)return;
    void *immediate=nullptr;
    if(event.caller_offset==0xf20df0)
        safe_field(caller_x19,0x38,immediate); // Effect's exact native producer.
    else if(event.caller_offset==0xf2b374)
        immediate=const_cast<void*>(caller_x19); // Projectile itself.
    else if(event.caller_offset==0xf319a4 && reinterpret_cast<uintptr_t>(caller_frame)>=0x20)
        safe_field(reinterpret_cast<const void*>(reinterpret_cast<uintptr_t>(caller_frame)-0x20),0,immediate);
    EffectOrigin origin;
    bool has_origin=event.caller_offset==0xf20df0 && g_effect_origin_ready &&
        effect_origin_lookup(const_cast<void*>(caller_x19),target,world,manager,tick,origin);
    if(!impact_fact(immediate,manager,objects,count,event.source)) {
        event.source=ImpactFact{};
        if(has_origin && !origin.current_source) {event.source=origin.source;event.origin=origin;}
        return;
    }
    uintptr_t vt=0;
    if(safe_field(immediate,0,vt) && vt==g_libg_base+0x189cc28) {
        event.projectile_id=event.source.id;event.projectile_gid=event.source.gid;
        void *creator=nullptr;ImpactFact owner;
        if(safe_field(immediate,0x100,creator) && impact_fact(creator,manager,objects,count,owner) &&
            owner.owner==event.source.owner) event.source=owner;
    }
    // Preserve the original projectile-to-controller convention. A BUFF's
    // immediate producer must never override a different proved controller.
    if(has_origin && heal_same_fact(event.source,origin.source))event.origin=origin;
}
extern "C" void cr_heal_bridge(void *component,int requested,int modifier,void *context_pointer,
    int metadata_four,int metadata_five,int metadata_six,const void *caller_x19,
    const void *caller_frame,const void *caller_return) {
    HealScope scope{g_heal_scope,component,false};
    // Healing A can invoke another heal for A through B. Do not count an
    // ancestor's aggregate delta a second time, while preserving independent B.
    for(auto *ancestor=g_heal_scope;ancestor;ancestor=ancestor->previous)
        if(ancestor->component==component)ancestor->nested=true;
    g_heal_scope=&scope;
    void *world=nullptr,*manager=nullptr,*target=nullptr;void **objects=nullptr;
    int tick=-1,count=0;uint64_t epoch=0;HealEvent event;
    bool context=deployment_context(world,manager,tick);
    if(context) {
        pthread_mutex_lock(&g_heal_mutex);heal_epoch_locked(world,tick);
        epoch=g_heal_epoch;++g_heal_calls;pthread_mutex_unlock(&g_heal_mutex);
    }
    bool capture=context && safe_field(component,8,target) && impact_vector(manager,objects,count) &&
        impact_fact(target,manager,objects,count,event.target) &&
        damage_health(component,target,event.pre_hp,event.pre_shield);
    uintptr_t caller=reinterpret_cast<uintptr_t>(caller_return);
    if(caller>=g_libg_base)event.caller_offset=caller-g_libg_base;
    if(capture)heal_source(event,world,manager,tick,target,objects,count,caller_x19,caller_frame);
    // No observer path skips or repeats this call; the entire seven-argument
    // ABI, including metadata and context pointer, is forwarded unchanged.
    g_heal_original(component,requested,modifier,context_pointer,metadata_four,metadata_five,metadata_six);
    g_heal_scope=scope.previous;
    if(!context)return;
    void *after_world=nullptr,*after_manager=nullptr;int after_tick=-1;ImpactFact after_target;
    bool valid=capture && !scope.nested && requested>=0 &&
        deployment_context(after_world,after_manager,after_tick) && world==after_world &&
        manager==after_manager && tick==after_tick && impact_vector(manager,objects,count) &&
        impact_fact(target,manager,objects,count,after_target) && heal_same_fact(event.target,after_target) &&
        damage_health(component,target,event.post_hp,event.post_shield);
    // The native function selects a positive built-in shield first; otherwise
    // it restores HP. Both pools are nonnegative, so these differences fit int.
    event.shield=event.pre_shield>0;
    int delta=event.shield ? event.post_shield-event.pre_shield : event.post_hp-event.pre_hp;
    bool no_change=valid && event.pre_hp==event.post_hp && event.pre_shield==event.post_shield;
    if(valid && !no_change)valid=requested>0 && delta>0 && delta<=requested &&
        (event.shield ? event.pre_hp==event.post_hp :
            event.pre_hp>0 && event.pre_shield==0 && event.post_shield==0);
    pthread_mutex_lock(&g_heal_mutex);
    if(world==g_heal_world && epoch==g_heal_epoch && tick>=g_heal_tick) {
        if(no_change)++g_heal_no_change;
        else if(valid) {
            event.sequence=++g_heal_sequence;event.tick=tick;event.requested=requested;event.amount=delta;
            auto &slot=g_heal_events[(event.sequence-1)%512];
            if(slot.sequence)g_heal_lost_tick=slot.tick;
            slot=event;
        } else ++g_heal_rejected;
    }
    pthread_mutex_unlock(&g_heal_mutex);
}
bool heal_words(uintptr_t offset,const uint32_t *expected,size_t count) {
    uint32_t actual[9]={};
    return count<=9 && safe_bytes(reinterpret_cast<void*>(g_libg_base+offset),actual,count*4) &&
        memcmp(actual,expected,count*4)==0;
}
bool install_heal_observer() {
    // These are the three BL f64d14 callers in the attested APK. The caller
    // register/frame meaning is enabled only when their argument loads match.
    const uint32_t effect[]={0x294b9664,0x39419266,0x2a1503e1,0x2a1403e2,0x94010fca};
    const uint32_t projectile[]={0x39429266,0x2a0003e5,0xaa1703e0,0x2a1503e1,0x2a1f03e2,
        0xaa1803e3,0x2a1903e4,0xf85e03b9,0x9400e669};
    const uint32_t area[]={0x39429326,0xb85c83b9,0x2a0003e5,0xaa1503e0,0x2a1603e1,
        0x2a1f03e2,0xaa1a03e3,0x2a1b03e4,0x9400ccdd};
    const uint32_t area_source[]={0xf85e03b9,0xaa1903e0,0x97fff613,0xaa0003fa};
    g_heal_sources_attested=heal_words(0xf20ddc,effect,5) && heal_words(0xf2b350,projectile,9) &&
        heal_words(0xf31980,area,9) && heal_words(0xf3195c,area_source,4);
    const uint8_t bytes[]={0xff,0xc3,0x01,0xd1,0xfd,0x7b,0x01,0xa9,
                          0xfc,0x6f,0x02,0xa9,0xfa,0x67,0x03,0xa9};
    return install_inline_hook(g_libg_base+0xf64d14,bytes,16,reinterpret_cast<void*>(&cr_heal_entry),
        reinterpret_cast<void**>(&g_heal_original),"proved HP and built-in shield healing");
}
void append_heal_events(char *json,int &offset,size_t size,void *world,int tick) {
    // Caller must reserve the envelope. If even that is impossible, leave a
    // missing envelope rather than writing past the output buffer.
    if(!json || offset<0 || size_t(offset)>=size || size-size_t(offset)<512)return;
    HealEvent events[512];int n=0;
    pthread_mutex_lock(&g_heal_mutex);heal_epoch_locked(world,tick);
    uint64_t epoch=g_heal_epoch,last=g_heal_sequence,calls=g_heal_calls,
        rejected=g_heal_rejected,no_change=g_heal_no_change;
    bool complete=g_heal_lost_tick<0 || g_heal_lost_tick<tick-10;
    uint64_t begin=last>512?last-512+1:1;
    for(uint64_t seq=begin;seq<=last;++seq) {
        const auto &e=g_heal_events[(seq-1)%512];
        if(e.sequence==seq && e.tick<=tick && e.tick>=tick-10)events[n++]=e;
    }
    pthread_mutex_unlock(&g_heal_mutex);
    complete=complete && size_t(offset)+size_t(n)*1536+4096<size;
    offset+=snprintf(json+offset,size-offset,
        ",\"heal_runtime\":{\"schema\":\"nulls-heal.v2\",\"hooks_ready\":%s,\"sources_attested\":%s,\"origin_hooks_ready\":%s,\"origin_epoch\":%llu,\"complete\":%s,\"epoch\":%llu,\"tick\":%d,\"window_ticks\":10,\"calls\":%llu,\"captured\":%llu,\"rejected\":%llu,\"no_change\":%llu,\"events\":[",
        g_heal_ready?"true":"false",g_heal_sources_attested?"true":"false",g_effect_origin_ready?"true":"false",
        (unsigned long long)g_effect_origin_epoch,complete?"true":"false",
        (unsigned long long)epoch,tick,(unsigned long long)calls,(unsigned long long)last,
        (unsigned long long)rejected,(unsigned long long)no_change);
    if(complete)for(int i=0;i<n;++i) {
        const auto &e=events[i];
        offset+=snprintf(json+offset,size-offset,
            "%s{\"sequence\":%llu,\"tick\":%d,\"source_id\":%u,\"source_data_id\":%u,\"source_owner\":%d,\"source_card_id\":%d,\"projectile_id\":%u,\"projectile_data_id\":%u,\"target_id\":%u,\"target_data_id\":%u,\"target_owner\":%d,\"target_card_id\":%d,\"requested\":%d,\"amount\":%d,\"pre_hp\":%d,\"post_hp\":%d,\"pre_shield\":%d,\"post_shield\":%d,\"shield\":%s,\"caller_offset\":%llu,\"origin\":",
            i?",":"",(unsigned long long)e.sequence,e.tick,e.source.id,e.source.gid,e.source.owner,e.source.card,
            e.projectile_id,e.projectile_gid,e.target.id,e.target.gid,e.target.owner,e.target.card,e.requested,e.amount,
            e.pre_hp,e.post_hp,e.pre_shield,e.post_shield,e.shield?"true":"false",(unsigned long long)e.caller_offset);
        if(e.origin.sequence)append_effect_origin(json,offset,size,e.origin);
        else offset+=snprintf(json+offset,size-offset,"null");
        offset+=snprintf(json+offset,size-offset,"}");
    }
    offset+=snprintf(json+offset,size-offset,"]}");
}
