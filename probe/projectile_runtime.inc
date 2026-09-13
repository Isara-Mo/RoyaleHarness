// Read-only projection of FirstLight cr_replay_probe.cpp::read_native_projectile_state.
// Exact Nulls 15.535.13 build. Never call attack/deployment/impact functions.
struct LiveReference { bool known=false; uint32_t id=0; };
LiveReference live_reference(void *pointer, void **objects, int count) {
    if (!pointer) return {true,0};
    for (int i=0;i<count;++i) if (objects[i]==pointer) {
        uint32_t id=0;
        if (!safe_field(pointer,8,id) || !id) return {};
        int matches=0;
        for (int j=0;j<count;++j) {
            uint32_t other=0;
            if (safe_field(objects[j],8,other) && other==id) ++matches;
        }
        return matches==1 ? LiveReference{true,id} : LiveReference{};
    }
    return {}; // Non-null outside the current vector is unknown, not absent.
}

bool projectile_layout_attested() {
    static uintptr_t checked=0;
    static bool valid=false;
    if (checked==g_libg_base) return valid;
    checked=g_libg_base;
    // Kind getter: mov w0,#4; ret. Data type: ldr x0,[x0,#0x10]; b ...;
    // table type getter: ldr w0,[x0,#0x84]; ret. Read their fields directly.
    const uint32_t expected[][2]={{0x52800080,0xd65f03c0},
        {0xf9400800,0x1400150e},{0xb9408400,0xd65f03c0}};
    const uintptr_t addresses[]={0xf27a58,0xdc2560,0xdc799c};
    valid=g_libg_base!=0;
    for (int i=0;i<3 && valid;++i) {
        uint32_t actual[2]={};
        valid=safe_bytes(reinterpret_cast<void *>(g_libg_base+addresses[i]),actual,sizeof(actual)) &&
              !memcmp(actual,expected[i],sizeof(actual));
    }
    return valid;
}

void append_projectile_snapshot(char *json,int &offset,size_t size,void *obj,void **objects,int count) {
    uintptr_t vt=0,getter=0;
    if (!safe_field(obj,0,vt) || vt!=g_libg_base+0x189cc28) return;
    void *data=nullptr,*table=nullptr,*source=nullptr,*target=nullptr,*homing=nullptr;
    uint32_t gid=0;
    int type=-1,x=0,y=0,drag_speed=-1;
    uint8_t terminal=255,drag=255;
    bool valid=projectile_layout_attested() && safe_field(reinterpret_cast<void *>(vt),0x10,getter) &&
        getter==g_libg_base+0xf27a58 && safe_field(obj,0x48,data) &&
        safe_field(data,0x10,table) && safe_field(table,0x84,type) && type==10 &&
        safe_field(data,0x40,gid) && gid!=0 && safe_field(data,0x1b0,drag_speed) &&
        safe_field(obj,0x100,source) && safe_field(obj,0x108,target) && safe_field(obj,0x110,homing) &&
        safe_field(obj,0x120,x) && safe_field(obj,0x124,y) &&
        safe_field(obj,0x170,terminal) && terminal<=1;
    if (valid && drag_speed>0) valid=safe_field(obj,0x189,drag) && drag<=1;
    if (!valid) {
        offset+=snprintf(json+offset,size-offset,",\"projectile_runtime\":{\"validated\":false}");
        return;
    }
    LiveReference s=live_reference(source,objects,count),t=live_reference(target,objects,count),h=live_reference(homing,objects,count);
    offset+=snprintf(json+offset,size-offset,
        ",\"projectile_runtime\":{\"validated\":true,\"data_global_id\":%u,"
        "\"source_known\":%s,\"source_id\":%u,\"target_known\":%s,\"target_id\":%u,"
        "\"homing_known\":%s,\"homing_id\":%u,\"destination\":[%d,%d],\"terminal\":%s,\"drag_stage\":%d}",
        gid,s.known?"true":"false",s.id,t.known?"true":"false",t.id,
        h.known?"true":"false",h.id,x,y,terminal?"true":"false",drag_speed>0?int(drag):-1);
}
