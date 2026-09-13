// Exercise the actual read-only buff serializer against fake engine storage.
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

uintptr_t g_libg_base=0;
template<class T> bool safe_field(const void *p,size_t off,T &out) {
    if (!p) return false;
    memcpy(&out,static_cast<const char*>(p)+off,sizeof(T)); return true;
}
bool safe_bytes(const void *p,void *out,size_t n) {
    if (!p) return false; memcpy(out,p,n); return true;
}
char component[256], object[256], source[256], entry[256], asset[256];
bool has_component=true;
void *runtime_component(void*,int index) { return has_component && index==3 ? component : nullptr; }
bool asset_name(const void*,char (&out)[129]) { strcpy(out,"Cannon_EV1_barrage_damage_buff"); return true; }
struct LiveReference { bool known=false; uint32_t id=0; };
LiveReference live_reference(void *p,void **objects,int count) {
    if (!p) return {true,0};
    for(int i=0;i<count;++i) if(objects[i]==p) return {true,5000100};
    return {};
}
#include "../probe/active_effects.inc"
bool effect_origin_runtime(uint64_t &epoch) {epoch=0;return false;}
bool append_live_effect_origin(char *json,int &offset,size_t size,void*,void*) {
    if(offset<0 || size_t(offset)+1024>=size)return false;
    offset+=snprintf(json+offset,size-offset,",\"origin\":null");return true;
}
template<class T> void put(void *p,size_t off,T value) { memcpy(static_cast<char*>(p)+off,&value,sizeof(value)); }
char output[65536];
int render(size_t size=sizeof(output)) {
    memset(output,0x7f,sizeof(output)); output[0]=0;
    int used=0; void *objects[]={source};
    append_active_effects(output,used,size,object,objects,1);
    assert(used>=0 && size_t(used)<size);
    assert(size==sizeof(output) || output[size]==0x7f);
    return used;
}
int main() {
    std::vector<char> libg(0x1a00000);
    g_libg_base=reinterpret_cast<uintptr_t>(libg.data());
    put(component,0,g_libg_base+0x1000);
    put(libg.data(),0x1020,g_libg_base+0x2000);
    put(libg.data(),0x2000,uint32_t(0x52800060));
    put(libg.data(),0x2004,uint32_t(0xd65f03c0));
    void *entries[]={entry,entry};
    put(component,0x18,entries); put(component,0x20,2); put(component,0x24,2); put(component,0x34,0);
    put(entry,8,-1); put(entry,0x18,static_cast<void*>(asset)); put(entry,0x38,static_cast<void*>(source));
    put(asset,0,g_libg_base+0x1882500); put(asset,0x40,uint32_t(3979700331));
    put(libg.data(),0x1882570,g_libg_base+0xd961f4); put(source,0x78,1);
    render(); assert(strstr(output,"\"validated\":true") && strstr(output,"\"count\":2"));
    assert(strstr(output,"3979700331") && strstr(output,"\"remaining_ms\":-1") && strstr(output,"\"source_owner\":1"));
    put(entry,0x38,static_cast<void*>(nullptr)); render(); assert(strstr(output,"\"source_known\":true,\"source_id\":0"));
    put(entry,0x38,static_cast<void*>(object)); render(); assert(strstr(output,"\"source_known\":false"));
    put(entry,8,-2); render(); assert(strstr(output,"\"validated\":false") && !strstr(output,"\"effects\""));
    put(entry,8,0); render(); assert(strstr(output,"\"remaining_ms\":0"));
    put(asset,0,g_libg_base+0x1882508); render(); assert(strstr(output,"layout_or_entry_invalid"));
    put(asset,0,g_libg_base+0x1882500);
    render(1000); assert(strstr(output,"snapshot_capacity"));
    void *full[64]; for (auto &p:full) p=entry;
    put(component,0x18,full); put(component,0x20,64); put(component,0x24,64);
    render(); assert(strstr(output,"\"validated\":true") && strstr(output,"\"count\":64"));
    put(component,0x20,65); render(); assert(strstr(output,"layout_or_entry_invalid"));
    put(component,0x18,entries); put(component,0x20,2);
    put(component,0x24,0); render(); assert(strstr(output,"\"effects\":[]"));
    put(component,0x24,3); render(); assert(strstr(output,"layout_or_entry_invalid"));
    put(component,0x24,1); put(libg.data(),0x2000,uint32_t(0x52800080));
    render(); assert(strstr(output,"layout_or_entry_invalid"));
    has_component=false; assert(render()==0);
    puts("Native active-effect tests passed: layout, unsigned identity, lifetime, source, complete vector, capacity.");
}
