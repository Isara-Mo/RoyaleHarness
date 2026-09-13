// Adapted from FirstLight native_call_arm64.S; see FIRSTLIGHT_LICENSE.
.text
.align 2
.global cr_read_card_selection
.hidden cr_read_card_selection
.type cr_read_card_selection, %function
// x0=deck slot, x1=player, w2=alternate, x3=result, x4=builder.
cr_read_card_selection:
    stp x29, x30, [sp, #-16]!
    mov x29, sp
    mov x8, x3
    blr x4
    ldp x29, x30, [sp], #16
    ret
.size cr_read_card_selection, .-cr_read_card_selection
.section .note.GNU-stack,"",@progbits
