/* Los sinónimos de cada entrada: lo que en StarDict acaba en el .syn.
 *
 * Reproduce el tramo final de render_pali_word_dpd_html:
 *
 *     synonyms = add_niggahitas(i.inflections_list_all)   <- deduplica
 *     synonyms += contracciones de speech_marks
 *     synonyms += sinhala + devanagari + thai + family_set_list + [id]
 *     synonyms += sutta_codes_list   (si needs_sutta_info_button)
 *
 * El orden de esa lista NO importa para la salida: pyglossary ordena el .syn.
 * Lo que sí importa es el multiconjunto exacto — `add_niggahitas` deduplica
 * con un set, pero lo que se añade después no, así que un mismo texto puede
 * aparecer dos veces y genera dos registros en el .syn.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "piloto.h"

/* Añade cada trozo separado por comas. Se emiten también los vacíos: el
 * split de Python sobre una columna vacía devuelve [""] y ese "" viaja hasta
 * la lista de sinónimos, así que omitirlo rompería la paridad. */
static void add_partido(Lista *l, const char *texto, const char *sep) {
    if (!texto) return;
    const char *ini = texto;
    size_t sn = strlen(sep);
    for (;;) {
        const char *p = strstr(ini, sep);
        size_t n = p ? (size_t)(p - ini) : strlen(ini);
        char *trozo = malloc(n + 1);
        memcpy(trozo, ini, n);
        trozo[n] = '\0';
        if (l->n == l->capacidad) {
            l->capacidad *= 2;
            char **q = realloc(l->items, l->capacidad * sizeof *l->items);
            if (!q) { fprintf(stderr, "sin memoria\n"); exit(1); }
            l->items = q;
        }
        l->items[l->n++] = trozo;
        if (!p) break;
        ini = p + sn;
    }
}

/* ---------- niggahitas ---------- */

/* Reemplaza todas las apariciones de `de` por `a`. */
static char *sustituir_todo(const char *s, const char *de, const char *a) {
    Buffer b;
    buf_init(&b, strlen(s) + 16);
    size_t nd = strlen(de);
    for (const char *p = s; *p;) {
        if (strncmp(p, de, nd) == 0) {
            buf_add(&b, a);
            p += nd;
        } else {
            buf_add_n(&b, p, 1);
            p++;
        }
    }
    return b.datos;
}

/* add_niggahitas: por cada palabra con ṃ añade su variante con ṁ y con ŋ, y
 * devuelve el conjunto (deduplicado). */
static void niggahitas_y_dedupe(Lista *l) {
    size_t original = l->n;
    for (size_t i = 0; i < original; i++) {
        if (!strstr(l->items[i], "ṃ")) continue;
        char *mi = sustituir_todo(l->items[i], "ṃ", "ṁ");
        lista_texto_add(l, mi);
        free(mi);
        char *ng = sustituir_todo(l->items[i], "ṃ", "ŋ");
        lista_texto_add(l, ng);
        free(ng);
    }
    /* list(set(words)) */
    ConjuntoTexto *vistos = conjunto_nuevo(1 << 12);
    size_t escritos = 0;
    for (size_t i = 0; i < l->n; i++) {
        if (conjunto_contiene(vistos, l->items[i])) {
            free(l->items[i]);
            continue;
        }
        conjunto_add(vistos, l->items[i]);
        l->items[escritos++] = l->items[i];
    }
    l->n = escritos;
    conjunto_liberar(vistos);
}

/* ---------- speech marks ---------- */

/* tools/speech_marks.json: palabra -> lista de formas. Solo interesan las que
 * llevan apóstrofo, que son las contracciones. */
typedef struct Marca { char *palabra; Lista formas; struct Marca *sig; } Marca;
static Marca *marcas[1 << 14];
static int marcas_cargadas = 0;

static size_t huella_marca(const char *s) {
    size_t h = 1469598103934665603u;
    for (; *s; s++) { h ^= (unsigned char)*s; h *= 1099511628211u; }
    return h % (1 << 14);
}

/* Lector mínimo del JSON: {"clave": ["a", "b"], ...} con escapes \" y \\. */
static const char *leer_cadena_json(const char *p, Buffer *destino) {
    destino->largo = 0;
    destino->datos[0] = '\0';
    while (*p && *p != '"') {
        if (*p == '\\' && p[1]) {
            p++;
            buf_add_n(destino, p, 1);
            p++;
        } else {
            buf_add_n(destino, p, 1);
            p++;
        }
    }
    return p;
}

void speech_marks_cargar(const char *ruta) {
    FILE *f = fopen(ruta, "rb");
    if (!f) return;                       /* sin marcas: no hay contracciones */
    fseek(f, 0, SEEK_END);
    long tam = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *json = malloc((size_t)tam + 1);
    size_t leidos = fread(json, 1, (size_t)tam, f);
    json[leidos] = '\0';
    fclose(f);

    Buffer texto;
    buf_init(&texto, 256);
    const char *p = json;
    while ((p = strchr(p, '"')) != NULL) {
        p = leer_cadena_json(p + 1, &texto);
        if (!*p) break;
        p++;
        char *clave = strdup(texto.datos);
        while (*p == ' ' || *p == ':' || *p == '\n' || *p == '\r' || *p == '\t') p++;
        if (*p != '[') { free(clave); continue; }
        p++;
        Marca *m = malloc(sizeof *m);
        m->palabra = clave;
        lista_texto_init(&m->formas);
        while (*p && *p != ']') {
            if (*p == '"') {
                p = leer_cadena_json(p + 1, &texto);
                lista_texto_add(&m->formas, texto.datos);
                if (*p) p++;
            } else {
                p++;
            }
        }
        if (*p) p++;
        size_t h = huella_marca(m->palabra);
        m->sig = marcas[h];
        marcas[h] = m;
    }
    free(texto.datos);
    free(json);
    marcas_cargadas = 1;
}

static const Marca *marca_de(const char *palabra) {
    if (!marcas_cargadas) return NULL;
    for (Marca *m = marcas[huella_marca(palabra)]; m; m = m->sig)
        if (strcmp(m->palabra, palabra) == 0) return m;
    return NULL;
}

/* ---------- construcción ---------- */

char **construir_sinonimos(const Entrada *e, const Sutta *su, size_t *cuantos) {
    Lista l;
    lista_texto_init(&l);

    /* inflections_list_all: las dos columnas, partidas por coma */
    add_partido(&l, e->inflections, ",");
    add_partido(&l, e->inflections_api_ca_eva_iti, ",");
    niggahitas_y_dedupe(&l);

    /* contracciones: una pasada sobre la lista base ya deduplicada */
    size_t base = l.n;
    for (size_t i = 0; i < base; i++) {
        const Marca *m = marca_de(l.items[i]);
        if (!m) continue;
        for (size_t j = 0; j < m->formas.n; j++)
            if (strchr(m->formas.items[j], '\''))
                lista_texto_add(&l, m->formas.items[j]);
    }

    if (e->inflections_sinhala && *e->inflections_sinhala)
        add_partido(&l, e->inflections_sinhala, ",");
    if (e->inflections_devanagari && *e->inflections_devanagari)
        add_partido(&l, e->inflections_devanagari, ",");
    if (e->inflections_thai && *e->inflections_thai)
        add_partido(&l, e->inflections_thai, ",");

    if (e->family_set && *e->family_set) add_partido(&l, e->family_set, "; ");

    char id[32];
    snprintf(id, sizeof id, "%d", e->id);
    lista_texto_add(&l, id);

    if (su) {
        size_t n = 0;
        char **codigos = sutta_codes_list(su, &n);
        for (size_t i = 0; i < n; i++) {
            lista_texto_add(&l, codigos[i]);
            free(codigos[i]);
        }
        free(codigos);
    }

    *cuantos = l.n;
    return l.items;
}
