// Adapted from FirstLight's cr_combat_heal_entry. The native f64d14 ABI has
// seven integer/pointer arguments. Capture caller-only provenance before the
// bridge can change x19, x29, or LR; x0..x6 are forwarded without alteration.
.text
.align 2
.global cr_heal_entry
.hidden cr_heal_entry
.type cr_heal_entry, %function
cr_heal_entry:
    mov x9, x29
    mov x10, x30
    sub sp, sp, #32
    str x9, [sp, #0]
    str x10, [sp, #8]
    stp x29, x30, [sp, #16]
    mov x7, x19
    bl cr_heal_bridge
    ldp x29, x30, [sp, #16]
    add sp, sp, #32
    ret
.size cr_heal_entry, .-cr_heal_entry
.section .note.GNU-stack,"",@progbits
