/* Buffer de cadena y conjunto de cadenas, compartidos por el piloto. */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "piloto.h"

/* ---------- buffer ---------- */

void buf_init(Buffer *b, size_t inicial) {
    if (inicial < 32) inicial = 32;
    b->datos = malloc(inicial);
    if (!b->datos) { fprintf(stderr, "sin memoria\n"); exit(1); }
    b->largo = 0;
    b->capacidad = inicial;
    b->datos[0] = '\0';
}

void buf_reservar(Buffer *b, size_t extra) {
    if (b->largo + extra + 1 <= b->capacidad) return;
    size_t nueva = b->capacidad ? b->capacidad : 256;
    while (nueva < b->largo + extra + 1) nueva *= 2;
    char *p = realloc(b->datos, nueva);
    if (!p) { fprintf(stderr, "sin memoria\n"); exit(1); }
    b->datos = p;
    b->capacidad = nueva;
}

void buf_add_n(Buffer *b, const char *s, size_t n) {
    if (!s || !n) return;
    buf_reservar(b, n);
    memcpy(b->datos + b->largo, s, n);
    b->largo += n;
    b->datos[b->largo] = '\0';
}

void buf_add(Buffer *b, const char *s) {
    if (!s) return;
    buf_add_n(b, s, strlen(s));
}

void buf_add_squashed(Buffer *b, const char *s) {
    if (!s) return;
    const char *linea = s;
    while (*linea) {
        const char *fin = strchr(linea, '\n');
        const char *tope = fin ? fin : linea + strlen(linea);
        const char *ini = linea;
        while (ini < tope && (*ini == ' ' || *ini == '\t' || *ini == '\r')) ini++;
        const char *ult = tope;
        while (ult > ini && (ult[-1] == ' ' || ult[-1] == '\t' || ult[-1] == '\r')) ult--;
        buf_add_n(b, ini, (size_t)(ult - ini));
        if (!fin) break;
        linea = fin + 1;
    }
}

/* len() de Python cuenta caracteres, no bytes: con diacriticos pali cada letra
 * ocupa dos bytes y strlen daria un largo mayor. */
size_t largo_utf8(const char *s) {
    size_t n = 0;
    for (; *s; s++) if ((*s & 0xC0) != 0x80) n++;
    return n;
}

/* ---------- conjunto de cadenas (encadenamiento simple) ---------- */

typedef struct Nodo { char *texto; struct Nodo *sig; } Nodo;

struct ConjuntoTexto {
    Nodo **cubos;
    size_t n_cubos;
};

static size_t huella(const char *s) {
    size_t h = 1469598103934665603u;
    for (; *s; s++) { h ^= (unsigned char)*s; h *= 1099511628211u; }
    return h;
}

ConjuntoTexto *conjunto_nuevo(size_t capacidad) {
    ConjuntoTexto *c = malloc(sizeof *c);
    if (capacidad < 64) capacidad = 64;
    c->n_cubos = capacidad;
    c->cubos = calloc(capacidad, sizeof *c->cubos);
    return c;
}

void conjunto_add(ConjuntoTexto *c, const char *s) {
    size_t i = huella(s) % c->n_cubos;
    for (Nodo *n = c->cubos[i]; n; n = n->sig)
        if (strcmp(n->texto, s) == 0) return;
    Nodo *n = malloc(sizeof *n);
    n->texto = strdup(s);
    n->sig = c->cubos[i];
    c->cubos[i] = n;
}

int conjunto_contiene(const ConjuntoTexto *c, const char *s) {
    if (!c || !s) return 0;
    size_t i = huella(s) % c->n_cubos;
    for (Nodo *n = c->cubos[i]; n; n = n->sig)
        if (strcmp(n->texto, s) == 0) return 1;
    return 0;
}

/* Array JSON de cadenas: ["a","b",...]. Deshace \" y \\ ; el resto va tal cual. */
ConjuntoTexto *conjunto_desde_json(const char *json) {
    ConjuntoTexto *c = conjunto_nuevo(1 << 16);
    if (!json) return c;
    const char *p = json;
    Buffer t; buf_init(&t, 256);
    while ((p = strchr(p, '"')) != NULL) {
        p++;
        t.largo = 0; t.datos[0] = '\0';
        while (*p && *p != '"') {
            if (*p == '\\' && p[1]) { p++; buf_add_n(&t, p, 1); p++; }
            else { buf_add_n(&t, p, 1); p++; }
        }
        if (!*p) break;
        p++;
        if (t.largo) conjunto_add(c, t.datos);
    }
    free(t.datos);
    return c;
}

void conjunto_liberar(ConjuntoTexto *c) {
    if (!c) return;
    for (size_t i = 0; i < c->n_cubos; i++) {
        Nodo *n = c->cubos[i];
        while (n) {
            Nodo *sig = n->sig;
            free(n->texto);
            free(n);
            n = sig;
        }
    }
    free(c->cubos);
    free(c);
}

/* ---------- lista de cadenas ---------- */

void lista_texto_init(Lista *l) {
    l->capacidad = 32;
    l->items = malloc(l->capacidad * sizeof *l->items);
    if (!l->items) { fprintf(stderr, "sin memoria\n"); exit(1); }
    l->n = 0;
}

void lista_texto_add(Lista *l, const char *s) {
    if (l->n == l->capacidad) {
        l->capacidad *= 2;
        char **p = realloc(l->items, l->capacidad * sizeof *l->items);
        if (!p) { fprintf(stderr, "sin memoria\n"); exit(1); }
        l->items = p;
    }
    l->items[l->n++] = strdup(s);
}

void lista_texto_add_unico(Lista *l, ConjuntoTexto *vistos, const char *s) {
    if (conjunto_contiene(vistos, s)) return;
    conjunto_add(vistos, s);
    lista_texto_add(l, s);
}

void lista_texto_liberar(Lista *l) {
    for (size_t i = 0; i < l->n; i++) free(l->items[i]);
    free(l->items);
    l->n = l->capacidad = 0;
    l->items = NULL;
}
