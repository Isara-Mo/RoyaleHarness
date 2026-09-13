// Adapted from FirstLight combat_impact_hook. A processed target is an impact
// fact, NOT proof of positive damage or of terminal projectile lifetime.
struct ImpactFact { uint32_t id=0,gid=0; int owner=-1,card=0; };
struct ImpactEvent {
    uint64_t sequence=0;
    int tick=-1,x=0,y=0,proof=0;
    ImpactFact projectile,target;
};
struct ImpactScope {
    ImpactScope *previous=nullptr;
    void *projectile=nullptr,*candidate=nullptr,*world=nullptr,*manager=nullptr;
    ImpactFact projectile_fact,target_fact;
    int tick=-1;
    bool validated=false,damage_proved=false;
};
thread_local ImpactScope *g_impact_scope=nullptr;
void impact_note_damage(void *immediate,void *target,void *world,void *manager,int tick,
                        uint32_t projectile_id,uint32_t projectile_gid,const ImpactFact &victim) {
    auto *scope=g_impact_scope;
    if(scope && scope->validated && scope->projectile==immediate && scope->candidate==target &&
        scope->world==world && scope->manager==manager && scope->tick==tick &&
        scope->projectile_fact.id==projectile_id && scope->projectile_fact.gid==projectile_gid &&
        scope->target_fact.id==victim.id && scope->target_fact.gid==victim.gid &&
        scope->target_fact.owner==victim.owner) scope->damage_proved=true;
}
constexpr int kImpactCapacity=512, kImpactWindow=10;
ImpactEvent g_impacts[kImpactCapacity];
pthread_mutex_t g_impact_mutex=PTHREAD_MUTEX_INITIALIZER;
void *g_impact_world=nullptr;
int g_impact_tick=-1,g_impact_lost_tick=-1;
uint64_t g_impact_epoch=0,g_impact_sequence=0,g_impact_rejected=0,g_impact_calls=0;
uint64_t g_impact_layout_valid=0,g_impact_zero_results=0,g_impact_zero_appends=0;
bool g_impact_ready=false;
using ImpactFn=int(*)(void*,void*,int,int,int);
ImpactFn g_impact_original=nullptr;

void impact_epoch_locked(void *world,int tick) {
    if (world!=g_impact_world || tick<g_impact_tick) {
        for (auto &e:g_impacts) e=ImpactEvent{};
        ++g_impact_epoch; g_impact_sequence=0; g_impact_rejected=0;
        g_impact_calls=0; g_impact_lost_tick=-1;
        g_impact_layout_valid=0;g_impact_zero_results=0;g_impact_zero_appends=0;
    }
    g_impact_world=world; g_impact_tick=tick;
}
bool impact_vector(void *manager,void **&objects,int &count) {
    int capacity=0;
    static thread_local void *copy[600];
    bool valid=safe_field(manager,8,objects) && safe_field(manager,0x10,capacity) &&
        safe_field(manager,0x14,count) && count>0 && count<=600 && capacity>=count &&
        capacity<=4096 && objects && safe_bytes(objects,copy,size_t(count)*sizeof(void*));
    if (valid) objects=copy;
    return valid;
}
bool impact_fact(void *object,void *manager,void **objects,int count,ImpactFact &out) {
    LiveReference ref=live_reference(object,objects,count);
    void *actual_manager=nullptr,*data=nullptr;
    if (!ref.known || !ref.id || !safe_field(object,0x10,actual_manager) || actual_manager!=manager ||
        !safe_field(object,0x48,data) || !safe_field(data,0x40,out.gid) || !out.gid ||
        !safe_field(object,0x78,out.owner) || out.owner<0 || out.owner>1 ||
        !safe_field(object,0xac,out.card)) return false;
    out.id=ref.id; return true;
}
bool impact_same_identity(void *object,const ImpactFact &fact) {
    void *data=nullptr; uint32_t id=0,gid=0; int owner=-1;
    return safe_field(object,8,id) && id==fact.id && safe_field(object,0x48,data) &&
        safe_field(data,0x40,gid) && gid==fact.gid && safe_field(object,0x78,owner) && owner==fact.owner;
}
bool impact_new_target(const int32_t *processed,int before,int after,int capacity,uint32_t target) {
    if (!processed || before<0 || after<=before || after>capacity || capacity>4096 || !target) return false;
    int matches=0;
    for (int i=before;i<after;++i) {
        uint32_t id=0;
        if (!safe_field(processed,size_t(i)*4,id)) return false;
        if (id==target) ++matches;
    }
    return matches==1;
}
int impact_observer(void *projectile,void *candidate,int x,int y,int z) {
    void *world=nullptr,*manager=nullptr; void **objects=nullptr;
    int tick=-1,count=0,pre=-1,capacity=-1;
    uintptr_t vt=0; uint64_t epoch=0;
    ImpactEvent event;
    bool context=deployment_context(world,manager,tick);
    if (context) {
        pthread_mutex_lock(&g_impact_mutex);
        impact_epoch_locked(world,tick); epoch=g_impact_epoch; ++g_impact_calls;
        pthread_mutex_unlock(&g_impact_mutex);
    }
    bool capture=context && projectile_layout_attested() &&
        safe_field(projectile,0,vt) && vt==g_libg_base+0x189cc28 &&
        impact_vector(manager,objects,count) &&
        impact_fact(projectile,manager,objects,count,event.projectile) &&
        impact_fact(candidate,manager,objects,count,event.target) &&
        event.projectile.id!=event.target.id &&
        safe_field(projectile,0x6c,pre) && safe_field(projectile,0x68,capacity) &&
        pre>=0 && pre<=capacity && capacity<=4096;
    ImpactScope scope{g_impact_scope,projectile,candidate,world,manager,
        event.projectile,event.target,tick,capture,false};
    g_impact_scope=&scope;
    int result=g_impact_original(projectile,candidate,x,y,z);
    g_impact_scope=scope.previous;
    if (!context) return result;
    pthread_mutex_lock(&g_impact_mutex);
    if(epoch==g_impact_epoch) {
        if(capture)++g_impact_layout_valid;
        if(!(result&1)) {
            ++g_impact_zero_results;
            int after=-1;
            if(capture && safe_field(projectile,0x6c,after) && after>pre)++g_impact_zero_appends;
        }
    }
    pthread_mutex_unlock(&g_impact_mutex);
    // f29c58 returns 0 after ordinary piercing damage/knockback. The return
    // bit only attests its terminal branch, not all successful target hits.
    // A second proof requires exact damage INSIDE this invocation to this
    // candidate by this immediate projectile, plus the new-target check below.
    if (!(result&1) && !scope.damage_proved) return result;
    int post=-1; const int32_t *processed=nullptr;
    void *after_world=nullptr,*after_manager=nullptr; int after_tick=-1;
    bool valid=capture && deployment_context(after_world,after_manager,after_tick) &&
        world==after_world && manager==after_manager && tick==after_tick &&
        impact_same_identity(projectile,event.projectile) && impact_same_identity(candidate,event.target) &&
        safe_field(projectile,0x60,processed) && safe_field(projectile,0x6c,post) &&
        safe_field(projectile,0x68,capacity) &&
        impact_new_target(processed,pre,post,capacity,event.target.id);
    pthread_mutex_lock(&g_impact_mutex);
    if (world==g_impact_world && epoch==g_impact_epoch && tick>=g_impact_tick) {
        if (valid) {
            event.sequence=++g_impact_sequence; event.tick=tick; event.x=x; event.y=y;
            event.proof=(result&1)?1:2;
            auto &slot=g_impacts[(event.sequence-1)%kImpactCapacity];
            if (slot.sequence) g_impact_lost_tick=slot.tick;
            slot=event;
        } else ++g_impact_rejected;
    }
    pthread_mutex_unlock(&g_impact_mutex);
    return result;
}
bool install_impact_observer() {
    const uint8_t prologue[]={0xff,0xc3,0x02,0xd1,0xfd,0x7b,0x05,0xa9,
                             0xfc,0x6f,0x06,0xa9,0xfa,0x67,0x07,0xa9};
    return install_inline_hook(g_libg_base+0xf29514,prologue,16,
        reinterpret_cast<void*>(&impact_observer),reinterpret_cast<void**>(&g_impact_original),"processed projectile target");
}
void append_impact_events(char *json,int &offset,size_t size,void *world,int tick) {
    ImpactEvent events[kImpactCapacity]; int n=0;
    pthread_mutex_lock(&g_impact_mutex);
    impact_epoch_locked(world,tick);
    uint64_t epoch=g_impact_epoch,last=g_impact_sequence,rejected=g_impact_rejected,calls=g_impact_calls;
    uint64_t layout_valid=g_impact_layout_valid,zero_results=g_impact_zero_results,zero_appends=g_impact_zero_appends;
    bool complete=g_impact_lost_tick<0 || g_impact_lost_tick<tick-kImpactWindow;
    const uint64_t begin=last>kImpactCapacity ? last-kImpactCapacity+1 : 1;
    for (uint64_t seq=begin;seq<=last;++seq) {
        const auto &e=g_impacts[(seq-1)%kImpactCapacity];
        if (e.sequence==seq && e.tick<=tick && e.tick>=tick-kImpactWindow) events[n++]=e;
    }
    pthread_mutex_unlock(&g_impact_mutex);
    complete=complete && offset>=0 && size_t(offset)+size_t(n)*512+4096<size;
    offset+=snprintf(json+offset,size-offset,
        ",\"impact_runtime\":{\"schema\":\"nulls-impact.v2\",\"hooks_ready\":%s,\"complete\":%s,\"epoch\":%llu,\"tick\":%d,\"window_ticks\":%d,\"calls\":%llu,\"captured\":%llu,\"rejected\":%llu,\"layout_valid_calls\":%llu,\"zero_results\":%llu,\"zero_result_appends\":%llu,\"events\":[",
        g_impact_ready?"true":"false",complete?"true":"false",
        (unsigned long long)epoch,tick,kImpactWindow,(unsigned long long)calls,(unsigned long long)last,(unsigned long long)rejected,
        (unsigned long long)layout_valid,(unsigned long long)zero_results,(unsigned long long)zero_appends);
    if (complete) for (int i=0;i<n;++i) {
        const auto &e=events[i];
        offset+=snprintf(json+offset,size-offset,
            "%s{\"sequence\":%llu,\"tick\":%d,\"projectile_id\":%u,\"projectile_data_id\":%u,\"owner\":%d,\"card_id\":%d,\"target_id\":%u,\"target_data_id\":%u,\"target_owner\":%d,\"target_card_id\":%d,\"position\":[%d,%d],\"proof\":%d}",
            i?",":"",(unsigned long long)e.sequence,e.tick,e.projectile.id,e.projectile.gid,
            e.projectile.owner,e.projectile.card,e.target.id,e.target.gid,e.target.owner,e.target.card,e.x,e.y,e.proof);
    }
    offset+=snprintf(json+offset,size-offset,"]}");
}
