.text
.align 2
.global test_attack_site
.type test_attack_site,%function
test_attack_site:
    stp x19,x20,[sp,#-48]!
    stp x21,x30,[sp,#16]
    mov x20,x0
    mov x21,x1
    mov x19,x2
    adr x9,.Ldispatch
    adrp x10,cr_attack_start_dispatch
    str x9,[x10,:lo12:cr_attack_start_dispatch]
    adr x9,.Lskip
    adrp x10,cr_attack_start_skip
    str x9,[x10,:lo12:cr_attack_start_skip]
    mov x0,#111
    mov x1,#222
    mov x9,#42
    movi v0.16b,#0
    movi v31.16b,#0
    cmp x9,x9
    b cr_attack_start_site
.Ldispatch:
    mov x10,#1
    b .Lfinish
.Lskip:
    mov x10,#0
.Lfinish:
    str x10,[x19]
    stp x0,x1,[x19,#8]
    stp x2,x9,[x19,#24]
    mrs x10,nzcv
    str x10,[x19,#40]
    str q0,[x19,#48]
    str q31,[x19,#64]
    ldp x21,x30,[sp,#16]
    ldp x19,x20,[sp],#48
    ret
.size test_attack_site,.-test_attack_site
.section .note.GNU-stack,"",@progbits
