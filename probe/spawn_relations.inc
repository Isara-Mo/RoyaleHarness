// Exact ActionSpawnToLocation provenance, adapted from FirstLight remaining runtime.
// Preserve producer identity across queued admission. Never infer ancestry by tick/position.
struct SummonScope {
    void *world=nullptr,*objects=nullptr,*source=nullptr,*expected_data=nullptr;
    uint64_t epoch=0,action_sequence=0;
    uint32_t source_id=0,source_gid=0,action_gid=0;
    int source_card=0,source_owner=-1,tick=-1,mode=-1;
    int kind=0; // 0 ActionSpawnToLocation, 1 exact active-buff tick
};
struct SummonMember {
    SummonScope scope;
    void *child=nullptr;
    uint32_t id=0,child_gid=0;
    int owner=-1,committed_tick=-1;
    bool committed=false,invalid=false;
    int route=0;
};
SummonMember g_summons[4096];
int g_summon_count=0;
uint64_t g_summon_epoch=0,g_summon_actions=0,g_summon_sequence=0;
uint64_t g_summon_captured=0,g_summon_promoted=0,g_summon_rejected=0,g_summon_overflow=0;
bool g_summon_hooks_ready=false;
pthread_mutex_t g_summon_mutex=PTHREAD_MUTEX_INITIALIZER;
thread_local SummonScope g_summon_scope;
using SummonActionFn=void(*)(void*,void*,void*,void*);
using SummonPendingFn=void(*)(void*,void*);
using SummonTaggedFn=void(*)(void*,void*,uint32_t);
SummonTaggedFn g_summon_tagged_original=nullptr;
using SummonBuffFn=void(*)(void*);
SummonBuffFn g_summon_buff_original=nullptr;
SummonActionFn g_summon_action_original=nullptr;
SummonPendingFn g_summon_pending_original=nullptr;
#if CR_EXPERIMENTAL_PRODUCER_ORIGINS
void (*g_projectile_queued_observer)(void*,void*)=nullptr;
#endif
extern "C" { void *g_summon_mode1_original=nullptr; void summon_mode1_entry(); }

void summon_epoch() {
    pthread_mutex_lock(&g_summon_mutex);
    if (g_summon_epoch!=g_deployment_epoch) {
        g_summon_epoch=g_deployment_epoch; g_summon_count=0;
        g_summon_actions=g_summon_captured=g_summon_promoted=g_summon_rejected=g_summon_overflow=0;
    }
    pthread_mutex_unlock(&g_summon_mutex);
}
bool summon_live(void *objects,void *object,uint32_t &id) {
    void *vector=nullptr; int count=0;
    if (!runtime_vector(objects,8,4096,vector,count)) return false;
    const auto ref=live_reference(object,static_cast<void**>(vector),count);
    if (!object || !ref.known || !ref.id) return false;
    id=ref.id; return true;
}
bool summon_absent(void *objects,void *object) {
    void *vector=nullptr; int count=0;
    if (!object || !runtime_vector(objects,8,4096,vector,count)) return false;
    for (int i=0;i<count;++i) {
        void *p=nullptr;
        if (!safe_field(vector,8*i,p) || p==object) return false;
    }
    return true;
}
bool summon_scope_capture(void *action,void *context,SummonScope &s) {
    uintptr_t vt=0; void *source_data=nullptr;
    if (!deployment_context(s.world,s.objects,s.tick)) return false;
    deployment_epoch(s.world,s.tick); summon_epoch();
    if (!safe_field(action,0,vt) || vt!=g_libg_base+0x1894588 ||
        !safe_field(context,8,s.source) || !summon_live(s.objects,s.source,s.source_id) ||
        !safe_field(s.source,0x48,source_data) || !safe_field(source_data,0x40,s.source_gid) || !s.source_gid ||
        !safe_field(s.source,0xac,s.source_card) || !safe_field(s.source,0x78,s.source_owner) ||
        s.source_owner<0 || s.source_owner>1 || !safe_field(action,0x40,s.action_gid) || !s.action_gid ||
        !safe_field(action,0xec,s.mode) || !safe_field(action,0xf0,s.expected_data) || !s.expected_data) return false;
    pthread_mutex_lock(&g_summon_mutex);
    s.epoch=g_summon_epoch; s.action_sequence=++g_summon_sequence;
    pthread_mutex_unlock(&g_summon_mutex);
    return true;
}
void summon_action(void *action,void *context,void *target,void *aux) {
    SummonScope previous=g_summon_scope,scope;
    const bool valid=summon_scope_capture(action,context,scope);
    pthread_mutex_lock(&g_summon_mutex); ++g_summon_actions; pthread_mutex_unlock(&g_summon_mutex);
    g_summon_scope=valid?scope:SummonScope{}; // invalid nested actions must shadow outer producers
    g_summon_action_original(action,context,target,aux);
    g_summon_scope=previous;
}
bool summon_buff_scope(void *entry,SummonScope &s) {
    void *component=nullptr,*buff=nullptr,*source_data=nullptr,*entries=nullptr;
    uintptr_t vt=0; int count=0;
    if (!deployment_context(s.world,s.objects,s.tick)) return false;
    deployment_epoch(s.world,s.tick); summon_epoch();
    if (!safe_field(entry,0x20,component) || !safe_field(component,8,s.source) ||
        runtime_component(s.source,3)!=component || !summon_live(s.objects,s.source,s.source_id) ||
        !runtime_vector(component,0x18,64,entries,count) ||
        !safe_field(entry,0x18,buff) || !safe_field(buff,0,vt) || vt!=g_libg_base+0x1882500 ||
        !safe_field(buff,0x40,s.action_gid) || !s.action_gid || !safe_field(buff,0x170,s.expected_data) ||
        !s.expected_data || !safe_field(s.source,0x48,source_data) ||
        !safe_field(source_data,0x40,s.source_gid) || !s.source_gid ||
        !safe_field(s.source,0xac,s.source_card) || !safe_field(s.source,0x78,s.source_owner) ||
        s.source_owner<0 || s.source_owner>1) return false;
    int matches=0;
    for (int i=0;i<count;++i) { void *p=nullptr; if (safe_field(entries,8*i,p) && p==entry) ++matches; }
    if (matches!=1) return false;
    pthread_mutex_lock(&g_summon_mutex);
    s.kind=1; s.epoch=g_summon_epoch; s.action_sequence=++g_summon_sequence;
    pthread_mutex_unlock(&g_summon_mutex);
    return true;
}
void summon_buff_tick(void *entry) {
    SummonScope previous=g_summon_scope,scope;
    g_summon_scope=summon_buff_scope(entry,scope)?scope:SummonScope{};
    g_summon_buff_original(entry);
    g_summon_scope=previous;
}
void summon_capture_child(void *objects,void *child,int route) {
    const auto &scope=g_summon_scope;
    if (!scope.action_sequence || scope.objects!=objects || child==scope.source || !child) return;
    void *data=nullptr; uint32_t gid=0,live_id=0; int owner=-1;
    bool valid=scope.epoch==g_deployment_epoch && safe_field(child,0x48,data) && data==scope.expected_data &&
        safe_field(data,0x40,gid) && gid && safe_field(child,0x78,owner) && owner>=0 && owner<=1 &&
        summon_absent(objects,child);
    pthread_mutex_lock(&g_summon_mutex);
    if (!valid) ++g_summon_rejected;
    else {
        SummonMember *member=nullptr;
        for (int i=0;i<g_summon_count;++i) if (g_summons[i].child==child) { member=&g_summons[i]; break; }
        if (member) {
            // Never overwrite an earlier child identity, even if its pointer was recycled.
            member->invalid=true; ++g_summon_rejected;
        } else if (g_summon_count>=4096) ++g_summon_overflow;
        else {
            g_summons[g_summon_count++]={scope,child,0,gid,owner,-1,false,false,route};
            ++g_summon_captured;
        }
    }
    pthread_mutex_unlock(&g_summon_mutex);
}
void summon_pending(void *objects,void *child) {
#if CR_EXPERIMENTAL_PRODUCER_ORIGINS
    if (g_projectile_queued_observer) g_projectile_queued_observer(objects,child);
#endif
    summon_capture_child(objects,child,1);
    g_summon_pending_original(objects,child);
}
void summon_tagged(void *objects,void *child,uint32_t tag) {
    summon_capture_child(objects,child,3);
    g_summon_tagged_original(objects,child,tag);
}
extern "C" void summon_mode1_bridge(void *objects,void *child,int option,int commit,
                                    const void *lr,const void *ambient,const void *request_slot) {
    if (reinterpret_cast<uintptr_t>(lr)!=g_libg_base+0xf24bb0 || option!=1 || commit!=0 ||
        g_summon_scope.mode!=1 || !g_summon_scope.action_sequence) return;
    uintptr_t slot=reinterpret_cast<uintptr_t>(request_slot);
    void *data=nullptr,*source=nullptr;
    if (slot<0x48 || !safe_field(reinterpret_cast<void*>(slot-0x48),0,data) ||
        !safe_field(reinterpret_cast<void*>(slot-0x48),8,source) ||
        ambient!=g_summon_scope.source || source!=g_summon_scope.source || data!=g_summon_scope.expected_data) {
        pthread_mutex_lock(&g_summon_mutex); ++g_summon_rejected; pthread_mutex_unlock(&g_summon_mutex); return;
    }
    summon_capture_child(objects,child,2);
}
void summon_registered(void *objects,void *child,int commit) {
    if (commit!=0 && commit!=1) return;
    void *world=nullptr,*current_objects=nullptr; int tick=-1;
    if (!deployment_context(world,current_objects,tick) || objects!=current_objects) return;
    deployment_epoch(world,tick); summon_epoch();
    pthread_mutex_lock(&g_summon_mutex);
    for (int i=0;i<g_summon_count;++i) {
        auto &m=g_summons[i];
        if (m.child!=child || m.invalid || m.committed) continue;
        uint32_t id=0,source_id=0,gid=0; int owner=-1; void *manager=nullptr,*data=nullptr;
        bool valid=m.scope.epoch==g_summon_epoch && m.scope.world==world && m.scope.objects==objects &&
            m.scope.tick==tick && safe_field(child,8,id) && id && id!=m.scope.source_id &&
            (!m.id || m.id==id) && safe_field(child,0x10,manager) && manager==objects &&
            safe_field(child,0x78,owner) && owner==m.owner && safe_field(child,0x48,data) &&
            data==m.scope.expected_data && safe_field(data,0x40,gid) && gid==m.child_gid;
        // Source may have died/transformed during this exact action/tick; a
        // present pointer with another ID is never allowed to replace it.
        bool source_live=summon_live(objects,m.scope.source,source_id);
        if (source_live ? source_id!=m.scope.source_id : !summon_absent(objects,m.scope.source)) valid=false;
        if (commit) { uint32_t live_id=0; valid=valid && summon_live(objects,child,live_id) && live_id==id; }
        if (!valid) { m.invalid=true; ++g_summon_rejected; }
        else { m.id=id; if (commit) { m.committed=true; m.committed_tick=tick; ++g_summon_promoted; } }
        break;
    }
    pthread_mutex_unlock(&g_summon_mutex);
}
void append_spawn_relation(char *json,int &offset,size_t size,void *object) {
    if (!g_summon_hooks_ready) return;
    uint32_t id=0; if (!safe_field(object,8,id)) return;
    pthread_mutex_lock(&g_summon_mutex);
    for (int i=0;i<g_summon_count;++i) {
        const auto &m=g_summons[i]; const auto &s=m.scope;
        if (m.child!=object || m.id!=id || !m.committed || m.invalid || s.epoch!=g_deployment_epoch) continue;
        offset+=snprintf(json+offset,size-offset,
            ",\"spawn_origin\":{\"schema\":\"nulls-spawn.v1\",\"validated\":true,\"epoch\":%llu,\"sequence\":%d,"
            "\"action_sequence\":%llu,\"evidence\":\"%s\",\"route\":%d,"
            "\"entity_id\":%u,\"owner\":%d,\"child_data_id\":%u,\"parent_id\":%u,\"parent_owner\":%d,"
            "\"parent_card_id\":%d,\"parent_data_id\":%u,\"action_data_id\":%u,\"created_tick\":%d}",
            (unsigned long long)s.epoch,i+1,(unsigned long long)s.action_sequence,
            s.kind==1?"buff_tick_nested_spawn":"action_spawn_to_location",m.route,
            m.id,m.owner,m.child_gid,s.source_id,s.source_owner,s.source_card,s.source_gid,s.action_gid,m.committed_tick);
        break;
    }
    pthread_mutex_unlock(&g_summon_mutex);
}
void append_spawn_diagnostics(char *json,int &offset,size_t size) {
    summon_epoch();
    pthread_mutex_lock(&g_summon_mutex);
    offset+=snprintf(json+offset,size-offset,
        ",\"spawn_relation_runtime\":{\"schema\":\"nulls-spawn.v1\",\"hooks_ready\":%s,\"epoch\":%llu,"
        "\"actions\":%llu,\"captured\":%llu,\"promoted\":%llu,\"rejected\":%llu,\"overflow\":%llu}",
        g_summon_hooks_ready?"true":"false",(unsigned long long)g_summon_epoch,
        (unsigned long long)g_summon_actions,(unsigned long long)g_summon_captured,(unsigned long long)g_summon_promoted,
        (unsigned long long)g_summon_rejected,(unsigned long long)g_summon_overflow);
    pthread_mutex_unlock(&g_summon_mutex);
}
bool install_spawn_observers() {
    const uint8_t action[]={0xff,0x83,0x03,0xd1,0xfd,0x7b,0x08,0xa9,0xfc,0x6f,0x09,0xa9,0xfa,0x67,0x0a,0xa9};
    const uint8_t pending[]={0x08,0x00,0x40,0xf9,0x02,0xe0,0x41,0x39,0xe3,0x03,0x1f,0x2a,0x04,0x21,0x40,0xf9};
    const uint8_t tagged[]={0x08,0x00,0x40,0xf9,0xe3,0x03,0x02,0x2a,0x22,0x00,0x80,0x52,0x04,0x21,0x40,0xf9};
    const uint8_t mode1[]={0xfd,0x7b,0xbc,0xa9,0xf7,0x0b,0x00,0xf9,0xf6,0x57,0x02,0xa9,0xf4,0x4f,0x03,0xa9};
    const uint8_t buff[]={0xff,0x03,0x03,0xd1,0xfd,0x7b,0x08,0xa9,0xf8,0x5f,0x09,0xa9,0xf6,0x57,0x0a,0xa9};
    const uint32_t spawn_call=0x97ffe21c; // f20aa4 -> f19314
    uint32_t actual_call=0;
    if (!g_deployment_hooks_ready || memcmp(reinterpret_cast<void*>(g_libg_base+0xe7b864),action,16) ||
        memcmp(reinterpret_cast<void*>(g_libg_base+0xf25928),pending,16) ||
        memcmp(reinterpret_cast<void*>(g_libg_base+0xf25914),tagged,16) ||
        memcmp(reinterpret_cast<void*>(g_libg_base+0xf2ffb8),mode1,16) ||
        memcmp(reinterpret_cast<void*>(g_libg_base+0xf208dc),buff,16) ||
        !safe_bytes(reinterpret_cast<void*>(g_libg_base+0xf20aa4),&actual_call,4) || actual_call!=spawn_call) return false;
    g_spawn_relation_observer=summon_registered;
    return install_inline_hook(g_libg_base+0xe7b864,action,16,reinterpret_cast<void*>(summon_action),
        reinterpret_cast<void**>(&g_summon_action_original),"spawn action") &&
        install_inline_hook(g_libg_base+0xf25928,pending,16,reinterpret_cast<void*>(summon_pending),
        reinterpret_cast<void**>(&g_summon_pending_original),"spawn pending") &&
        install_inline_hook(g_libg_base+0xf2ffb8,mode1,16,reinterpret_cast<void*>(summon_mode1_entry),
        &g_summon_mode1_original,"spawn mode1") &&
        install_inline_hook(g_libg_base+0xf208dc,buff,16,reinterpret_cast<void*>(summon_buff_tick),
        reinterpret_cast<void**>(&g_summon_buff_original),"spawn buff tick") &&
        install_inline_hook(g_libg_base+0xf25914,tagged,16,reinterpret_cast<void*>(summon_tagged),
        reinterpret_cast<void**>(&g_summon_tagged_original),"spawn tagged pending");
}
