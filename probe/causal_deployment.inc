// Read-only exact consume-card -> live-object-add dynamic scopes.
// Adapted from FirstLight combat_event_telemetry.inc for the attested build.
// Preserve exact object identity across the stock pending-add queue. No
// time/card/proximity fallback; unrelated delayed producers remain unbound.
struct DeploymentScope {
    void *world=nullptr,*objects=nullptr;
    uint64_t epoch=0,sequence=0;
    int owner=-1,tick=-1,slot=-1,form=-1;
    uint32_t card=0,packed=0;
};
struct DeploymentMember {
    void *pointer=nullptr;
    uint32_t id=0;
    DeploymentScope scope;
    uintptr_t caller=0;
    bool pending=false,queued_origin=false;
    uintptr_t commit_caller=0;
};
constexpr int kDeploymentMemberCapacity=4096;
DeploymentMember g_deployment_members[kDeploymentMemberCapacity];
pthread_mutex_t g_deployment_mutex=PTHREAD_MUTEX_INITIALIZER;
void *g_deployment_world=nullptr;
int g_deployment_tick=-1;
uint64_t g_deployment_epoch=0,g_deployment_sequence=0;
uint64_t g_deployment_rejected=0,g_deployment_bound=0,g_deployment_unscoped=0,g_deployment_overflow=0;
uint64_t g_deployment_queued=0,g_deployment_promoted=0;
bool g_deployment_hooks_ready=false;
thread_local DeploymentScope g_deployment_scope;
thread_local int g_deployment_spawn_depth=0;
using DeploymentConsumeFn=void(*)(void*,const CardSelection*,int,int,int,int);
using DeploymentSpawnFn=void(*)(void*,void*,int,int);
DeploymentConsumeFn g_deployment_consume_original=nullptr;
DeploymentSpawnFn g_deployment_spawn_original=nullptr;
void (*g_spawn_relation_observer)(void*,void*,int)=nullptr;
#if CR_EXPERIMENTAL_PRODUCER_ORIGINS
void (*g_area_relation_observer)(void*,void*,int)=nullptr;
void (*g_projectile_relation_observer)(void*,void*,int)=nullptr;
#endif

void deployment_epoch(void *world,int tick) {
    pthread_mutex_lock(&g_deployment_mutex);
    if (world!=g_deployment_world || tick<g_deployment_tick) {
        for (auto &m:g_deployment_members) m=DeploymentMember{};
        g_deployment_world=world; ++g_deployment_epoch;
        g_deployment_rejected=g_deployment_bound=g_deployment_unscoped=g_deployment_overflow=0;
        g_deployment_queued=g_deployment_promoted=0;
    }
    g_deployment_tick=tick;
    pthread_mutex_unlock(&g_deployment_mutex);
}

bool deployment_context(void *&world,void *&objects,int &tick) {
    void *manager=g_live_manager.load(),*player=nullptr;
    if (!manager || !g_game_state_tick || !safe_field(manager,0xa8,world) || !world ||
        !safe_field(world,0xe0,player) || !safe_field(player,0x10,objects) || !objects) return false;
    tick=g_game_state_tick(manager);
    return tick>=0;
}

bool capture_deployment_scope(void *player,const CardSelection *descriptor,DeploymentScope &out) {
    CardSelection selected;
    if (!deployment_context(out.world,out.objects,out.tick) || !safe_bytes(descriptor,&selected,20)) return false;
    deployment_epoch(out.world,out.tick);
    void *candidate=nullptr,*deck=nullptr,*slots=nullptr,*slot=nullptr,*data=nullptr;
    for (int o=0;o<2;++o) if (safe_field(out.world,0xe0+8*o,candidate) && candidate==player) out.owner=o;
    out.slot=int((selected.packed>>22)&63)-1; out.form=selected.packed&15;
    int count=0;
    if (out.owner<0 || out.slot<0 || out.slot>=8 || out.form>2 ||
        !safe_field(out.world,0x88+8*out.owner,deck) || !runtime_vector(deck,0x20,8,slots,count) || count!=8 ||
        !safe_field(slots,8*out.slot,slot) || !safe_field(slot,0x10,data) || data!=selected.data ||
        !safe_field(data,0x40,out.card) || !out.card || out.card==28000006) return false;
    // Exact descriptor comparison; hero secondary pointer is opaque.
    const uint8_t expected[]={0xa0,0x00,0x00,0xb4,0x09,0x00,0x40,0xf9,0x42,0x00,0x00,0x12,0x23,0x89,0x40,0xf9};
    void *builder=reinterpret_cast<void*>(g_libg_base+0xe2d1bc);
    uint8_t actual[16];
    if (!safe_bytes(builder,actual,16) || memcmp(actual,expected,16)) return false;
    CardSelection rebuilt;
    cr_read_card_selection(slot,player,0,&rebuilt,builder);
    if (selected.data!=rebuilt.data || selected.secondary!=rebuilt.secondary || selected.packed!=rebuilt.packed) return false;
    out.packed=selected.packed;
    pthread_mutex_lock(&g_deployment_mutex);
    out.epoch=g_deployment_epoch; out.sequence=++g_deployment_sequence;
    pthread_mutex_unlock(&g_deployment_mutex);
    return true;
}

void deployment_consume(void *player,const CardSelection *descriptor,int a,int b,int c,int d) {
    const auto previous=g_deployment_scope;
    DeploymentScope scope;
    if (!capture_deployment_scope(player,descriptor,scope)) {
        scope=DeploymentScope{};
        pthread_mutex_lock(&g_deployment_mutex); ++g_deployment_rejected; pthread_mutex_unlock(&g_deployment_mutex);
    }
    g_deployment_scope=scope;
    g_deployment_consume_original(player,descriptor,a,b,c,d);
    g_deployment_scope=previous;
}

void deployment_spawn(void *objects,void *object,int option,int commit) {
    const uintptr_t caller=reinterpret_cast<uintptr_t>(__builtin_return_address(0))-g_libg_base;
    const int depth=g_deployment_spawn_depth++;
    g_deployment_spawn_original(objects,object,option,commit);
    --g_deployment_spawn_depth;
    if (g_spawn_relation_observer) g_spawn_relation_observer(objects,object,commit);
#if CR_EXPERIMENTAL_PRODUCER_ORIGINS
    if (g_area_relation_observer) g_area_relation_observer(objects,object,commit);
    if (g_projectile_relation_observer) g_projectile_relation_observer(objects,object,commit);
#endif
    if (commit!=0 && commit!=1) return;
    void *world=nullptr,*live_objects=nullptr,*object_manager=nullptr,*vector=nullptr;
    int tick=-1,count=0,owner=-1; uint32_t id=0;
    if (!deployment_context(world,live_objects,tick) || live_objects!=objects ||
        !safe_field(object,0x10,object_manager) || object_manager!=objects ||
        !runtime_vector(objects,commit?8:0x28,4096,vector,count) ||
        !safe_field(object,8,id) || !id || !safe_field(object,0x78,owner)) return;
    int matches=0;
    for (int i=0;i<count;++i) { void *p=nullptr; if (safe_field(vector,8*i,p) && p==object) ++matches; }
    if (matches!=1) return;
    deployment_epoch(world,tick);
    pthread_mutex_lock(&g_deployment_mutex);
    const auto &scope=g_deployment_scope;
    bool exact=depth==0 && scope.sequence && scope.epoch==g_deployment_epoch && scope.world==world &&
        scope.objects==objects && scope.owner==owner;
    DeploymentMember *entry=nullptr;
    for (int i=0;i<kDeploymentMemberCapacity;++i) {
        auto &m=g_deployment_members[(id+i)%kDeploymentMemberCapacity];
        if (!m.id || m.id==id) { entry=&m; break; }
    }
    if (!entry) ++g_deployment_overflow;
    else if (commit && entry->id==id && entry->pointer==object && entry->pending &&
        entry->scope.sequence && entry->scope.epoch==g_deployment_epoch && entry->scope.world==world &&
        entry->scope.objects==objects && entry->scope.owner==owner &&
        (!exact || entry->scope.sequence==scope.sequence)) {
        // f2593c appends the exact object to objects+0x28, then registers it
        // with commit=0. f25a38 drains that queue and calls this hook with
        // commit=1. Derived object managers may forward through another call
        // site, so continuity is proved by the queued pointer AND assigned ID,
        // manager, epoch, owner and live membership, not one return address.
        entry->pending=false; entry->commit_caller=caller;
        ++g_deployment_promoted; ++g_deployment_bound;
    } else if (entry->id && entry->pointer==object && entry->scope.sequence) {
        // A second registration cannot silently overwrite an earlier root.
        if (!exact || entry->scope.sequence!=scope.sequence) { entry->scope={}; ++g_deployment_rejected; }
    } else {
        *entry={object,id,exact?scope:DeploymentScope{},caller,!commit,!commit};
        if (exact) { if (commit) ++g_deployment_bound; else ++g_deployment_queued; }
        else if (commit) ++g_deployment_unscoped;
    }
    pthread_mutex_unlock(&g_deployment_mutex);
}

void append_deployment_member(char *json,int &offset,size_t size,void *object) {
    uint32_t id=0; if (!safe_field(object,8,id)) return;
    DeploymentMember found;
    pthread_mutex_lock(&g_deployment_mutex);
    for (int i=0;i<kDeploymentMemberCapacity;++i) {
        const auto &m=g_deployment_members[(id+i)%kDeploymentMemberCapacity];
        if (!m.id) break;
        if (m.id==id) { if (m.pointer==object && m.scope.epoch==g_deployment_epoch) found=m; break; }
    }
    pthread_mutex_unlock(&g_deployment_mutex);
    if (!g_deployment_hooks_ready || !found.scope.sequence || found.pending) return;
    const auto &s=found.scope;
    offset+=snprintf(json+offset,size-offset,
        ",\"deployment_origin\":{\"schema\":\"nulls-deployment.v1\",\"validated\":true,"
        "\"evidence\":\"%s\",\"epoch\":%llu,\"sequence\":%llu,"
        "\"entity_id\":%u,\"owner\":%d,\"source_card_id\":%u,\"deck_slot\":%d,"
        "\"form_code\":%d,\"consumed_tick\":%d,\"spawn_caller\":%llu,\"commit_caller\":%llu}",
        found.queued_origin?"consume_card_queued_object":"consume_card_dynamic_scope",
        (unsigned long long)s.epoch,(unsigned long long)s.sequence,id,s.owner,s.card,s.slot,s.form,s.tick,
        (unsigned long long)found.caller,(unsigned long long)found.commit_caller);
}

void append_deployment_diagnostics(char *json,int &offset,size_t size) {
    pthread_mutex_lock(&g_deployment_mutex);
    offset+=snprintf(json+offset,size-offset,
        ",\"causal_deployment_runtime\":{\"schema\":\"nulls-deployment.v1\",\"hooks_ready\":%s,"
        "\"epoch\":%llu,\"bound_spawns\":%llu,\"unscoped_spawns\":%llu,\"rejected\":%llu,\"overflow\":%llu,"
        "\"queued_spawns\":%llu,\"promoted_spawns\":%llu}",
        g_deployment_hooks_ready?"true":"false",(unsigned long long)g_deployment_epoch,
        (unsigned long long)g_deployment_bound,(unsigned long long)g_deployment_unscoped,
        (unsigned long long)g_deployment_rejected,(unsigned long long)g_deployment_overflow,
        (unsigned long long)g_deployment_queued,(unsigned long long)g_deployment_promoted);
    pthread_mutex_unlock(&g_deployment_mutex);
}

bool install_deployment_observers() {
    const uint8_t consume[]={0xff,0x83,0x03,0xd1,0xfd,0x7b,0x08,0xa9,0xfc,0x6f,0x09,0xa9,0xfa,0x67,0x0a,0xa9};
    const uint8_t spawn[]={0xfd,0x7b,0xbc,0xa9,0xf7,0x0b,0x00,0xf9,0xf6,0x57,0x02,0xa9,0xf4,0x4f,0x03,0xa9};
    if (memcmp(reinterpret_cast<void*>(g_libg_base+0xf38a68),consume,16) ||
        memcmp(reinterpret_cast<void*>(g_libg_base+0xf25b8c),spawn,16)) return false;
    return install_inline_hook(g_libg_base+0xf38a68,consume,16,reinterpret_cast<void*>(deployment_consume),
        reinterpret_cast<void**>(&g_deployment_consume_original),"causal consume") &&
        install_inline_hook(g_libg_base+0xf25b8c,spawn,16,reinterpret_cast<void*>(deployment_spawn),
        reinterpret_cast<void**>(&g_deployment_spawn_original),"causal spawn");
}
