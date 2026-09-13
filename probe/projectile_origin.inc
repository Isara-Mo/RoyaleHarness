// Preserve an exact, live +0x100 source observed at queued admission. This is
// separate from the projectile's current route and never guesses a caster.
struct ProjectileOrigin {
    uint64_t epoch=0,sequence=0;
    int captured_tick=-1,committed_tick=-1;
    ImpactFact child,source;
    bool current_source=false;
};
struct ProjectileOriginBinding {
    void *child=nullptr,*source=nullptr,*asset=nullptr,*world=nullptr,*manager=nullptr;
    ProjectileOrigin origin;
    bool valid=false,committed=false,cleared=false;
};
constexpr int kProjectileOriginCapacity=4096;
ProjectileOriginBinding g_projectile_origins[kProjectileOriginCapacity];
bool g_projectile_origin_ready=false;
uint64_t g_projectile_origin_epoch=0,g_projectile_origin_sequence=0;
uint64_t g_projectile_origin_captured=0,g_projectile_origin_promoted=0;
uint64_t g_projectile_origin_rejected=0,g_projectile_origin_overflow=0;
void *g_projectile_origin_world=nullptr,*g_projectile_origin_manager=nullptr;
int g_projectile_origin_tick=-1;
pthread_mutex_t g_projectile_origin_mutex=PTHREAD_MUTEX_INITIALIZER;
struct ProjectileOriginMutation {void *child;ProjectileOriginMutation *previous;};
ProjectileOriginMutation *g_projectile_origin_mutations=nullptr;

void projectile_origin_epoch_locked(void *world,void *manager,int tick) {
    if(world!=g_projectile_origin_world || manager!=g_projectile_origin_manager || tick<g_projectile_origin_tick) {
        for(auto &b:g_projectile_origins)b=ProjectileOriginBinding{};
        ++g_projectile_origin_epoch;g_projectile_origin_sequence=0;
        g_projectile_origin_captured=g_projectile_origin_promoted=0;
        g_projectile_origin_rejected=g_projectile_origin_overflow=0;
    }
    g_projectile_origin_world=world;g_projectile_origin_manager=manager;g_projectile_origin_tick=tick;
}
bool projectile_origin_mutating_locked(void *child) {
    for(auto *m=g_projectile_origin_mutations;m;m=m->previous)if(m->child==child)return true;
    return false;
}
ProjectileOriginBinding *projectile_origin_find_locked(void *child) {
    for(auto &b:g_projectile_origins)if(child && b.child==child)return &b;
    return nullptr;
}
void projectile_origin_reject_locked(ProjectileOriginBinding &b) {
    if(b.valid)++g_projectile_origin_rejected;
    b.valid=false; // Retain the pointer tombstone until an observed lifecycle boundary.
}
bool projectile_origin_same(const ImpactFact &a,const ImpactFact &b) {
    return a.id==b.id && a.gid==b.gid && a.owner==b.owner && a.card==b.card;
}
bool projectile_origin_fact(void *object,void *manager,void **objects,int count,ImpactFact &fact) {
    if(!object || !impact_fact(object,manager,objects,count,fact))return false;
    int matches=0;
    for(int i=0;i<count;++i) {
        uint32_t id=0;
        if(objects[i] && (!safe_field(objects[i],8,id)))return false;
        if(id==fact.id)++matches;
    }
    return matches==1;
}
bool projectile_origin_layout(void *child,void *&asset,ImpactFact &fact,void *&source) {
    uintptr_t vt=0;void *table=nullptr;int type=-1;
    return child && projectile_layout_attested() && safe_field(child,0,vt) &&
        vt==g_libg_base+0x189cc28 && safe_field(child,0x48,asset) &&
        safe_field(asset,0x10,table) && safe_field(table,0x84,type) && type==10 &&
        safe_field(asset,0x40,fact.gid) && fact.gid && safe_field(child,0x78,fact.owner) &&
        fact.owner>=0 && fact.owner<=1 && safe_field(child,0xac,fact.card) &&
        safe_field(child,0x100,source);
}
void projectile_origin_queued(void *manager,void *child) {
    if(!g_projectile_origin_ready || !child)return;
    void *world=nullptr,*actual_manager=nullptr,*asset=nullptr,*source=nullptr,*child_manager=nullptr;
    void **objects=nullptr;int tick=-1,count=0;ImpactFact child_fact,source_fact;
    if(!deployment_context(world,actual_manager,tick) || !world || tick<0 || actual_manager!=manager ||
        !projectile_origin_layout(child,asset,child_fact,source))return;
    pthread_mutex_lock(&g_projectile_origin_mutex);
    projectile_origin_epoch_locked(world,manager,tick);
    if(projectile_origin_mutating_locked(child)) {pthread_mutex_unlock(&g_projectile_origin_mutex);return;}
    if(auto *old=projectile_origin_find_locked(child)) {
        projectile_origin_reject_locked(*old);pthread_mutex_unlock(&g_projectile_origin_mutex);return;
    }
    ProjectileOriginBinding *slot=nullptr;
    for(auto &b:g_projectile_origins)if(!b.child){slot=&b;break;}
    if(!slot){++g_projectile_origin_overflow;pthread_mutex_unlock(&g_projectile_origin_mutex);return;}
    bool valid=source && source!=child && impact_vector(manager,objects,count) &&
        projectile_origin_fact(source,manager,objects,count,source_fact) &&
        safe_field(child,0x10,child_manager) && (!child_manager || child_manager==manager);
    if(valid)for(int i=0;i<count;++i)if(objects[i]==child)valid=false;
    // Even an invalid first admission leaves a tombstone. Another queue call
    // must not retroactively rehabilitate that allocation without a reset.
    *slot={child,source,asset,world,manager,{},valid,false,false};
    if(valid) {
        slot->origin={g_projectile_origin_epoch,++g_projectile_origin_sequence,tick,-1,child_fact,source_fact,true};
        ++g_projectile_origin_captured;
    } else ++g_projectile_origin_rejected;
    pthread_mutex_unlock(&g_projectile_origin_mutex);
}
bool projectile_origin_validate_locked(ProjectileOriginBinding &b,void *world,void *manager,int tick,
                                       bool require_live,ProjectileOrigin &out) {
    void *asset=nullptr,*source=nullptr,*actual_manager=nullptr;void **objects=nullptr;
    int count=0;ImpactFact child,producer;
    bool valid=b.valid && b.world==world && b.manager==manager && b.origin.epoch==g_projectile_origin_epoch &&
        tick>=b.origin.captured_tick && projectile_origin_layout(b.child,asset,child,source) &&
        asset==b.asset && child.gid==b.origin.child.gid && child.owner==b.origin.child.owner &&
        child.card==b.origin.child.card && impact_vector(manager,objects,count) &&
        safe_field(b.child,0x10,actual_manager) && actual_manager==manager;
    if(valid && require_live)valid=projectile_origin_fact(b.child,manager,objects,count,child) &&
        (!b.origin.child.id || child.id==b.origin.child.id) && child.id!=b.origin.source.id;
    if(valid && source)valid=!b.cleared && source==b.source &&
        projectile_origin_fact(source,manager,objects,count,producer) && projectile_origin_same(producer,b.origin.source);
    if(valid && !source)valid=b.cleared; // Only an observed native null replacement proves loss.
    if(!valid){projectile_origin_reject_locked(b);return false;}
    out=b.origin;out.child=child;out.current_source=source!=nullptr;
    return true;
}
void projectile_origin_registered(void *manager,void *child,int commit) {
    if(!g_projectile_origin_ready || commit!=1 || !child)return;
    void *world=nullptr,*actual_manager=nullptr;int tick=-1;
    if(!deployment_context(world,actual_manager,tick) || !world || tick<0 || manager!=actual_manager)return;
    pthread_mutex_lock(&g_projectile_origin_mutex);
    projectile_origin_epoch_locked(world,manager,tick);
    auto *b=projectile_origin_find_locked(child);
    if(b && b->valid && !projectile_origin_mutating_locked(child)) {
        ProjectileOrigin out;
        if(projectile_origin_validate_locked(*b,world,manager,tick,true,out)) {
            if(!b->committed) {out.committed_tick=tick;b->origin=out;b->committed=true;++g_projectile_origin_promoted;}
        }
    }
    pthread_mutex_unlock(&g_projectile_origin_mutex);
}
bool projectile_origin_lookup(void *child,void *world,void *manager,int tick,ProjectileOrigin &out) {
    out=ProjectileOrigin{};
    if(!g_projectile_origin_ready || !child || !world || !manager || tick<0)return false;
    pthread_mutex_lock(&g_projectile_origin_mutex);
    projectile_origin_epoch_locked(world,manager,tick);
    auto *b=projectile_origin_find_locked(child);
    bool valid=b && b->valid && b->committed && !projectile_origin_mutating_locked(child) &&
        projectile_origin_validate_locked(*b,world,manager,tick,true,out);
    pthread_mutex_unlock(&g_projectile_origin_mutex);return valid;
}
struct ProjectileOriginMutationGuard {
    ProjectileOriginMutation scope;
    explicit ProjectileOriginMutationGuard(void *child,bool clear=true) {
        pthread_mutex_lock(&g_projectile_origin_mutex);
        if(clear)if(auto *b=projectile_origin_find_locked(child))*b=ProjectileOriginBinding{};
        scope={child,g_projectile_origin_mutations};g_projectile_origin_mutations=&scope;
        pthread_mutex_unlock(&g_projectile_origin_mutex);
    }
    ~ProjectileOriginMutationGuard() {
        pthread_mutex_lock(&g_projectile_origin_mutex);
        auto **link=&g_projectile_origin_mutations;
        while(*link && *link!=&scope)link=&(*link)->previous;
        if(*link)*link=scope.previous;
        pthread_mutex_unlock(&g_projectile_origin_mutex);
    }
};
using ProjectileOriginCtorFn=uintptr_t(*)(void*,void*);
using ProjectileOriginResetFn=uintptr_t(*)(void*);
using ProjectileOriginDecodeFn=uintptr_t(*)(void*,void*);
using ProjectileOriginReplaceFn=uintptr_t(*)(void*,void*,int,int,void*);
using ProjectileOriginInitFn=uintptr_t(*)(void*,void*,int,int,int,void*,int,int,void*,void*,int,int,int);
ProjectileOriginCtorFn g_projectile_origin_ctor_original=nullptr;
ProjectileOriginResetFn g_projectile_origin_reset_original=nullptr;
ProjectileOriginDecodeFn g_projectile_origin_decode_original=nullptr;
ProjectileOriginReplaceFn g_projectile_origin_replace_original=nullptr;
ProjectileOriginInitFn g_projectile_origin_init_original=nullptr;
uintptr_t projectile_origin_ctor(void *child,void *asset) {
    ProjectileOriginMutationGuard guard(child);return g_projectile_origin_ctor_original(child,asset);
}
uintptr_t projectile_origin_reset(void *child) {
    ProjectileOriginMutationGuard guard(child);return g_projectile_origin_reset_original(child);
}
uintptr_t projectile_origin_decode(void *child,void *decoder) {
    ProjectileOriginMutationGuard guard(child);return g_projectile_origin_decode_original(child,decoder);
}
uintptr_t projectile_origin_init(void *child,void *emitter,int x,int y,int z,void *target,int dx,int dy,
                               void *source,void *scaling,int level,int a,int b) {
    ProjectileOriginMutationGuard guard(child);
    return g_projectile_origin_init_original(child,emitter,x,y,z,target,dx,dy,source,scaling,level,a,b);
}
uintptr_t projectile_origin_replace(void *child,void *removed,int id,int flag,void *replacement) {
    ProjectileOriginMutationGuard guard(child,false);
    void *before=nullptr;uint64_t sequence=0,epoch=0;bool tracking=false,clear=false;
    pthread_mutex_lock(&g_projectile_origin_mutex);
    if(auto *b=projectile_origin_find_locked(child))if(b->valid) {
        sequence=b->origin.sequence;epoch=b->origin.epoch;
        tracking=safe_field(child,0x100,before);
        if(!tracking || (before && before!=b->source))projectile_origin_reject_locked(*b);
        else if(before==removed && before) {
            // A removal notification can arrive after vector removal. Its
            // exact old pointer + old ID proves the clearing route; a source
            // still present in that vector must also retain its full fact.
            void **objects=nullptr;int count=0;bool present=false;
            bool source_ok=impact_vector(b->manager,objects,count);
            if(source_ok)for(int i=0;i<count;++i)if(objects[i]==before)present=true;
            if(source_ok && present) {
                ImpactFact fact;source_ok=projectile_origin_fact(before,b->manager,objects,count,fact) &&
                    projectile_origin_same(fact,b->origin.source);
            }
            if(!source_ok || replacement)projectile_origin_reject_locked(*b);
            else if(uint32_t(id)!=b->origin.source.id)projectile_origin_reject_locked(*b);
            else clear=true;
        }
    }
    pthread_mutex_unlock(&g_projectile_origin_mutex);
    uintptr_t result=g_projectile_origin_replace_original(child,removed,id,flag,replacement);
    pthread_mutex_lock(&g_projectile_origin_mutex);
    if(auto *b=projectile_origin_find_locked(child))if(b->valid && tracking &&
        b->origin.epoch==epoch && b->origin.sequence==sequence) {
        void *after=nullptr;
        if(!safe_field(child,0x100,after) || (clear ? after!=nullptr : after!=before))projectile_origin_reject_locked(*b);
        else if(clear)b->cleared=true;
    }
    pthread_mutex_unlock(&g_projectile_origin_mutex);return result;
}
void append_projectile_origin(char *json,int &offset,size_t size,void *child) {
    void *world=nullptr,*manager=nullptr;int tick=-1;ProjectileOrigin out;
    if(!deployment_context(world,manager,tick) || !projectile_origin_lookup(child,world,manager,tick,out))return;
    char fragment[768];
    int n=snprintf(fragment,sizeof(fragment),
        ",\"projectile_origin\":{\"schema\":\"nulls-projectile-origin.v1\",\"validated\":true,\"epoch\":%llu,\"sequence\":%llu,"
        "\"evidence\":\"projectile_queued_source\",\"captured_tick\":%d,\"committed_tick\":%d,"
        "\"entity_id\":%u,\"child_data_id\":%u,\"owner\":%d,\"child_card_id\":%d,"
        "\"source_id\":%u,\"source_data_id\":%u,\"source_owner\":%d,\"source_card_id\":%d,\"current_source\":%s}",
        (unsigned long long)out.epoch,(unsigned long long)out.sequence,out.captured_tick,out.committed_tick,
        out.child.id,out.child.gid,out.child.owner,out.child.card,out.source.id,out.source.gid,out.source.owner,
        out.source.card,out.current_source?"true":"false");
    if(n>0 && n<int(sizeof(fragment)) && offset>=0 && size_t(offset)<size && size_t(n)<size-size_t(offset)) {
        memcpy(json+offset,fragment,size_t(n)+1);offset+=n;
    }
}
void append_projectile_origin_diagnostics(char *json,int &offset,size_t size,void *world,int tick) {
    void *actual_world=nullptr,*manager=nullptr;int actual_tick=-1;
    if(!deployment_context(actual_world,manager,actual_tick) || actual_world!=world || actual_tick!=tick)return;
    pthread_mutex_lock(&g_projectile_origin_mutex);
    projectile_origin_epoch_locked(world,manager,tick);
    char fragment[512];int n=snprintf(fragment,sizeof(fragment),
        ",\"projectile_origin_runtime\":{\"schema\":\"nulls-projectile-origin.v1\",\"hooks_ready\":%s,\"epoch\":%llu,"
        "\"captured\":%llu,\"promoted\":%llu,\"rejected\":%llu,\"overflow\":%llu}",
        g_projectile_origin_ready?"true":"false",(unsigned long long)g_projectile_origin_epoch,
        (unsigned long long)g_projectile_origin_captured,(unsigned long long)g_projectile_origin_promoted,
        (unsigned long long)g_projectile_origin_rejected,(unsigned long long)g_projectile_origin_overflow);
    pthread_mutex_unlock(&g_projectile_origin_mutex);
    if(n>0 && n<int(sizeof(fragment)) && offset>=0 && size_t(offset)<size && size_t(n)<size-size_t(offset)) {
        memcpy(json+offset,fragment,size_t(n)+1);offset+=n;
    }
}
bool projectile_origin_words(uintptr_t address,const uint32_t *words,size_t count) {
    uint32_t actual[32]={};return count<=32 && safe_bytes(reinterpret_cast<void*>(g_libg_base+address),actual,count*4) &&
        memcmp(actual,words,count*4)==0;
}
bool install_projectile_origin_observers() {
    g_projectile_origin_ready=false;
    const uint32_t ctor[]={0xa9be7bfd,0xf9000bf3,0x910003fd,0xaa0003f3};
    const uint32_t reset[]={0xa9bd7bfd,0xf9000bf5,0xa9024ff4,0x910003fd};
    const uint32_t decode[]={0xa9bd7bfd,0xa90157f6,0xa9024ff4,0x910003fd};
    const uint32_t init[]={0xd10243ff,0xa9037bfd,0xa9046ffc,0xa90567fa};
    const uint32_t replace[]={0xd10243ff,0xa9037bfd,0xa9046ffc,0xa90567fa};
    const uint32_t clear=0xad080260,decode_write=0xf9008260,source_load=0xf94033b6;
    const uint32_t init_write[]={0xf9008276,0xb4000076,0xb9400ac8,0xb900fa68};
    const uint32_t replacement_args[]={0xf9408408,0xaa0403f3,0xaa0003f5,0xaa0103f6,0x2a0203f7,0xb81fc3a3};
    const uint32_t replacement_compare[]={0xf94082a8,0xeb16011f,0x54000240};
    const uint32_t replacement_write[]={0xf90082b3,0xf940c2a8,0xeb16011f,0x54fffde1,0xf900c2bf};
    if(!projectile_layout_attested() || !projectile_origin_words(0xf26d68,ctor,4) ||
        !projectile_origin_words(0xf26e0c,reset,4) || !projectile_origin_words(0xf2764c,decode,4) ||
        !projectile_origin_words(0xf27c94,init,4) || !projectile_origin_words(0xf2c4e0,replace,4) ||
        !projectile_origin_words(0xf26de4,&clear,1) || !projectile_origin_words(0xf26efc,&clear,1) ||
        !projectile_origin_words(0xf276a4,&decode_write,1) || !projectile_origin_words(0xf27cc0,&source_load,1) ||
        !projectile_origin_words(0xf27d3c,init_write,4) || !projectile_origin_words(0xf2c500,replacement_args,6) ||
        !projectile_origin_words(0xf2c9f0,replacement_compare,3) || !projectile_origin_words(0xf2ca40,replacement_write,5))return false;
    bool ready=install_inline_hook(g_libg_base+0xf26d68,reinterpret_cast<const uint8_t*>(ctor),16,
        reinterpret_cast<void*>(&projectile_origin_ctor),reinterpret_cast<void**>(&g_projectile_origin_ctor_original),"projectile origin constructor") &&
        install_inline_hook(g_libg_base+0xf26e0c,reinterpret_cast<const uint8_t*>(reset),16,
        reinterpret_cast<void*>(&projectile_origin_reset),reinterpret_cast<void**>(&g_projectile_origin_reset_original),"projectile origin reset") &&
        install_inline_hook(g_libg_base+0xf2764c,reinterpret_cast<const uint8_t*>(decode),16,
        reinterpret_cast<void*>(&projectile_origin_decode),reinterpret_cast<void**>(&g_projectile_origin_decode_original),"projectile origin decoder") &&
        install_inline_hook(g_libg_base+0xf27c94,reinterpret_cast<const uint8_t*>(init),16,
        reinterpret_cast<void*>(&projectile_origin_init),reinterpret_cast<void**>(&g_projectile_origin_init_original),"projectile origin route init") &&
        install_inline_hook(g_libg_base+0xf2c4e0,reinterpret_cast<const uint8_t*>(replace),16,
        reinterpret_cast<void*>(&projectile_origin_replace),reinterpret_cast<void**>(&g_projectile_origin_replace_original),"projectile origin reference replacement");
    g_projectile_origin_ready=ready;return ready;
}
