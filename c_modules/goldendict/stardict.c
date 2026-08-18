/* Escritor del formato StarDict.
 *
 * Reproduce lo que pyglossary escribe desde tools/goldendict_exporter.py con
 * `format="Stardict", sametypesequence="h", sqlite=True`:
 *
 *   .dict  definiciones concatenadas, sin separador (lo permite sametypesequence)
 *   .idx   por entrada: palabra \0 + desplazamiento be32 + tamaño be32
 *   .syn   por sinonimo: sinonimo \0 + indice be32 dentro del .idx
 *   .ifo   cabecera de clave=valor
 *
 * La colacion es la de pyglossary/sort_modules/stardict.py: la clave es
 * (bytes.lower(), bytes), donde lower() de Python sobre bytes solo pliega
 * A-Z. SQLite guarda esas claves como BLOB y las compara con memcmp, asi que
 * el desempate es por bytes crudos y, a igualdad de prefijo, gana la corta.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "piloto.h"

/* ---------- comparacion ---------- */

/* memcmp sobre el minimo comun y, si empatan, la mas corta primero: es como
 * SQLite compara dos BLOB. */
static int cmp_bytes(const char *a, size_t na, const char *b, size_t nb) {
    size_t n = na < nb ? na : nb;
    int r = memcmp(a, b, n);
    if (r) return r;
    if (na == nb) return 0;
    return na < nb ? -1 : 1;
}

/* bytes.lower() de Python: solo A-Z, byte a byte, sin tocar el resto */
static int cmp_bytes_minusculas(const char *a, size_t na, const char *b, size_t nb) {
    size_t n = na < nb ? na : nb;
    for (size_t i = 0; i < n; i++) {
        unsigned char ca = (unsigned char)a[i], cb = (unsigned char)b[i];
        if (ca >= 'A' && ca <= 'Z') ca += 32;
        if (cb >= 'A' && cb <= 'Z') cb += 32;
        if (ca != cb) return ca < cb ? -1 : 1;
    }
    if (na == nb) return 0;
    return na < nb ? -1 : 1;
}

/* la clave de pyglossary: (minusculas, crudo) */
static int cmp_clave(const char *a, size_t na, const char *b, size_t nb) {
    int r = cmp_bytes_minusculas(a, na, b, nb);
    if (r) return r;
    return cmp_bytes(a, na, b, nb);
}

/* ---------- acumulador de entradas ---------- */

typedef struct {
    char  *palabra;
    size_t palabra_n;
    char  *definicion;
    size_t definicion_n;
    char **sinonimos;
    size_t n_sinonimos;
} Articulo;

struct Diccionario {
    Articulo *articulos;
    size_t    n, capacidad;
};

Diccionario *dict_nuevo(size_t capacidad) {
    Diccionario *d = malloc(sizeof *d);
    if (!d) { fprintf(stderr, "sin memoria\n"); exit(1); }
    d->capacidad = capacidad < 16 ? 16 : capacidad;
    d->articulos = malloc(d->capacidad * sizeof *d->articulos);
    if (!d->articulos) { fprintf(stderr, "sin memoria\n"); exit(1); }
    d->n = 0;
    return d;
}

/* Se queda con `definicion` (no la copia): viene del buffer ya construido. */
void dict_add(Diccionario *d, const char *palabra, char *definicion,
              size_t definicion_n, char **sinonimos, size_t n_sinonimos) {
    if (d->n == d->capacidad) {
        d->capacidad *= 2;
        Articulo *p = realloc(d->articulos, d->capacidad * sizeof *d->articulos);
        if (!p) { fprintf(stderr, "sin memoria\n"); exit(1); }
        d->articulos = p;
    }
    Articulo *a = &d->articulos[d->n++];
    a->palabra = strdup(palabra);
    a->palabra_n = strlen(palabra);
    a->definicion = definicion;
    a->definicion_n = definicion_n;
    a->sinonimos = sinonimos;
    a->n_sinonimos = n_sinonimos;
}

size_t dict_cuantos(const Diccionario *d) { return d->n; }

/* ---------- escritura ---------- */

static int cmp_articulos(const void *x, const void *y) {
    const Articulo *a = x, *b = y;
    return cmp_clave(a->palabra, a->palabra_n, b->palabra, b->palabra_n);
}

typedef struct { const char *alt; size_t alt_n; unsigned indice; } Sinonimo;

static int cmp_sinonimos(const void *x, const void *y) {
    const Sinonimo *a = x, *b = y;
    int r = cmp_clave(a->alt, a->alt_n, b->alt, b->alt_n);
    if (r) return r;
    /* pyglossary ordena la tupla entera: a igualdad de texto, por indice */
    return a->indice < b->indice ? -1 : a->indice > b->indice ? 1 : 0;
}

static void escribir_be32(FILE *f, unsigned v) {
    unsigned char b[4] = {
        (unsigned char)(v >> 24), (unsigned char)(v >> 16),
        (unsigned char)(v >> 8),  (unsigned char)v,
    };
    fwrite(b, 1, 4, f);
}

static FILE *abrir(const char *base, const char *ext) {
    char ruta[1024];
    snprintf(ruta, sizeof ruta, "%s%s", base, ext);
    FILE *f = fopen(ruta, "wb");
    if (!f) { fprintf(stderr, "no se pudo escribir %s\n", ruta); exit(1); }
    return f;
}

/* El .ifo es de una línea por clave, así que pyglossary sustituye los saltos
 * por <br> antes de escribir un valor. */
static void escribir_valor_ifo(FILE *f, const char *valor) {
    for (const char *p = valor; *p; p++) {
        if (*p == '\n') fputs("<br>", f);
        else fputc(*p, f);
    }
}

/* La marca de tiempo del .ifo: make_timestamp() usa "%Y-%m-%dT%H:%M:%S". */
static void marca_de_tiempo(char *destino, size_t n) {
    time_t t = time(NULL);
    struct tm tm;
    localtime_r(&t, &tm);
    strftime(destino, n, "%Y-%m-%dT%H:%M:%S", &tm);
}

void dict_escribir(Diccionario *d, const char *base, const InfoDicc *info) {
    qsort(d->articulos, d->n, sizeof *d->articulos, cmp_articulos);

    /* .dict y .idx a la vez: el .idx necesita el desplazamiento del .dict */
    FILE *fdict = abrir(base, ".dict");
    FILE *fidx = abrir(base, ".idx");
    unsigned long desplazamiento = 0, idx_bytes = 0;
    for (size_t i = 0; i < d->n; i++) {
        const Articulo *a = &d->articulos[i];
        fwrite(a->definicion, 1, a->definicion_n, fdict);
        fwrite(a->palabra, 1, a->palabra_n, fidx);
        fputc(0, fidx);
        escribir_be32(fidx, (unsigned)desplazamiento);
        escribir_be32(fidx, (unsigned)a->definicion_n);
        idx_bytes += a->palabra_n + 1 + 8;
        desplazamiento += a->definicion_n;
    }
    fclose(fdict);
    fclose(fidx);

    /* .syn: todos los sinonimos de todas las entradas, ordenados */
    size_t total_sin = 0;
    for (size_t i = 0; i < d->n; i++) total_sin += d->articulos[i].n_sinonimos;

    unsigned long syn_escritos = 0;
    if (total_sin) {
        Sinonimo *sins = malloc(total_sin * sizeof *sins);
        if (!sins) { fprintf(stderr, "sin memoria\n"); exit(1); }
        size_t k = 0;
        for (size_t i = 0; i < d->n; i++) {
            const Articulo *a = &d->articulos[i];
            for (size_t j = 0; j < a->n_sinonimos; j++) {
                sins[k].alt = a->sinonimos[j];
                sins[k].alt_n = strlen(a->sinonimos[j]);
                sins[k].indice = (unsigned)i;
                k++;
            }
        }
        qsort(sins, total_sin, sizeof *sins, cmp_sinonimos);
        FILE *fsyn = abrir(base, ".syn");
        for (size_t i = 0; i < total_sin; i++) {
            fwrite(sins[i].alt, 1, sins[i].alt_n, fsyn);
            fputc(0, fsyn);
            escribir_be32(fsyn, sins[i].indice);
        }
        fclose(fsyn);
        syn_escritos = total_sin;
        free(sins);
    }

    /* .ifo — el orden de las claves es el que emite pyglossary */
    char fecha[32];
    marca_de_tiempo(fecha, sizeof fecha);
    FILE *fifo = abrir(base, ".ifo");
    fprintf(fifo, "StarDict's dict ifo file\n");
    fprintf(fifo, "version=3.0.0\n");
    fputs("bookname=", fifo);
    escribir_valor_ifo(fifo, info->bookname);
    fprintf(fifo, " (%s-%s)\n", info->source_lang, info->target_lang);
    fprintf(fifo, "wordcount=%zu\n", d->n);
    fprintf(fifo, "idxfilesize=%lu\n", idx_bytes);
    fprintf(fifo, "sametypesequence=h\n");
    if (syn_escritos) fprintf(fifo, "synwordcount=%lu\n", syn_escritos);
    fputs("author=", fifo); escribir_valor_ifo(fifo, info->author);
    fputc('\n', fifo);
    fputs("website=", fifo); escribir_valor_ifo(fifo, info->website);
    fputc('\n', fifo);
    fprintf(fifo, "date=%s\n", info->date && *info->date ? info->date : fecha);
    fputs("description=", fifo);
    escribir_valor_ifo(fifo, info->description);
    fputc('\n', fifo);
    fclose(fifo);
}

void dict_liberar(Diccionario *d) {
    if (!d) return;
    for (size_t i = 0; i < d->n; i++) {
        Articulo *a = &d->articulos[i];
        free(a->palabra);
        free(a->definicion);
        for (size_t j = 0; j < a->n_sinonimos; j++) free(a->sinonimos[j]);
        free(a->sinonimos);
    }
    free(d->articulos);
    free(d);
}

/* Acceso de solo lectura a un artículo ya acumulado, para el modo de volcado. */
void dict_articulo(const Diccionario *d, size_t i, const char **palabra,
                   const char **definicion, size_t *largo) {
    const Articulo *a = &d->articulos[i];
    *palabra = a->palabra;
    *definicion = a->definicion;
    *largo = a->definicion_n;
}
