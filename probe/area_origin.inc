// Exact AEO initializer parent, retained through manager admission and later
// parent removal. This is an additional creation edge, not a HEAL source or a
// deployment grouping rule. Unknown/private parent pointers stay unknown.
struct AreaOriginBinding {
    void *child=nullptr,*asset=nullptr,*parent=nullptr,*parent_asset=nullptr;
    void *world=nullptr,*manager=nullptr;
    ImpactFact source,entity;
    uint64_t epoch=0,sequence=0;
    uint32_t child_gid=0;
    int created_tick=-1,captured_tick=-1,committed_tick=-1;
    bool initializing=false,captured=false,queued=false,committed=false,invalid=false;
};
constexpr int kAreaOriginCapacity=4096;
AreaOriginBinding g_area_origins[kAreaOriginCapacity];
pthread_mutex_t g_area_origin_mutex=PTHREAD_MUTEX_INITIALIZER;
void *g_area_origin_world=nullptr;
int g_area_origin_tick=-1;
uint64_t g_area_origin_epoch=0,g_area_origin_sequence=0,g_area_origin_calls=0;
uint64_t g_area_origin_captured=0,g_area_origin_queued=0,g_area_origin_promoted=0;
uint64_t g_area_origin_rejected=0,g_area_origin_unknown_parent=0,g_area_origin_overflow=0;
bool g_area_origin_ready=false;
using AreaInitializerFn=uintptr_t(*)(void*,int,void*);
AreaInitializerFn g_area_initializer_original=nullptr;

void area_origin_epoch_locked(void *world,int tick) {
    if(world!=g_area_origin_world || tick<g_area_origin_tick) {
        for(auto &entry:g_area_origins)entry=AreaOriginBinding{};
        ++g_area_origin_epoch;g_area_origin_sequence=g_area_origin_calls=0;
        g_area_origin_captured=g_area_origin_queued=g_area_origin_promoted=0;
        g_area_origin_rejected=g_area_origin_unknown_parent=g_area_origin_overflow=0;
    }
    g_area_origin_world=world;g_area_origin_tick=tick;
}
void area_origin_epoch(void *world,int tick) {
    if(!world || tick<0)return;
    pthread_mutex_lock(&g_area_origin_mutex);area_origin_epoch_locked(world,tick);
    pthread_mutex_unlock(&g_area_origin_mutex);
}
bool area_origin_same(const ImpactFact &left,const ImpactFact &right) {
    return left.id==right.id && left.gid==right.gid && left.owner==right.owner && left.card==right.card;
}
bool area_origin_asset(void *child,void *&asset,uint32_t &gid) {
    uintptr_t vt=0;
    return child && safe_field(child,0,vt) && vt==g_libg_base+0x189c2e8 &&
        safe_field(child,0x48,asset) && asset && safe_field(asset,0x40,gid) && gid;
}
// A missing item is proved against a readable complete vector. A failed
// vector read is never treated as an absent parent or a fresh child.
bool area_origin_membership(void *manager,size_t offset,void *object,int &matches) {
    void **members=nullptr;int count=0,capacity=0;
    matches=0;
    if(!manager || !object || !safe_field(manager,offset,members) ||
        !safe_field(manager,offset+8,capacity) || !safe_field(manager,offset+12,count) ||
        count<0 || count>4096 || capacity<count || capacity>4096 || (count && !members))return false;
    for(int i=0;i<count;++i) {
        void *member=nullptr;
        if(!safe_field(members,size_t(i)*sizeof(void*),member))return false;
        if(member==object)++matches;
    }
    return true;
}
bool area_origin_registered_fact(void *child,void *manager,bool committed,ImpactFact &fact) {
    int matches=0;void *actual_manager=nullptr,*asset=nullptr;
    bool valid=area_origin_membership(manager,committed?8:0x28,child,matches) && matches==1 &&
        safe_field(child,0x10,actual_manager) && actual_manager==manager &&
        safe_field(child,8,fact.id) && fact.id && area_origin_asset(child,asset,fact.gid) &&
        safe_field(child,0x78,fact.owner) && fact.owner>=0 && fact.owner<=1 &&
        safe_field(child,0xac,fact.card);
    if(!valid)return false;
    void **members=nullptr;int count=0,id_matches=0;size_t offset=committed?8:0x28;
    if(!safe_field(manager,offset,members) || !safe_field(manager,offset+12,count))return false;
    for(int i=0;i<count;++i) {
        void *member=nullptr;uint32_t id=0;
        if(!safe_field(members,size_t(i)*sizeof(void*),member) ||
            (member && !safe_field(member,8,id)))return false;
        if(id==fact.id)++id_matches;
    }
    return id_matches==1;
}
void area_origin_reject_locked(AreaOriginBinding &entry) {
    if(!entry.invalid)++g_area_origin_rejected;
    entry.invalid=true;
}
// Inspect the native slot only while the exact child lifetime is current.
// Null slots retain past facts without dereferencing the saved parent address.
bool area_origin_parent(const AreaOriginBinding &entry,bool &current) {
    current=false;void *slot=nullptr;
    if(!safe_field(entry.child,0x130,slot))return false;
    if(slot && slot!=entry.parent)return false;
    // A recycled ID elsewhere in the current manager is a conflict even if
    // +130 was cleared. Reusing the old *address* with another ID after a null
    // slot does not change the saved history and is deliberately not followed.
    void **members=nullptr;int count=0,id_matches=0;ImpactFact source;void *asset=nullptr;
    if(!impact_vector(entry.manager,members,count))return false;
    for(int i=0;i<count;++i) {
        uint32_t id=0;
        if(!members[i])continue;
        if(!safe_field(members[i],8,id))return false;
        if(id!=entry.source.id)continue;
        ++id_matches;
        if(members[i]!=entry.parent || !impact_fact(members[i],entry.manager,members,count,source) ||
            !area_origin_same(source,entry.source) || !safe_field(members[i],0x48,asset) ||
            asset!=entry.parent_asset)return false;
    }
    if(id_matches>1)return false;
    if(!slot)return true;
    int matches=0;
    if(!area_origin_membership(entry.manager,8,slot,matches))return false;
    if(matches==0)return true;
    if(matches!=1 || id_matches!=1 ||
        !impact_fact(slot,entry.manager,members,count,source) ||
        !area_origin_same(source,entry.source) || !safe_field(slot,0x48,asset) || asset!=entry.parent_asset)return false;
    current=true;return true;
}

uintptr_t area_origin_initializer(void *child,int level,void *parent) {
    AreaOriginBinding observed;void **members=nullptr;int count=0,matches=0;
    bool context=g_area_origin_ready && deployment_context(observed.world,observed.manager,observed.created_tick) &&
        observed.world && observed.manager && observed.created_tick>=0;
    if(context) {
        pthread_mutex_lock(&g_area_origin_mutex);
        area_origin_epoch_locked(observed.world,observed.created_tick);++g_area_origin_calls;
        observed.child=child;observed.parent=parent;observed.epoch=g_area_origin_epoch;
        observed.sequence=++g_area_origin_sequence;observed.initializing=true;
        bool child_valid=area_origin_asset(child,observed.asset,observed.child_gid) &&
            area_origin_membership(observed.manager,8,child,matches) && matches==0 &&
            area_origin_membership(observed.manager,0x28,child,matches) && matches==0;
        bool source_valid=child_valid && parent && child!=parent &&
            impact_vector(observed.manager,members,count) &&
            impact_fact(parent,observed.manager,members,count,observed.source) &&
            safe_field(parent,0x48,observed.parent_asset) && observed.parent_asset;
        observed.captured=source_valid;observed.invalid=!child_valid;
        if(source_valid)observed.captured_tick=observed.created_tick;
        else if(child_valid)++g_area_origin_unknown_parent;
        else ++g_area_origin_rejected;
        AreaOriginBinding *slot=nullptr;
        for(auto &entry:g_area_origins) {
            if(entry.child==child && child) {slot=&entry;break;}
            if(!slot && !entry.child)slot=&entry;
        }
        // A newly observed initializer establishes a new lifetime at this
        // address. No read/registration can silently rehabilitate a tombstone.
        if(slot && child) {
            if(slot->initializing) {observed.invalid=true;observed.captured=false;++g_area_origin_rejected;}
            *slot=observed;
        } else {++g_area_origin_overflow;context=false;}
        pthread_mutex_unlock(&g_area_origin_mutex);
    }
    // f16204's attested body consumes x0, w1 and x2. Its last native x0 is
    // the integer result of d7acc8; the wrapper preserves all 64 return bits.
    // The original executes exactly once, including all rejected observations.
    uintptr_t result=g_area_initializer_original(child,level,parent);
    if(!context)return result;
    void *after_world=nullptr,*after_manager=nullptr,*after_asset=nullptr,*slot_parent=nullptr;
    int after_tick=-1;uint32_t after_gid=0;
    bool after_context=deployment_context(after_world,after_manager,after_tick) &&
        after_world==observed.world && after_manager==observed.manager && after_tick==observed.created_tick;
    pthread_mutex_lock(&g_area_origin_mutex);
    for(auto &entry:g_area_origins) {
        if(entry.child!=child || entry.epoch!=observed.epoch || entry.sequence!=observed.sequence)continue;
        entry.initializing=false;
        bool current=false;
        bool valid=after_context && entry.epoch==g_area_origin_epoch && !entry.invalid &&
            area_origin_asset(child,after_asset,after_gid) && after_asset==entry.asset && after_gid==entry.child_gid &&
            safe_field(child,0x130,slot_parent) && slot_parent==parent;
        if(entry.captured)valid=valid && area_origin_parent(entry,current) && current;
        if(!valid)area_origin_reject_locked(entry);
        else if(entry.captured)++g_area_origin_captured;
        break;
    }
    pthread_mutex_unlock(&g_area_origin_mutex);
    return result;
}

void area_origin_registered(void *manager,void *child,int commit) {
    if(!g_area_origin_ready || (commit!=0 && commit!=1))return;
    void *world=nullptr,*current_manager=nullptr;int tick=-1;
    if(!deployment_context(world,current_manager,tick) || current_manager!=manager || !world || tick<0)return;
    pthread_mutex_lock(&g_area_origin_mutex);area_origin_epoch_locked(world,tick);
    for(auto &entry:g_area_origins) {
        if(entry.child!=child || entry.invalid || !entry.captured || entry.initializing)continue;
        ImpactFact entity;void *asset=nullptr;uint32_t gid=0;bool current=false;
        bool valid=entry.epoch==g_area_origin_epoch && entry.world==world && entry.manager==manager &&
            tick>=entry.created_tick && (entry.queued || entry.committed || tick==entry.created_tick) &&
            area_origin_registered_fact(child,manager,commit!=0,entity) &&
            area_origin_asset(child,asset,gid) && asset==entry.asset && gid==entry.child_gid &&
            entity.id!=entry.source.id && entity.owner==entry.source.owner && area_origin_parent(entry,current);
        if(entry.entity.id)valid=valid && area_origin_same(entity,entry.entity);
        if(!valid)area_origin_reject_locked(entry);
        else {
            entry.entity=entity;
            if(commit && !entry.committed) {
                entry.committed=true;entry.committed_tick=tick;++g_area_origin_promoted;
            } else if(!commit && !entry.queued) {entry.queued=true;++g_area_origin_queued;}
        }
        break;
    }
    pthread_mutex_unlock(&g_area_origin_mutex);
}
bool area_origin_lookup(void *child,AreaOriginBinding &out,bool &current_parent) {
    out=AreaOriginBinding{};current_parent=false;
    if(!g_area_origin_ready || !child)return false;
    void *world=nullptr,*manager=nullptr;int tick=-1;
    if(!deployment_context(world,manager,tick) || !world || tick<0)return false;
    pthread_mutex_lock(&g_area_origin_mutex);area_origin_epoch_locked(world,tick);
    bool found=false;
    for(auto &entry:g_area_origins) {
        if(entry.child!=child)continue;
        if(!entry.invalid && entry.captured && entry.committed && !entry.initializing) {
            ImpactFact entity;void *asset=nullptr;uint32_t gid=0;
            bool valid=entry.epoch==g_area_origin_epoch && entry.world==world && entry.manager==manager &&
                tick>=entry.committed_tick && area_origin_registered_fact(child,manager,true,entity) &&
                area_origin_same(entity,entry.entity) && area_origin_asset(child,asset,gid) &&
                asset==entry.asset && gid==entry.child_gid && area_origin_parent(entry,current_parent);
            if(valid) {out=entry;found=true;}
            else area_origin_reject_locked(entry);
        }
        break;
    }
    pthread_mutex_unlock(&g_area_origin_mutex);return found;
}
void append_area_origin(char *json,int &offset,size_t size,void *child) {
    if(!json || offset<0 || size_t(offset)>=size)return;
    AreaOriginBinding entry;bool current=false;
    if(!area_origin_lookup(child,entry,current))return;
    char fragment[768];
    int length=snprintf(fragment,sizeof(fragment),
        ",\"area_origin\":{\"schema\":\"nulls-area-origin.v1\",\"validated\":true,\"evidence\":\"area_initializer\","
        "\"epoch\":%llu,\"sequence\":%llu,\"entity_id\":%u,\"owner\":%d,\"child_data_id\":%u,"
        "\"parent_id\":%u,\"parent_owner\":%d,\"parent_card_id\":%d,\"parent_data_id\":%u,"
        "\"created_tick\":%d,\"captured_tick\":%d,\"committed_tick\":%d,\"current_parent\":%s}",
        (unsigned long long)entry.epoch,(unsigned long long)entry.sequence,entry.entity.id,entry.entity.owner,entry.child_gid,
        entry.source.id,entry.source.owner,entry.source.card,entry.source.gid,
        entry.created_tick,entry.captured_tick,entry.committed_tick,current?"true":"false");
    if(length<0 || length>=int(sizeof(fragment)) || size_t(length)>=size-size_t(offset)) {
        pthread_mutex_lock(&g_area_origin_mutex);++g_area_origin_overflow;pthread_mutex_unlock(&g_area_origin_mutex);return;
    }
    memcpy(json+offset,fragment,size_t(length)+1);offset+=length;
}
void append_area_origin_diagnostics(char *json,int &offset,size_t size) {
    if(!json || offset<0 || size_t(offset)>=size)return;
    void *world=nullptr,*manager=nullptr;int tick=-1;
    if(deployment_context(world,manager,tick))area_origin_epoch(world,tick);
    char fragment[512];pthread_mutex_lock(&g_area_origin_mutex);
    int length=snprintf(fragment,sizeof(fragment),
        ",\"area_origin_runtime\":{\"schema\":\"nulls-area-origin.v1\",\"hooks_ready\":%s,\"epoch\":%llu,"
        "\"calls\":%llu,\"captured\":%llu,\"queued\":%llu,\"promoted\":%llu,\"rejected\":%llu,\"unknown_parent\":%llu,\"overflow\":%llu}",
        g_area_origin_ready?"true":"false",(unsigned long long)g_area_origin_epoch,
        (unsigned long long)g_area_origin_calls,(unsigned long long)g_area_origin_captured,
        (unsigned long long)g_area_origin_queued,(unsigned long long)g_area_origin_promoted,
        (unsigned long long)g_area_origin_rejected,(unsigned long long)g_area_origin_unknown_parent,
        (unsigned long long)g_area_origin_overflow);
    pthread_mutex_unlock(&g_area_origin_mutex);
    if(length>=0 && length<int(sizeof(fragment)) && size_t(length)<size-size_t(offset)) {
        memcpy(json+offset,fragment,size_t(length)+1);offset+=length;
    }
}
bool install_area_origin_observers() {
    g_area_origin_ready=false;
    // Entire initializer: exact ABI, parent write, and integer return path.
    // Only the first four stack-save instructions are displaced by the hook;
    // they contain no PC-relative addressing or branches.
    const uint32_t body[]={0xa9bd7bfd,0xf9000bf5,0xa9024ff4,0x910003fd,0xf9402415,0xaa0003f4,
        0x92407c20,0xaa0203f3,0xf940daa8,0xaa0803e1,0x9400a629,0xb900fe80,0x2a0003e1,
        0xaa1503e0,0x97f992a3,0xb9010280,0xb40000b3,0xb941eaa8,0x7100051f,0x54000041,
        0xf9009e93,0xf9009a93,0xa9424ff4,0xf9400bf5,0xa8c37bfd,0xd65f03c0};
    uint32_t actual[26]={};
    if(!safe_bytes(reinterpret_cast<void*>(g_libg_base+0xf16204),actual,sizeof(actual)) ||
        memcmp(actual,body,sizeof(actual)))return false;
    return install_inline_hook(g_libg_base+0xf16204,reinterpret_cast<const uint8_t*>(body),16,
        reinterpret_cast<void*>(area_origin_initializer),reinterpret_cast<void**>(&g_area_initializer_original),
        "exact AEO initializer parent");
}
