.text
.p2align 2
// Adapted ABI-preserving observer from FirstLight remaining_runtime_arm64.S.
.global summon_mode1_entry
.type summon_mode1_entry, %function
summon_mode1_entry:
    sub sp, sp, #0xd0
    stp x0, x1, [sp, #0x00]
    stp x2, x3, [sp, #0x10]
    stp x4, x5, [sp, #0x20]
    stp x6, x7, [sp, #0x30]
    stp x8, x30, [sp, #0x40]
    stp q0, q1, [sp, #0x50]
    stp q2, q3, [sp, #0x70]
    stp q4, q5, [sp, #0x90]
    stp q6, q7, [sp, #0xb0]
    mov x4, x30
    mov x5, x21
    ldr x6, [sp, #0x138]
    bl summon_mode1_bridge
    ldp q6, q7, [sp, #0xb0]
    ldp q4, q5, [sp, #0x90]
    ldp q2, q3, [sp, #0x70]
    ldp q0, q1, [sp, #0x50]
    ldp x8, x30, [sp, #0x40]
    ldp x6, x7, [sp, #0x30]
    ldp x4, x5, [sp, #0x20]
    ldp x2, x3, [sp, #0x10]
    ldp x0, x1, [sp, #0x00]
    add sp, sp, #0xd0
    adrp x16, :got:g_summon_mode1_original
    ldr x16, [x16, :got_lo12:g_summon_mode1_original]
    ldr x16, [x16]
    br x16
.size summon_mode1_entry, .-summon_mode1_entry


.section .note.GNU-stack,"",%progbits
