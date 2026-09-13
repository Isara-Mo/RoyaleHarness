// Read-only adaptation of FirstLight read_evolution_slot (Apache 2.0;
// see FIRSTLIGHT_NOTICE.md). Calls only the attested selection builder,
// matching upstream snapshot reads; never submits a deployment command.
extern "C" void cr_read_card_selection(void*,void*,int,void*,void*);
struct CardSelection { void *data=nullptr,*secondary=nullptr; uint32_t packed=0,padding=0; };
static_assert(sizeof(CardSelection)==24);
bool runtime_vector(const void *root,size_t off,int limit,void *&data,int &count) {
    int capacity=0;
    return safe_field(root,off,data) && safe_field(root,off+8,capacity) &&
        safe_field(root,off+12,count) && count>=0 && capacity>=count && capacity<=limit && (!count || data);
}
void append_card_runtime(char *json,int &offset,size_t size,void *player,void **slots,int deck_count) {
    void *progress=nullptr; int count=0;
    bool progress_valid=deck_count==8 && runtime_vector(player,0x2e8,8,progress,count) && count==deck_count;
    const uint8_t fingerprint[]={0xa0,0x00,0x00,0xb4,0x09,0x00,0x40,0xf9,0x42,0x00,0x00,0x12,0x23,0x89,0x40,0xf9};
    void *builder=reinterpret_cast<void*>(g_libg_base+0xe2d1bc);
    uint8_t actual[16]={};
    bool builder_valid=safe_bytes(builder,actual,16) && !memcmp(actual,fingerprint,16);
    offset+=snprintf(json+offset,size-offset,",\"card_runtime\":[");
    for (int slot=0;slots && deck_count==8 && slot<deck_count;++slot) {
        void *entry=nullptr,*data=nullptr,*forms=nullptr; int card=0,nforms=0,played=-1;
        bool valid=safe_field(slots,slot*sizeof(void*),entry) && safe_field(entry,0x10,data) &&
            safe_field(data,0x40,card) && card>0 && runtime_vector(data,0x220,16,forms,nforms);
        bool has_progress=progress_valid && safe_field(progress,slot*sizeof(int),played) && played>=0;
        int ids[16]={},codes[16]={},required[16]={};
        for (int i=0;valid && i<nforms;++i) {
            void *form=nullptr,*metadata=nullptr;
            valid=safe_field(forms,i*sizeof(void*),form) && safe_field(form,0x40,ids[i]) && ids[i]>0 &&
                safe_field(form,0x2a8,metadata) && safe_field(metadata,0x64,codes[i]) && codes[i]>=0 && codes[i]<=15;
            if (valid && codes[i]==1) valid=safe_field(form,0x284,required[i]) && required[i]>0 && required[i]<100;
        }
        CardSelection selection;
        bool selection_valid=false;
        if (valid && builder_valid && player) {
            cr_read_card_selection(entry,player,0,&selection,builder);
            selection_valid=selection.data==data && int((selection.packed>>22)&0x3f)-1==slot;
        }
        offset+=snprintf(json+offset,size-offset,
            "%s{\"deck_slot\":%d,\"card_id\":%d,\"variants_known\":%s,\"active_form\":",
            slot?",":"",slot,card,valid?"true":"false");
        if (selection_valid) offset+=snprintf(json+offset,size-offset,"%u,\"selected_cost\":%u",selection.packed&15,selection.packed>>28);
        else offset+=snprintf(json+offset,size-offset,"null,\"selected_cost\":null");
        offset+=snprintf(json+offset,size-offset,",\"role_active\":null,\"evolution_progress\":");
        if (has_progress) offset+=snprintf(json+offset,size-offset,"%d",played);
        else offset+=snprintf(json+offset,size-offset,"null");
        offset+=snprintf(json+offset,size-offset,",\"variants\":[");
        for (int i=0;valid && i<nforms;++i) {
            offset+=snprintf(json+offset,size-offset,"%s{\"data_id\":%d,\"form_code\":%d,\"cycle_required\":",
                i?",":"",ids[i],codes[i]);
            if (codes[i]==1) offset+=snprintf(json+offset,size-offset,"%d",required[i]);
            else offset+=snprintf(json+offset,size-offset,"null");
            offset+=snprintf(json+offset,size-offset,"}");
        }
        offset+=snprintf(json+offset,size-offset,"]}");
    }
    offset+=snprintf(json+offset,size-offset,"]");
}
