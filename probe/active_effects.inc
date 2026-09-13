// Read-only adaptation of FirstLight::read_native_type_three_state.
// Read the complete type-3 active-buff vector or explicitly report unknown.
bool effect_origin_runtime(uint64_t &epoch);
bool append_live_effect_origin(char *json,int &offset,size_t size,void *entry,void *target);
struct ActiveBuffSnapshot {
    uint32_t gid=0;
    int remaining=-2,source_owner=-1;
    char name[129]={};
    LiveReference source;
    void *entry=nullptr;
};

void append_active_effects(char *json,int &offset,size_t size,void *object,void **objects,int object_count) {
    void *component=runtime_component(object,3);
    if (!component) return;
    uintptr_t vt=0,getter=0; uint32_t type_words[2]={};
    bool valid=safe_field(component,0,vt) && vt>=g_libg_base && vt<g_libg_base+0x1a00000 &&
        safe_field(reinterpret_cast<void*>(vt),0x20,getter) && getter>=g_libg_base && getter<g_libg_base+0x1800000 &&
        safe_bytes(reinterpret_cast<void*>(getter),type_words,sizeof(type_words)) &&
        type_words[0]==0x52800060 && type_words[1]==0xd65f03c0; // mov w0,#3; ret
    void **entries=nullptr; int count=0,capacity=0,invisible=-1;
    valid=valid && safe_field(component,0x18,entries) && safe_field(component,0x20,capacity) &&
        safe_field(component,0x24,count) && safe_field(component,0x34,invisible) &&
        count>=0 && capacity>=count && capacity<=64 && invisible>=0 && invisible<=count && (!count || entries);
    ActiveBuffSnapshot effects[64];
    for (int i=0;valid && i<count;++i) {
        void *entry=nullptr,*asset=nullptr,*source=nullptr; uintptr_t asset_vt=0,class_getter=0;
        auto &effect=effects[i];
        valid=safe_field(entries,i*sizeof(void*),entry) && safe_field(entry,8,effect.remaining) && effect.remaining>=-1 &&
            safe_field(entry,0x18,asset) && safe_field(asset,0,asset_vt) && asset_vt==g_libg_base+0x1882500 &&
            safe_field(reinterpret_cast<void*>(asset_vt),0x70,class_getter) && class_getter==g_libg_base+0xd961f4 &&
            safe_field(asset,0x40,effect.gid) && effect.gid && asset_name(asset,effect.name) && safe_field(entry,0x38,source);
        if (valid) {
            effect.entry=entry;
            effect.source=live_reference(source,objects,object_count);
            if (effect.source.known && effect.source.id) {
                int owner=-1;
                if (safe_field(source,0x78,owner) && owner>=0 && owner<=1) effect.source_owner=owner;
            }
        }
    }
    if (!valid) {
        offset+=snprintf(json+offset,size-offset,",\"active_effect_runtime\":{\"schema\":\"nulls-active-effects.v1\",\"validated\":false,\"reason\":\"layout_or_entry_invalid\"}");
        return;
    }
    // Build separately so a full 64-entry vector cannot consume the JSON tail
    // or silently become a partial list. Leave space for other entity fields.
    char fragment[65536];
    uint64_t origin_epoch=0;bool origin_ready=effect_origin_runtime(origin_epoch);
    int used=snprintf(fragment,sizeof(fragment),",\"active_effect_runtime\":{\"schema\":\"nulls-active-effects.v2\",\"validated\":true,\"origin_hooks_ready\":%s,\"origin_epoch\":%llu,\"count\":%d,\"invisible_count\":%d,\"effects\":[",
        origin_ready?"true":"false",(unsigned long long)origin_epoch,count,invisible);
    for (int i=0;i<count;++i) {
        const auto &e=effects[i];
        int n=snprintf(fragment+used,sizeof(fragment)-used,
            "%s{\"buff_global_id\":%u,\"name\":\"%s\",\"remaining_ms\":%d,\"source_known\":%s,\"source_id\":%u,\"source_owner\":%d",
            i?",":"",e.gid,e.name,e.remaining,e.source.known?"true":"false",e.source.id,e.source_owner);
        if (n<0 || n>=int(sizeof(fragment))-used) { valid=false; break; }
        used+=n;
        if (!append_live_effect_origin(fragment,used,sizeof(fragment),e.entry,object)) { valid=false;break; }
        used+=snprintf(fragment+used,sizeof(fragment)-used,"}");
    }
    if (!valid || size_t(used)+8192>size-size_t(offset)) {
        offset+=snprintf(json+offset,size-offset,",\"active_effect_runtime\":{\"schema\":\"nulls-active-effects.v1\",\"validated\":false,\"reason\":\"snapshot_capacity\"}");
        return;
    }
    used+=snprintf(fragment+used,sizeof(fragment)-used,"]}");
    memcpy(json+offset,fragment,used+1); offset+=used;
}
