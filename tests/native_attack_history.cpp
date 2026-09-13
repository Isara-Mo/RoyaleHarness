#include <cassert>
#include <cstdio>
#include "../probe/attack_history.h"
using namespace nulls_attack;
int main() {
    History h; Edge out[kCapacity+6];
    assert(h.complete(0) && h.collect(0,out)==0);
    h.push({1,90,0,0,0});
    h.push({2,95,1,1,0});
    h.push({3,95,1,0,0});
    assert(h.anchor[1].sequence==2);
    assert(h.collect(95,out)==3);
    assert(out[1].a==1 && out[2].a==0);
    // More than one start/release in one sampling interval remain ordered.
    h.push({4,96,0,0,0}); h.push({5,97,1,1,0});
    assert(h.collect(97,out)==5 && out[4].sequence==5);
    h=History{};
    h.push({1,0,0,0,0});
    h.push({2,1,4,-100,3979700331LL});
    for(int tick=1;tick<200;++tick) h.push({uint64_t(tick+2),tick,3,50,0});
    assert(h.complete(199));
    int n=h.collect(199,out);
    assert(n==13 && out[0].kind==0 && out[1].b==3979700331LL);
    for(int i=1;i<n;++i) assert(out[i-1].sequence<out[i].sequence);
    // Burst beyond capacity is explicit, and recovers only outside its window.
    for(int i=0;i<100;++i) h.push({uint64_t(202+i),200,1,0,0});
    assert(!h.complete(200) && !h.complete(210) && h.complete(211));
    n=h.collect(211,out);
    assert(n==3); // durable start, stop, scale; failures are not phase anchors
    for(int i=0;i<n;++i) assert(out[i].tick<=211);
    assert(h.collect(0,out)==1); // future records never leak backwards
    puts("Native attack history tests passed.");
}
