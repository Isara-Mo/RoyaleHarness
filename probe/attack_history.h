#pragma once
#include <cstdint>
#include <cstddef>

// Recent ordered evidence plus durable phase anchors. A failed release must
// never erase the last successful release. Anchors survive a long Freeze.
namespace nulls_attack {
constexpr int kWindowTicks = 10;
constexpr int kCapacity = 64;
struct Edge {
    uint64_t sequence=0;
    int tick=-1, kind=-1, a=0;
    int64_t b=0; // Buff IDs are native uint32, not signed int32.
};
struct History {
    Edge recent[kCapacity], anchor[6];
    uint64_t count=0;
    int lost_through_tick=-1;
    void push(Edge e) {
        if (!e.sequence || e.tick<0 || e.kind>=6 || e.kind<0) return;
        auto &slot=recent[count%kCapacity];
        if (slot.sequence) lost_through_tick=slot.tick;
        slot=e; ++count;
        if (e.kind!=1 || e.a==1) anchor[e.kind]=e;
    }
    bool complete(int tick) const { return lost_through_tick<0 || lost_through_tick<tick-kWindowTicks; }
    int collect(int tick, Edge (&out)[kCapacity+6]) const {
        int n=0;
        const uint64_t begin=count>kCapacity ? count-kCapacity : 0;
        for (uint64_t i=begin;i<count;++i) {
            const auto &e=recent[i%kCapacity];
            if (e.tick<=tick && e.tick>=tick-kWindowTicks) out[n++]=e;
        }
        for (const auto &e:anchor) {
            if (!e.sequence || e.tick>tick) continue;
            bool duplicate=false;
            for (int i=0;i<n;++i) if (out[i].sequence==e.sequence) duplicate=true;
            if (!duplicate) out[n++]=e;
        }
        // Stable order is part of the wire contract, independent of kind.
        for (int i=1;i<n;++i) {
            Edge e=out[i]; int j=i;
            while (j>0 && out[j-1].sequence>e.sequence) {out[j]=out[j-1]; --j;}
            out[j]=e;
        }
        return n;
    }
};
}
