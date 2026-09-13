// Exact-build read-only attack hooks. No gameplay calls are injected.
#include "attack_history.h"
struct AttackEdges {
    void *object=nullptr, *data=nullptr;
    uint32_t id=0;
    int owner=-1,card=0;
    nulls_attack::History history;
};
AttackEdges g_attack_edges[512];
pthread_mutex_t g_attack_mutex=PTHREAD_MUTEX_INITIALIZER;
uint64_t g_edge_sequence=0;
bool g_attack_hooks_ready=false;
void *g_attack_world=nullptr;
int g_attack_tick=-1;
uint64_t g_attack_epoch=0;
// Called under g_attack_mutex at both producer and snapshot boundaries.
void attack_epoch_locked(void *world,int tick) {
    if (world!=g_attack_world || tick<g_attack_tick) {
        for (auto &entry:g_attack_edges) entry=AttackEdges{};
        ++g_attack_epoch;
    }
    g_attack_world=world; g_attack_tick=tick;
}
using DispatchFn=void(*)(void*,void*,void*);
using ExecuteFn=int(*)(void*,void*,int,void*,int,int,int);
using TargetFn=void(*)(void*,void*,int,int,int);
using ScaleFn=int(*)(void*,int);
using BuffFn=void(*)(void*,void*);
DispatchFn g_dispatch=nullptr;
ExecuteFn g_execute=nullptr;
TargetFn g_target=nullptr;
ScaleFn g_scale=nullptr;
BuffFn g_buff=nullptr;
std::atomic<uint64_t> g_execute_calls{0};
std::atomic<uintptr_t> g_execute_caller{0};
std::atomic<int> g_execute_result{0};
std::atomic<uint32_t> g_execute_owner_id{0};
thread_local void *g_attack_release_scope=nullptr;
thread_local bool g_attack_scope_issued=false;
extern "C" void cr_attack_start_site();
extern "C" {
__attribute__((visibility("hidden"))) uintptr_t cr_attack_start_dispatch=0;
__attribute__((visibility("hidden"))) uintptr_t cr_attack_start_skip=0;
}

void record_attack_edge(void *object,int kind,int a=0,int64_t b=0) {
    void *manager=g_live_manager.load();
    void *world=nullptr,*player=nullptr,*live_objects=nullptr,*object_manager=nullptr;
    uint32_t id=0; void *data=nullptr; int owner=-1,card=0;
    if (!manager || !safe_field(manager,0xa8,world) || !safe_field(world,0xe0,player) ||
        !safe_field(player,0x10,live_objects) || !safe_field(object,0x10,object_manager) ||
        object_manager!=live_objects || !safe_field(object,8,id) || !id ||
        !safe_field(object,0x48,data) || !data || kind<0 || kind>=6 ||
        !safe_field(object,0x78,owner) || owner<0 || owner>1 || !safe_field(object,0xac,card)) return;
    int tick=g_game_state_tick(manager);
    if (tick<0) return;
    pthread_mutex_lock(&g_attack_mutex);
    attack_epoch_locked(world,tick);
    auto &entry=g_attack_edges[id%512];
    if (entry.object!=object || entry.id!=id || entry.data!=data || entry.owner!=owner || entry.card!=card) {
        entry=AttackEdges{}; entry.object=object; entry.id=id; entry.data=data;
        entry.owner=owner; entry.card=card;
    }
    entry.history.push({++g_edge_sequence,tick,kind,a,b});
    pthread_mutex_unlock(&g_attack_mutex);
}
void attack_dispatch(void *owner,void *target,void *action) {
    uintptr_t caller=reinterpret_cast<uintptr_t>(__builtin_return_address(0))-g_libg_base;
    // f5ff20 is unconditional on the normal release branch, even when the
    // optional action is null. The execute return bit is NOT release success:
    // stock normal attacks reach this boundary with w26 == 0.
    if (caller==0xf5ff24 && owner && owner==g_attack_release_scope) {
        record_attack_edge(owner,1,1);
        g_attack_scope_issued=true;
    }
    if (action) g_dispatch(owner,target,action);
}
extern "C" __attribute__((visibility("hidden"))) void cr_attack_start_observed(void *owner) {
    record_attack_edge(owner,0);
}
int attack_execute(void *component,void *target,int index,void *aux,int a,int b,int c) {
    uintptr_t caller=reinterpret_cast<uintptr_t>(__builtin_return_address(0))-g_libg_base;
    g_execute_calls.fetch_add(1); g_execute_caller.store(caller);
    void *owner=nullptr;
    safe_field(component,8,owner);
    void *previous_scope=g_attack_release_scope;
    bool previous_issued=g_attack_scope_issued;
    g_attack_release_scope=(caller==cr_phase_runtime::kPrimaryAttackReturnOffset ||
        caller==cr_phase_runtime::kAdditionalAttackReturnOffset) ? owner : nullptr;
    g_attack_scope_issued=false;
    int result=g_execute(component,target,index,aux,a,b,c);
    if (g_attack_release_scope && !g_attack_scope_issued)
        record_attack_edge(owner,5,result&1); // exact attempt, release outcome unknown
    g_attack_release_scope=previous_scope;
    g_attack_scope_issued=previous_issued;
    g_execute_result.store(result);
    uint32_t diagnostic_id=0;
    if (safe_field(component,8,owner)) safe_field(owner,8,diagnostic_id);
    g_execute_owner_id.store(diagnostic_id);
    return result;
}
void attack_target(void *component,void *target,int a,int b,int c) {
    int before=-1,after=-1; void *owner=nullptr;
    safe_field(component,0x24,before);
    g_target(component,target,a,b,c);
    if (before>0 && safe_field(component,0x24,after) && after==0 && safe_field(component,8,owner))
        record_attack_edge(owner,2,before,after);
}
int attack_scale(void *component,int input) {
    uintptr_t caller=reinterpret_cast<uintptr_t>(__builtin_return_address(0))-g_libg_base;
    int output=g_scale(component,input); void *owner=nullptr;
    if (caller==cr_phase_runtime::kAttackScaleReturnOffset && input>0 && output>=0 && safe_field(component,8,owner))
        record_attack_edge(owner,3,input,output);
    return output;
}
void effect_origin_added(void *component,void *entry);
void attack_buff(void *component,void *entry) {
    void *owner=nullptr,*data=nullptr; int multiplier=0; uint32_t gid=0;
    bool valid=safe_field(component,8,owner) && safe_field(entry,0x18,data) && safe_field(data,0xe0,multiplier) && safe_field(data,0x40,gid) && gid>0;
    effect_origin_added(component,entry);
    g_buff(component,entry);
    if (valid && multiplier<=-100) record_attack_edge(owner,4,multiplier,gid);
}
bool install_attack_edges() {
    using namespace cr_phase_runtime;
    const uintptr_t offsets[]={kActionDispatchOffset,kAttackExecuteOffset,kTargetSetOffset,kAttackScaleOffset,kBuffAddedOffset};
    const uint8_t *bytes[]={kActionDispatchPrologue,kAttackExecutePrologue,kTargetSetPrologue,kAttackScalePrologue,kBuffAddedPrologue};
    for (int i=0;i<5;++i) if (memcmp(reinterpret_cast<void*>(g_libg_base+offsets[i]),bytes[i],16)) return false;
    // Observe the already-taken cycle-start branch BEFORE its optional action
    // check. Re-execute all four stolen instructions with both original routes.
    const uint32_t start_words[]={0xf940bea2,0xb4000082,0xaa1403e0,0xaa1403e1};
    const uint32_t release_words[]={0xf940ca82,0xaa1303e0,0xaa1803e1,0x97ff0c7c};
    if (memcmp(reinterpret_cast<void*>(g_libg_base+0xf61a68),start_words,16) ||
        memcmp(reinterpret_cast<void*>(g_libg_base+0xf5ff14),release_words,16)) return false;
    // The original first instruction is PC-relative CBZ x2; copy only the
    // following stack prologue, preserving the null branch in the wrapper.
    auto *target=reinterpret_cast<uint8_t*>(g_libg_base+kActionDispatchOffset);
    void *trampoline=mmap(nullptr,32,PROT_READ|PROT_WRITE|PROT_EXEC,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    if (trampoline==MAP_FAILED) return false;
    memcpy(trampoline,target+4,12); emit_absolute_jump(static_cast<uint8_t*>(trampoline)+12,target+16);
    __builtin___clear_cache(static_cast<char*>(trampoline),static_cast<char*>(trampoline)+32);
    if (!make_page_writable(target,PROT_READ|PROT_WRITE|PROT_EXEC)) return false;
    g_dispatch=reinterpret_cast<DispatchFn>(trampoline);
    emit_absolute_jump(target,reinterpret_cast<void*>(&attack_dispatch));
    __builtin___clear_cache(reinterpret_cast<char*>(target),reinterpret_cast<char*>(target+16));
    make_page_writable(target,PROT_READ|PROT_EXEC);
    bool ok=true;
    ok &= install_inline_hook(g_libg_base+kAttackExecuteOffset,kAttackExecutePrologue,16,reinterpret_cast<void*>(&attack_execute),reinterpret_cast<void**>(&g_execute),"attack release");
    ok &= install_inline_hook(g_libg_base+kTargetSetOffset,kTargetSetPrologue,16,reinterpret_cast<void*>(&attack_target),reinterpret_cast<void**>(&g_target),"attack reset");
    ok &= install_inline_hook(g_libg_base+kAttackScaleOffset,kAttackScalePrologue,16,reinterpret_cast<void*>(&attack_scale),reinterpret_cast<void**>(&g_scale),"attack timing scale");
    ok &= install_inline_hook(g_libg_base+kBuffAddedOffset,kBuffAddedPrologue,16,reinterpret_cast<void*>(&attack_buff),reinterpret_cast<void**>(&g_buff),"attack stop effect");
    if (!ok) return false;
    auto *start=reinterpret_cast<uint8_t*>(g_libg_base+0xf61a68);
    cr_attack_start_dispatch=g_libg_base+0xf61a78;
    cr_attack_start_skip=g_libg_base+0xf61a7c;
    if (!make_page_writable(start,PROT_READ|PROT_WRITE|PROT_EXEC)) return false;
    emit_absolute_jump(start,reinterpret_cast<void*>(&cr_attack_start_site));
    __builtin___clear_cache(reinterpret_cast<char*>(start),reinterpret_cast<char*>(start+16));
    make_page_writable(start,PROT_READ|PROT_EXEC);
    return ok;
}
