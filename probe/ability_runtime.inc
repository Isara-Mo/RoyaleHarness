// Read-only champion/hero controllers; adapted from FirstLight's
// read_champion_controller. See FIRSTLIGHT_NOTICE.md and FIRSTLIGHT_LICENSE.
struct AbilitySnapshot {
    bool known=false,empty=false;
    int state=-1,cooldown=0,total=0,charges=0,maximum=0,action_id=0,count=0;
    char name[129]={};
    uint32_t members[32]={};
};
AbilitySnapshot read_ability_snapshot(void *player,int slot,int owner) {
    AbilitySnapshot out;
    void *controller=nullptr,*backlink=nullptr,*character=nullptr,*vector=nullptr,*items=nullptr;
    uintptr_t vtable=0;
    if (!safe_field(player,0x3a0+slot*sizeof(void*),controller)) return out;
    if (!controller) { out.known=true; out.empty=true; return out; }
    if (!safe_field(controller,0,vtable) || vtable!=g_libg_base+0x189e4e8 ||
        !safe_field(controller,0x20,backlink) || backlink!=player ||
        !safe_field(controller,0x90,character) || !safe_field(controller,0x98,out.state) ||
        !safe_field(controller,0xa0,vector) || !runtime_vector(vector,0,32,items,out.count)) return out;
    if (!character) {
        out.empty=out.state==0 && out.count==0; out.known=out.empty; return out;
    }
    void *action=nullptr,*ability=nullptr;
    if (!safe_field(controller,0x18,action) || !safe_field(action,0,vtable) || vtable!=g_libg_base+0x188cb28 ||
        !safe_field(action,0x40,out.action_id) || out.action_id<=0 ||
        !safe_field(character,0x710,ability) || !safe_field(ability,0,vtable) || vtable!=g_libg_base+0x18823f0 ||
        !asset_name(ability,out.name) || !safe_field(controller,0x78,out.cooldown) ||
        !safe_field(controller,0x7c,out.total) || !safe_field(controller,0x80,out.charges) ||
        !safe_field(ability,0xac,out.maximum)) return out;
    int configured=-1;
    if (!safe_field(ability,0xb4,configured) || configured!=out.total || out.cooldown<0 ||
        out.total<out.cooldown || out.state<0 || out.state>14) return out;
    bool charge_ok=out.maximum>0 ? out.charges>=0 && out.charges<=out.maximum : out.charges==-1;
    if (out.state==1 && !out.count && out.charges==-1) charge_ok=true;
    if (!charge_ok) return out;
    void *manager=nullptr,*objects=nullptr; int object_count=0;
    if (!safe_field(player,0x10,manager) || !safe_field(manager,8,objects) ||
        !safe_field(manager,0x14,object_count) || object_count<0 || object_count>=600) return out;
    for (int i=0;i<out.count;++i) {
        void *member=nullptr,*data=nullptr,*member_manager=nullptr;
        int actual_owner=-1;
        if (!safe_field(items,i*sizeof(void*),member)) return out;
        bool found=false;
        for (int j=0;j<object_count;++j) {
            void *candidate=nullptr;
            if (!safe_field(objects,j*sizeof(void*),candidate)) return out;
            if (candidate==member) { found=true; break; }
        }
        if (!found || !safe_field(member,0x48,data) || data!=character ||
            !safe_field(member,0x78,actual_owner) || actual_owner!=owner ||
            !safe_field(member,0x10,member_manager) || member_manager!=manager ||
            !safe_field(member,8,out.members[i]) || !out.members[i]) return out;
        for (int j=0;j<i;++j) if (out.members[j]==out.members[i]) return out;
    }
    out.known=true;
    return out;
}
void append_ability_runtime(char *json,int &offset,size_t size,void *player,int owner) {
    offset+=snprintf(json+offset,size-offset,",\"ability_runtime\":[");
    for (int slot=0;slot<2;++slot) {
        auto a=read_ability_snapshot(player,slot,owner);
        offset+=snprintf(json+offset,size-offset,"%s{\"controller_slot\":%d,\"known\":%s",slot?",":"",slot+1,a.known?"true":"false");
        if (a.known) {
            offset+=snprintf(json+offset,size-offset,",\"empty\":%s,\"ability_name\":\"%s\",\"button_state\":%d,\"cooldown_ms\":%d,\"configured_cooldown_ms\":%d,\"charges\":%d,\"max_charges\":%d,\"members\":[",
                a.empty?"true":"false",a.name,a.state,a.cooldown,a.total,a.charges,a.maximum);
            for (int i=0;i<a.count;++i) offset+=snprintf(json+offset,size-offset,"%s%u",i?",":"",a.members[i]);
            offset+=snprintf(json+offset,size-offset,"]");
        }
        offset+=snprintf(json+offset,size-offset,"}");
    }
    offset+=snprintf(json+offset,size-offset,"]");
}
