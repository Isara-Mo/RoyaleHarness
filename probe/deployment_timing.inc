// Read-only phase fields, from the same attested FirstLight layout as attacks.
// Snapshot ticks are observation times; these reads do not identify exact births.
void append_deployment_timing(char *json,int &offset,size_t size,void *obj,void *data) {
    using namespace cr_phase_runtime;
    void *attack=runtime_component(obj,0);
    uintptr_t vt=0;
    if (!attack || !safe_field(attack,0,vt) || vt!=g_libg_base+kAttackComponentVtableOffset) return;
    int remaining=-1,previous=-1,configured=-1;
    if (safe_field(obj,kObjectDeployRemainingOffset,remaining) &&
        safe_field(obj,kObjectDeployPreviousOffset,previous) &&
        safe_field(data,kCharacterDeployTimeOffset,configured) &&
        remaining>=0 && remaining<=3600000 && previous>=0 && previous<=3600000 &&
        configured>=0 && configured<=3600000) {
        offset+=snprintf(json+offset,size-offset,
            ",\"deployment_runtime\":{\"validated\":true,\"remaining_ms\":%d,\"previous_ms\":%d,\"configured_ms\":%d}",
            remaining,previous,configured);
    }
    void *movement=runtime_component(obj,1);
    int progress=-2,multiplier=-1;
    if (movement && safe_field(movement,0,vt) && vt==g_libg_base+kMovementComponentVtableOffset &&
        safe_field(movement,kClassicChargeProgressOffset,progress) && valid_classic_charge_progress(progress) &&
        safe_field(data,kCharacterChargeSpeedMultiplierOffset,multiplier) && multiplier>=0 && multiplier<=10000) {
        offset+=snprintf(json+offset,size-offset,
            ",\"movement_runtime\":{\"validated\":true,\"classic_charge_progress\":%d,\"charge_speed_multiplier\":%d}",
            progress,multiplier);
    }
}

// c893d0 validates stock UI commands before calling c89778, which constructs
// and enqueues ClientInput. Observe that path; never inject or modify commands.
// This is client acceptance, not acknowledgement by the remote server.
struct ClientInputEdge {
    void *world=nullptr;
    uint64_t sequence=0;
    int tick=-1,input_tick=-1,command_type=-1;
    int64_t monotonic_ms=0;
};
ClientInputEdge g_client_inputs[32];
uint64_t g_client_input_sequence=0;
pthread_mutex_t g_client_input_mutex=PTHREAD_MUTEX_INITIALIZER;
bool g_client_input_hook_ready=false;
void *g_client_input_snapshot_world=nullptr;
int g_client_input_snapshot_tick=-1;
using ClientInputFn=void(*)(void*,void*,int,int);
ClientInputFn g_client_input_original=nullptr;

void client_input_observer(void *controller,void *command_owner,int input_tick,int checksum) {
    uintptr_t caller=reinterpret_cast<uintptr_t>(__builtin_return_address(0))-g_libg_base;
    void *manager=nullptr,*world=nullptr,*command=nullptr,*vtable=nullptr,*getter=nullptr;
    uint32_t words[2]={};
    ClientInputEdge edge;
    if (caller==0xc89730 && safe_field(controller,0x90,manager) && manager==g_live_manager.load() &&
        safe_field(manager,0xa8,world) && world && safe_field(command_owner,0,command) &&
        safe_field(command,0,vtable) && safe_field(vtable,0x20,getter) && safe_bytes(getter,words,sizeof(words)) &&
        words[1]==0xd65f03c0 && (words[0]&0xffe0001f)==0x52800000) {
        edge.world=world; edge.tick=g_game_state_tick(manager); edge.input_tick=input_tick;
        edge.command_type=(words[0]>>5)&0xffff; edge.monotonic_ms=current_time_ms();
    }
    g_client_input_original(controller,command_owner,input_tick,checksum);
    if (edge.world && edge.tick>=0) {
        pthread_mutex_lock(&g_client_input_mutex);
        edge.sequence=++g_client_input_sequence;
        g_client_inputs[edge.sequence%32]=edge;
        pthread_mutex_unlock(&g_client_input_mutex);
    }
}

bool install_client_input_observer() {
    const uint8_t bytes[]={0xff,0x43,0x01,0xd1,0xfd,0x7b,0x01,0xa9,
                           0xf8,0x5f,0x02,0xa9,0xf6,0x57,0x03,0xa9};
    auto *target=reinterpret_cast<void*>(g_libg_base+0xc89778);
    if (memcmp(target,bytes,16)) return false;
    return install_inline_hook(g_libg_base+0xc89778,bytes,16,
        reinterpret_cast<void*>(client_input_observer),
        reinterpret_cast<void**>(&g_client_input_original),"client input timing");
}

void append_client_input_edges(char *json,int &offset,size_t size,void *world,int tick) {
    offset+=snprintf(json+offset,size-offset,",\"client_input_runtime\":{\"hook_ready\":%s,\"events\":[",
        g_client_input_hook_ready?"true":"false");
    pthread_mutex_lock(&g_client_input_mutex);
    if (world!=g_client_input_snapshot_world || tick<g_client_input_snapshot_tick) {
        for (auto &e:g_client_inputs) e=ClientInputEdge{};
    }
    g_client_input_snapshot_world=world;
    g_client_input_snapshot_tick=tick;
    bool first=true;
    uint64_t begin=g_client_input_sequence>31?g_client_input_sequence-31:1;
    for (uint64_t seq=begin;seq<=g_client_input_sequence;++seq) {
        auto &e=g_client_inputs[seq%32];
        if (e.sequence!=seq || e.world!=world || e.tick>tick || tick-e.tick>100) continue;
        offset+=snprintf(json+offset,size-offset,
            "%s{\"sequence\":%llu,\"tick\":%d,\"input_tick\":%d,\"command_type\":%d,\"monotonic_ms\":%lld}",
            first?"":",",static_cast<unsigned long long>(seq),e.tick,e.input_tick,e.command_type,
            static_cast<long long>(e.monotonic_ms));
        first=false;
    }
    pthread_mutex_unlock(&g_client_input_mutex);
    offset+=snprintf(json+offset,size-offset,"]}");
}
