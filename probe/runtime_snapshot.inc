// Read-only fields for the attested Nulls 15.535.13 libg build.
// Layout sources: FirstLight_CR/native_runner/probe/phase_runtime_layout.h
// and cr_replay_probe.cpp::read_shield/read_bounded_libg_string_utf8.
#include "phase_runtime_layout.h"

bool safe_bytes(const void *p, void *out, size_t size) {
    // Android denies /proc/self/mem to this app domain. Validate readable
    // mappings before copying, as the original native probe does.
    struct Range { uintptr_t begin, end; };
    static thread_local Range ranges[8192];
    static thread_local int count=0;
    static thread_local int64_t refreshed=0;
    if (!is_valid_pointer(p) || size > 65536) return false;
    int64_t now=current_time_ms();
    if (!count || now-refreshed > 1000) {
        FILE *maps=fopen("/proc/self/maps","r");
        if (!maps) return false;
        count=0; char line[512], flags[5]; uintptr_t a,b;
        while (fgets(line,sizeof(line),maps) && count<8192) {
            if (sscanf(line,"%lx-%lx %4s",&a,&b,flags)==3 && flags[0]=='r') ranges[count++]={a,b};
        }
        fclose(maps); refreshed=now;
    }
    uintptr_t address=reinterpret_cast<uintptr_t>(p);
    if (address+size < address) return false;
    int lo=0,hi=count;
    while (lo<hi) { int mid=(lo+hi)/2; if (ranges[mid].begin<=address) lo=mid+1; else hi=mid; }
    if (lo>0 && address+size<=ranges[lo-1].end) { memcpy(out,p,size); return true; }
    return false;
}
template<class T> bool safe_field(const void *p, size_t off, T &out) {
    return p && safe_bytes(static_cast<const uint8_t *>(p) + off, &out, sizeof(out));
}
void *runtime_component(void *object, int index) {
    void **parts = nullptr;
    int count = 0, capacity = 0;
    void *part = nullptr, *owner = nullptr;
    if (!safe_field(object, 0x18, parts) || !safe_field(object, 0x20, capacity) ||
        !safe_field(object, 0x24, count) || count <= index || capacity < count || capacity > 32 ||
        !safe_field(parts, index * sizeof(void *), part) || !safe_field(part, 8, owner) || owner != object) return nullptr;
    return part;
}
bool asset_name(const void *asset, char (&name)[129]) {
    name[0] = 0;
    int len = 0;
    const char *bytes = nullptr;
    if (!safe_field(asset, 0x2c, len) || len <= 0 || len > 128) return false;
    if (len < 8) bytes = static_cast<const char *>(asset) + 0x30;
    else if (!safe_field(asset, 0x30, bytes)) return false;
    if (!safe_bytes(bytes, name, len)) { name[0] = 0; return false; }
    name[len] = 0;
    // Internal asset identifiers only; reject arbitrary strings/JSON escapes.
    for (int i = 0; i < len; ++i) {
        char c = name[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '_' || c == '-')) { name[0] = 0; return false; }
    }
    return true;
}

#include "projectile_runtime.inc"
#include "attack_edges.inc"
#include "deployment_timing.inc"
#include "active_effects.inc"

void append_runtime_snapshot(char *json, int &offset, size_t size, void *obj,
                             void **objects, int count, bool tower) {
    char name[129] = {};
    void *data = nullptr;
    safe_field(obj, 0x48, data);
    asset_name(data, name);
    offset += snprintf(json + offset, size-offset, ",\"native_data_name\":\"%s\"", name);
    append_projectile_snapshot(json,offset,size,obj,objects,count);
    append_deployment_timing(json,offset,size,obj,data);
    void *health = runtime_component(obj, 2);
    int shield = -1, max_shield = -1;
    if (health && safe_field(health, 0x28, shield) && safe_field(health, 0x2c, max_shield) &&
        shield >= 0 && max_shield >= shield && max_shield < 1000000) {
        offset += snprintf(json+offset, size-offset, ",\"shield\":%d,\"max_shield\":%d", shield, max_shield);
    }
    void *attack = runtime_component(obj, 0);
    uintptr_t vtable = 0;
    if (attack && safe_field(attack, 0, vtable) && vtable == g_libg_base + cr_phase_runtime::kAttackComponentVtableOffset) {
        void *target = nullptr;
        int timeline=-1, load=-1, stage=-2, speed=-1, dash=-1;
        if (safe_field(attack, 0x10, target) && safe_field(attack, 0x24, timeline) &&
            safe_field(attack, 0x28, load) && safe_field(attack, 0x20, stage) &&
            timeline >= 0 && timeline <= 3600000 && load >= 0 && load <= 3600000 && stage >= -1 && stage <= 255) {
            LiveReference reference=live_reference(target,objects,count);
            uint32_t target_id=reference.id;
            bool target_known=reference.known;
            safe_field(data, cr_phase_runtime::kCharacterHitSpeedOffset, speed);
            safe_field(data, cr_phase_runtime::kCharacterAttackDashTimeOffset, dash);
            offset += snprintf(json+offset, size-offset,
                ",\"attack_runtime\":{\"target_known\":%s,\"target_id\":%u,\"timeline_ms\":%d,\"load_ms\":%d,\"sequence_stage\":%d,\"hit_speed_ms\":%d,\"dash_ms\":%d",
                target_known?"true":"false", target_id, timeline, load, stage, speed, dash);
            uint32_t id=read_field<uint32_t>(obj,8);
            pthread_mutex_lock(&g_attack_mutex);
            AttackEdges edges=g_attack_edges[id%512];
            int edge_tick=g_attack_tick;
            uint64_t edge_epoch=g_attack_epoch;
            pthread_mutex_unlock(&g_attack_mutex);
            int edge_owner=-1,edge_card=0;
            bool identity_fields=safe_field(obj,0x78,edge_owner) && safe_field(obj,0xac,edge_card);
            const bool identity=identity_fields && (!edges.object || (edges.object==obj && edges.id==id &&
                edges.data==data && edges.owner==edge_owner && edges.card==edge_card));
            nulls_attack::Edge selected[nulls_attack::kCapacity+6];
            int n=identity ? edges.history.collect(edge_tick,selected) : 0;
            // Leave room for remaining fields and top-level diagnostics.
            bool capacity=offset>=0 && size_t(offset)+size_t(n)*160+8192<size;
            bool complete=identity && edges.history.complete(edge_tick) && capacity;
            offset+=snprintf(json+offset,size-offset,
                ",\"hooks_ready\":%s,\"history_schema\":\"nulls-attack-history.v3\",\"history_complete\":%s,\"epoch\":%llu,\"entity_id\":%u,\"snapshot_tick\":%d,\"window_ticks\":%d,\"owner\":%d,\"card_id\":%d,\"edges\":[",
                g_attack_hooks_ready?"true":"false",complete?"true":"false",
                static_cast<unsigned long long>(edge_epoch),id,edge_tick,nulls_attack::kWindowTicks,edge_owner,edge_card);
            if (complete) for (int k=0;k<n;++k) {
                const auto &edge=selected[k];
                offset+=snprintf(json+offset,size-offset,"%s{\"kind\":%d,\"sequence\":%llu,\"tick\":%d,\"a\":%d,\"b\":%lld}",
                    k?",":"",edge.kind,static_cast<unsigned long long>(edge.sequence),edge.tick,edge.a,static_cast<long long>(edge.b));
            }
            offset+=snprintf(json+offset,size-offset,"]}");
        }
    }
    if (tower) {
        // Exact BUILDING asset names from this APK, joined to FirstLight's
        // competitive Tower Troop catalog. Unknown names remain unknown.
        int troop=0;
        if (!strcmp(name,"PrincessTower")) troop=159000000;
        else if (!strcmp(name,"Cannoneer")) troop=159000001;
        else if (!strcmp(name,"DaggerDuchess")) troop=159000002;
        else if (!strcmp(name,"ChefTower") || !strcmp(name,"ChefTowerKing")) troop=159000004;
        if (troop) offset+=snprintf(json+offset,size-offset,",\"tower_troop_id\":%d",troop);
    }
    append_active_effects(json,offset,size,obj,objects,count);
}
