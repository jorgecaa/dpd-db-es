//! Envoltorio C de minify-html 0.18.1, la misma biblioteca y version que usa
//! el exportador de Python, con la misma configuracion (todo en falso, como
//! los valores por defecto del binding de Python).

use minify_html::{minify, Cfg};

/// Minifica `entrada[..largo]`. Devuelve un puntero malloc'd (liberar con
/// `minificar_liberar`) y deja el largo del resultado en `salida_largo`.
#[no_mangle]
pub extern "C" fn minificar(
    entrada: *const u8,
    largo: usize,
    salida_largo: *mut usize,
) -> *mut u8 {
    let datos = unsafe { std::slice::from_raw_parts(entrada, largo) };
    let cfg = Cfg::default();
    let salida = minify(datos, &cfg);
    let n = salida.len();
    unsafe {
        *salida_largo = n;
        let p = libc_malloc(n + 1);
        std::ptr::copy_nonoverlapping(salida.as_ptr(), p, n);
        *p.add(n) = 0;
        p
    }
}

extern "C" {
    fn malloc(n: usize) -> *mut u8;
    fn free(p: *mut u8);
}

unsafe fn libc_malloc(n: usize) -> *mut u8 {
    malloc(n)
}

#[no_mangle]
pub extern "C" fn minificar_liberar(p: *mut u8) {
    unsafe { free(p) }
}
