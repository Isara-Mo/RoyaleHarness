// Remember the exact source attached to a native BUFF instance while it is
// alive. The engine clears +0x38 when that source is removed; it does not turn
// a later, unrelated object at the same address into this effect's producer.
struct EffectOrigin {
    uint64_t epoch=0,sequence=0;
    int captured_tick=-1;
    uint32_t buff_gid=0;
    ImpactFact target,source;
    bool current_source=false;
};
struct EffectOriginBinding {
    bool valid=false;
    void *entry=nullptr,*component=nullptr,*asset=nullptr,*target=nullptr,*source=nullptr;
    void *world=nullptr,*manager=nullptr;
    EffectOrigin origin;
};
constexpr int kEffectOriginCapacity=4096;
EffectOriginBinding g_effect_origins[kEffectOriginCapacity];
bool g_effect_origin_ready=false;
uint64_t g_effect_origin_epoch=0,g_effect_origin_sequence=0;
uint64_t g_effect_origin_captured=0,g_effect_origin_rejected=0,g_effect_origin_overflow=0;
void *g_effect_origin_world=nullptr;
int g_effect_origin_tick=-1;
pthread_mutex_t g_effect_origin_mutex=PTHREAD_MUTEX_INITIALIZER;
struct EffectOriginMutationScope {
    void *entry=nullptr;
    EffectOriginMutationScope *previous=nullptr;
};
EffectOriginMutationScope *g_effect_origin_mutations=nullptr;

bool effect_origin_mutating_locked(void *entry) {
    for(auto *scope=g_effect_origin_mutations;scope;scope=scope->previous)
        if(scope->entry==entry)return true;
    return false;
}

void effect_origin_epoch_locked(void *world,int tick) {
    if(world!=g_effect_origin_world || tick<g_effect_origin_tick) {
        for(auto &binding:g_effect_origins) binding=EffectOriginBinding{};
        ++g_effect_origin_epoch;g_effect_origin_sequence=0;
        g_effect_origin_captured=g_effect_origin_rejected=g_effect_origin_overflow=0;
    }
    g_effect_origin_world=world;g_effect_origin_tick=tick;
}
void effect_origin_epoch(void *world,int tick) {
    if(!world || tick<0)return;
    pthread_mutex_lock(&g_effect_origin_mutex);
    effect_origin_epoch_locked(world,tick);
    pthread_mutex_unlock(&g_effect_origin_mutex);
}
bool effect_origin_same(const ImpactFact &a,const ImpactFact &b) {
    return a.id==b.id && a.gid==b.gid && a.owner==b.owner && a.card==b.card;
}
bool effect_origin_layout(void *entry,void *component,void *target,
                          void *&asset,uint32_t &gid,void *&source) {
    void *backlink=nullptr,*host=nullptr,**entries=nullptr;
    uintptr_t component_vtable=0,asset_vtable=0;
    int count=0,capacity=0;
    if(!entry || !component || !target || runtime_component(target,3)!=component ||
        !safe_field(component,0,component_vtable) || component_vtable!=g_libg_base+0x18a1d58 ||
        !safe_field(entry,0x20,backlink) || backlink!=component ||
        !safe_field(component,8,host) || host!=target ||
        !safe_field(component,0x18,entries) || !safe_field(component,0x20,capacity) ||
        !safe_field(component,0x24,count) || !entries || count<1 || count>64 ||
        capacity<count || capacity>64 || !safe_field(entry,0x18,asset) ||
        !safe_field(asset,0,asset_vtable) || asset_vtable!=g_libg_base+0x1882500 ||
        !safe_field(asset,0x40,gid) || !gid || !safe_field(entry,0x38,source))return false;
    int matches=0;
    for(int i=0;i<count;++i) {
        void *member=nullptr;
        if(!safe_field(entries,size_t(i)*sizeof(void*),member))return false;
        if(member==entry)++matches;
    }
    return matches==1;
}
void effect_origin_invalidate(void *entry) {
    if(!entry)return;
    pthread_mutex_lock(&g_effect_origin_mutex);
    // Called before constructors, setters, decoders and reset code. Clearing
    // the pointer permits a subsequent native add or a fully validated first
    // observation to establish the new instance's source.
    for(auto &binding:g_effect_origins)
        if(binding.entry==entry)binding=EffectOriginBinding{};
    pthread_mutex_unlock(&g_effect_origin_mutex);
}
struct EffectOriginMutationGuard {
    EffectOriginMutationScope scope;
    explicit EffectOriginMutationGuard(void *entry) {
        pthread_mutex_lock(&g_effect_origin_mutex);
        for(auto &binding:g_effect_origins)
            if(binding.entry==entry)binding=EffectOriginBinding{};
        scope={entry,g_effect_origin_mutations};g_effect_origin_mutations=&scope;
        pthread_mutex_unlock(&g_effect_origin_mutex);
    }
    ~EffectOriginMutationGuard() {
        pthread_mutex_lock(&g_effect_origin_mutex);
        // Other threads can finish their own nested scopes in another order.
        auto **link=&g_effect_origin_mutations;
        while(*link && *link!=&scope)link=&(*link)->previous;
        if(*link)*link=scope.previous;
        pthread_mutex_unlock(&g_effect_origin_mutex);
    }
};
void effect_origin_added(void *component,void *entry) {
    if(!g_effect_origin_ready)return;
    void *world=nullptr,*manager=nullptr,*target=nullptr,*asset=nullptr,*source=nullptr;
    void **objects=nullptr;int tick=-1,count=0;uint32_t gid=0;
    ImpactFact host,producer;
    if(!deployment_context(world,manager,tick) || !world || tick<0)return;
    pthread_mutex_lock(&g_effect_origin_mutex);
    effect_origin_epoch_locked(world,tick);
    if(effect_origin_mutating_locked(entry)) {
        pthread_mutex_unlock(&g_effect_origin_mutex);return;
    }
    bool valid=safe_field(component,8,target) &&
        effect_origin_layout(entry,component,target,asset,gid,source) &&
        impact_vector(manager,objects,count) &&
        impact_fact(target,manager,objects,count,host) &&
        impact_fact(source,manager,objects,count,producer);
    EffectOriginBinding *slot=nullptr;
    for(auto &binding:g_effect_origins) {
        if(binding.entry==entry && entry) {
            // An unexpected repeated add cannot replace a live binding, nor
            // rehabilitate one invalidated by a query. A lifecycle hook must
            // first establish that this address belongs to another instance.
            bool same=valid && binding.valid && binding.world==world && binding.manager==manager &&
                binding.component==component && binding.asset==asset && binding.target==target &&
                binding.source==source && binding.origin.buff_gid==gid &&
                effect_origin_same(binding.origin.target,host) && effect_origin_same(binding.origin.source,producer);
            if(!same) {binding.valid=false;++g_effect_origin_rejected;}
            pthread_mutex_unlock(&g_effect_origin_mutex);return;
        }
        // A rejected binding is a tombstone, not a free slot. Losing it to an
        // unrelated entry would let a later query rehabilitate this instance.
        if(!slot && !binding.entry)slot=&binding;
    }
    if(!valid)++g_effect_origin_rejected;
    else if(!slot)++g_effect_origin_overflow;
    else {
        EffectOrigin origin{g_effect_origin_epoch,++g_effect_origin_sequence,tick,gid,host,producer,true};
        *slot={true,entry,component,asset,target,source,world,manager,origin};
        ++g_effect_origin_captured;
    }
    pthread_mutex_unlock(&g_effect_origin_mutex);
}
bool effect_origin_lookup(void *entry,void *expected_target,void *world,void *manager,int tick,EffectOrigin &out) {
    out=EffectOrigin{};
    if(!g_effect_origin_ready || !entry || !world || tick<0)return false;
    pthread_mutex_lock(&g_effect_origin_mutex);
    effect_origin_epoch_locked(world,tick);
    if(effect_origin_mutating_locked(entry)) {
        pthread_mutex_unlock(&g_effect_origin_mutex);return false;
    }
    bool found=false;
    EffectOriginBinding *available=nullptr;
    for(auto &binding:g_effect_origins) {
        if(!available && !binding.entry)available=&binding;
        if(binding.entry!=entry)continue;
        if(!binding.valid) {
            pthread_mutex_unlock(&g_effect_origin_mutex);return false;
        }
        void *asset=nullptr,*source=nullptr;uint32_t gid=0;
        void **objects=nullptr;int count=0;ImpactFact target,producer;
        bool valid=binding.origin.epoch==g_effect_origin_epoch && binding.world==world &&
            binding.manager==manager && binding.target==expected_target && tick>=binding.origin.captured_tick &&
            effect_origin_layout(entry,binding.component,expected_target,asset,gid,source) &&
            asset==binding.asset && gid==binding.origin.buff_gid && impact_vector(manager,objects,count) &&
            impact_fact(expected_target,manager,objects,count,target) && effect_origin_same(target,binding.origin.target);
        if(valid && source)
            valid=source==binding.source && impact_fact(source,manager,objects,count,producer) &&
                effect_origin_same(producer,binding.origin.source);
        if(valid) {out=binding.origin;out.current_source=source!=nullptr;found=true;}
        else {binding.valid=false;++g_effect_origin_rejected;}
        pthread_mutex_unlock(&g_effect_origin_mutex);return found;
    }
    // Some effects are attached while their immediate AEO is still waiting
    // for manager admission. Capture the first later *observed* live binding,
    // never an estimated creation time and never an old tombstone. Absence of
    // a live source remains unknown and creates no historical attribution.
    void *actual_world=nullptr,*actual_manager=nullptr,*component=nullptr,*asset=nullptr,*source=nullptr;
    int actual_tick=-1,count=0;uint32_t gid=0;void **objects=nullptr;
    ImpactFact target,producer;
    bool valid=deployment_context(actual_world,actual_manager,actual_tick) &&
        actual_world==world && actual_manager==manager && actual_tick==tick &&
        safe_field(entry,0x20,component) &&
        effect_origin_layout(entry,component,expected_target,asset,gid,source) &&
        impact_vector(manager,objects,count) &&
        impact_fact(expected_target,manager,objects,count,target) &&
        impact_fact(source,manager,objects,count,producer);
    if(valid && available) {
        out={g_effect_origin_epoch,++g_effect_origin_sequence,tick,gid,target,producer,true};
        *available={true,entry,component,asset,expected_target,source,world,manager,out};
        ++g_effect_origin_captured;found=true;
    } else if(valid)++g_effect_origin_overflow;
    pthread_mutex_unlock(&g_effect_origin_mutex);
    return found;
}
void append_effect_origin(char *json,int &offset,size_t size,const EffectOrigin &origin) {
    if(!json || offset<0 || size_t(offset)>=size)return;
    char fragment[768];
    int written=snprintf(fragment,sizeof(fragment),
        "{\"schema\":\"nulls-effect-origin.v1\",\"validated\":true,\"epoch\":%llu,\"sequence\":%llu,"
        "\"captured_tick\":%d,\"buff_global_id\":%u,\"target_id\":%u,\"target_data_id\":%u,\"target_owner\":%d,"
        "\"source_id\":%u,\"source_data_id\":%u,\"source_owner\":%d,\"source_card_id\":%d,\"current_source\":%s}",
        (unsigned long long)origin.epoch,(unsigned long long)origin.sequence,origin.captured_tick,origin.buff_gid,
        origin.target.id,origin.target.gid,origin.target.owner,origin.source.id,origin.source.gid,
        origin.source.owner,origin.source.card,origin.current_source?"true":"false");
    if(written<0 || written>=int(sizeof(fragment)) || size_t(written)>=size-size_t(offset)) {
        if(size-size_t(offset)>4) {memcpy(json+offset,"null",5);offset+=4;}
        return;
    }
    memcpy(json+offset,fragment,size_t(written)+1);offset+=written;
}

// These methods do not return a consumed value at their audited call sites.
// Preserve x0 nevertheless, so the observer also preserves the native return
// register rather than assuming an undocumented C++ return declaration.
using EffectOriginCtorFn=uintptr_t(*)(void*,void*,void*);
using EffectOriginResetFn=uintptr_t(*)(void*);
using EffectOriginDecodeFn=uintptr_t(*)(void*,void*);
using EffectOriginSetterFn=uintptr_t(*)(void*,void*,int);
EffectOriginCtorFn g_effect_origin_ctor_original=nullptr;
EffectOriginResetFn g_effect_origin_reset_original=nullptr;
EffectOriginDecodeFn g_effect_origin_decode_original=nullptr;
EffectOriginSetterFn g_effect_origin_setter_original=nullptr;
uintptr_t effect_origin_ctor(void *entry,void *context,void *component) {
    EffectOriginMutationGuard mutation(entry);return g_effect_origin_ctor_original(entry,context,component);
}
uintptr_t effect_origin_reset(void *entry) {
    EffectOriginMutationGuard mutation(entry);return g_effect_origin_reset_original(entry);
}
uintptr_t effect_origin_decode(void *entry,void *decoder) {
    EffectOriginMutationGuard mutation(entry);return g_effect_origin_decode_original(entry,decoder);
}
uintptr_t effect_origin_setter(void *entry,void *source,int metadata) {
    EffectOriginMutationGuard mutation(entry);return g_effect_origin_setter_original(entry,source,metadata);
}
bool effect_origin_words(uintptr_t offset,const uint32_t *expected,size_t count) {
    uint32_t actual[32]={};
    return count<=32 && safe_bytes(reinterpret_cast<void*>(g_libg_base+offset),actual,count*4) &&
        memcmp(actual,expected,count*4)==0;
}
bool install_effect_origin_observers() {
    g_effect_origin_ready=false;
    const uint32_t ctor[]={0x9e6703e0,0x2f00e5e1,0x12800008,0xa9007c01};
    const uint32_t reset[]={0x2f00e5e1,0x9e6703e0,0x12800008,0x6f00e402};
    const uint32_t decode[]={0xa9bd7bfd,0xf9000bf5,0xa9024ff4,0x910003fd};
    const uint32_t setter[]={0xa9be7bfd,0xa9014ff4,0x910003fd,0xaa0003f3};
    const uint32_t clear[]={0xf9401c08,0xeb01011f,0x54000040,0xd65f03c0,0xf9001c1f,0xd65f03c0};
    const uint32_t refresh[]={0xa9be7bfd,0xf9000bf3,0x910003fd,0xb9400809,0xaa0003f3,0x6b090028,
        0x540000cd,0x3100053f,0x54000080,0xb9400e69,0x0b090108,0x29012261,0xf9400e68,0x92407c40,
        0xf940a501,0x94007aa7,0xb9402a68,0x53081d08,0x6b00611f,0x5400004a,0xb9002a60,0xf9400bf3,
        0xa8c27bfd,0xd65f03c0};
    const uint32_t ctor_clear=0x3d800c00,reset_clear=0x3d800c00,decode_write=0xf9001e60,setter_write=0xf9001c01;
    if(!effect_origin_words(0xf2023c,ctor,4) || !effect_origin_words(0xf20418,reset,4) ||
        !effect_origin_words(0xf205d8,decode,4) || !effect_origin_words(0xf20ec4,setter,4) ||
        !effect_origin_words(0xf208c4,clear,6) || !effect_origin_words(0xf20ff8,refresh,24) ||
        !effect_origin_words(0xf20264,&ctor_clear,1) || !effect_origin_words(0xf20454,&reset_clear,1) ||
        !effect_origin_words(0xf206a4,&decode_write,1) || !effect_origin_words(0xf20ed4,&setter_write,1))return false;
    bool ready=install_inline_hook(g_libg_base+0xf2023c,reinterpret_cast<const uint8_t*>(ctor),16,
        reinterpret_cast<void*>(&effect_origin_ctor),reinterpret_cast<void**>(&g_effect_origin_ctor_original),"effect origin constructor") &&
        install_inline_hook(g_libg_base+0xf20418,reinterpret_cast<const uint8_t*>(reset),16,
        reinterpret_cast<void*>(&effect_origin_reset),reinterpret_cast<void**>(&g_effect_origin_reset_original),"effect origin reset") &&
        install_inline_hook(g_libg_base+0xf205d8,reinterpret_cast<const uint8_t*>(decode),16,
        reinterpret_cast<void*>(&effect_origin_decode),reinterpret_cast<void**>(&g_effect_origin_decode_original),"effect origin decode") &&
        install_inline_hook(g_libg_base+0xf20ec4,reinterpret_cast<const uint8_t*>(setter),16,
        reinterpret_cast<void*>(&effect_origin_setter),reinterpret_cast<void**>(&g_effect_origin_setter_original),"effect origin source setter");
    g_effect_origin_ready=ready;return ready;
}
