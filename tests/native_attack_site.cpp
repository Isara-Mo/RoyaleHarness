#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
extern "C" {
uintptr_t cr_attack_start_dispatch=0, cr_attack_start_skip=0;
void test_attack_site(void*,void*,uint64_t*);
void *observed=nullptr;
int calls=0;
void cr_attack_start_observed(void *owner) {
    observed=owner; ++calls;
    asm volatile("mov x9,#99\n movi v0.16b,#0xff\n movi v31.16b,#0xff\n cmp x9,#0"
                 ::: "x9", "v0", "v31", "cc");
}
}
int main() {
    alignas(16) char data[512]={};
    uint64_t out[10]={}; void *owner=data+400;
    const uintptr_t actions[]={0,0x123456};
    for (uintptr_t action : actions) {
        memcpy(data+0x178,&action,sizeof(action));
        test_attack_site(owner,data,out);
        assert(observed==owner && out[3]==action);
        assert(out[4]==42 && out[5]==0x60000000 && out[6]==0 && out[7]==0);
        assert(out[8]==0 && out[9]==0);
        if (action) assert(out[0]==1 && out[1]==uintptr_t(owner) && out[2]==uintptr_t(owner));
        else assert(out[0]==0 && out[1]==111 && out[2]==222);
    }
    assert(calls==2);
    puts("Native attack site: both original routes, registers, SIMD, flags preserved.");
}
