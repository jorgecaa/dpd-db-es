/* Piloto en C del exportador de GoldenDict — paso 2.
 *
 * Reproduce: la cabecera completa (dpd_header.jinja + CSSManager + las
 * propiedades needs_*_button) byte a byte, y un cuerpo aproximado.
 * Falta: la plantilla principal (1288 lineas, 286 construcciones de control),
 * la transliteracion, el minificado y el escritor de StarDict.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "sqlite3.h"
#include "piloto.h"
#include "trad_tabla.h"

/* envoltorio Rust de minify-html 0.18.1 (minificador/src/lib.rs) */
unsigned char *minificar(const unsigned char *e, size_t n, size_t *sal);
void minificar_liberar(unsigned char *p);
int MINIFICAR = 0;

/* Igual que render_pali_word_dpd_html: la cabecera ya viene aplastada; el
 * cuerpo (desde "<body>") pasa por minify. */
/* Minifica el buffer entero. Lo usan las entradas de raíces y EPD, que
 * ensamblan la cabecera aparte y pasan solo el cuerpo. */
void minificar_buffer(Buffer *b) {
    if (!MINIFICAR) return;
    size_t sal = 0;
    unsigned char *min = minificar((const unsigned char *)b->datos, b->largo, &sal);
    b->largo = 0;
    b->datos[0] = 0;
    buf_add_n(b, (const char *)min, sal);
    minificar_liberar(min);
}

static void minificar_entrada(Buffer *b) {
    if (!MINIFICAR) return;
    char *cuerpo = strstr(b->datos, "<body>");
    if (!cuerpo) return;
    size_t cab = (size_t)(cuerpo - b->datos);
    size_t sal = 0;
    unsigned char *min = minificar((const unsigned char *)cuerpo,
                                   b->largo - cab, &sal);
    b->largo = cab;
    b->datos[cab] = 0;
    buf_add_n(b, (const char *)min, sal);
    minificar_liberar(min);
}

enum {
    C_ID, C_LEMMA1, C_POS, C_GRAMMAR, C_MEANING1, C_MEANING_LIT, C_MEANING2,
    C_CONSTRUCTION, C_DERIVATIVE, C_SUFFIX, C_PHONETIC, C_COMPOUND_TYPE,
    C_COMPOUND_CONSTRUCTION, C_SANSKRIT, C_ROOT_KEY, C_ROOT_SIGN, C_ROOT_BASE,
    C_FAMILY_ROOT, C_FAMILY_WORD, C_FAMILY_COMPOUND,
    C_FAMILY_IDIOMS, C_FAMILY_SET,
    C_EXAMPLE1, C_SUTTA1, C_SOURCE1, C_EXAMPLE2, C_SUTTA2, C_SOURCE2,
    C_COMMENTARY, C_NOTES, C_ANTONYM, C_SYNONYM, C_VARIANT,
    C_INFLECTIONS_HTML, C_FREQ_DATA, C_PLUS_CASE, C_ORIGIN, C_NEG, C_VERB, C_TRANS,
    C_VAR_PHONETIC, C_VAR_TEXT, C_COGNATE, C_LINK, C_NON_IA, C_STEM, C_PATTERN,
    C_INFLECTIONS, C_INFL_API, C_INFL_SINHALA, C_INFL_DEVANAGARI, C_INFL_THAI
};

static const char *COLUMNAS =
    "id, lemma_1, pos, grammar, meaning_1, meaning_lit, meaning_2,"
    " construction, derivative, suffix, phonetic, compound_type,"
    " compound_construction, sanskrit, root_key, root_sign, root_base,"
    " family_root, family_word, family_compound,"
    " family_idioms, family_set,"
    " example_1, sutta_1, source_1, example_2, sutta_2, source_2,"
    " commentary, notes, antonym, synonym, variant, inflections_html, freq_data,"
    " plus_case, origin, neg, verb, trans, var_phonetic, var_text, cognate,"
    " link, non_ia, stem, pattern, inflections, inflections_api_ca_eva_iti,"
    " inflections_sinhala, inflections_devanagari, inflections_thai";

static const char *txt(sqlite3_stmt *st, int col) {
    const unsigned char *v = sqlite3_column_text(st, col);
    return v ? (const char *)v : "";
}

static void cargar_entrada(Entrada *e, sqlite3_stmt *st) {
    e->id = sqlite3_column_int(st, C_ID);
    e->lemma_1 = txt(st, C_LEMMA1);
    e->pos = txt(st, C_POS);
    e->grammar = txt(st, C_GRAMMAR);
    e->meaning_1 = txt(st, C_MEANING1);
    e->meaning_lit = txt(st, C_MEANING_LIT);
    e->meaning_2 = txt(st, C_MEANING2);
    e->construction = txt(st, C_CONSTRUCTION);
    e->derivative = txt(st, C_DERIVATIVE);
    e->suffix = txt(st, C_SUFFIX);
    e->phonetic = txt(st, C_PHONETIC);
    e->compound_type = txt(st, C_COMPOUND_TYPE);
    e->compound_construction = txt(st, C_COMPOUND_CONSTRUCTION);
    e->sanskrit = txt(st, C_SANSKRIT);
    e->root_key = txt(st, C_ROOT_KEY);
    e->root_sign = txt(st, C_ROOT_SIGN);
    e->root_base = txt(st, C_ROOT_BASE);
    /* hybrid_property root_family_key */
    if (*txt(st, C_ROOT_KEY) && *txt(st, C_FAMILY_ROOT))
        snprintf(e->root_family_key, sizeof e->root_family_key, "%s %s",
                 txt(st, C_ROOT_KEY), txt(st, C_FAMILY_ROOT));
    else
        e->root_family_key[0] = '\0';
    e->family_root = txt(st, C_FAMILY_ROOT);
    e->family_word = txt(st, C_FAMILY_WORD);
    e->family_compound = txt(st, C_FAMILY_COMPOUND);
    e->family_idioms = txt(st, C_FAMILY_IDIOMS);
    e->family_set = txt(st, C_FAMILY_SET);
    e->example_1 = txt(st, C_EXAMPLE1);
    e->sutta_1 = txt(st, C_SUTTA1);
    e->source_1 = txt(st, C_SOURCE1);
    e->example_2 = txt(st, C_EXAMPLE2);
    e->sutta_2 = txt(st, C_SUTTA2);
    e->source_2 = txt(st, C_SOURCE2);
    e->commentary = txt(st, C_COMMENTARY);
    e->notes = txt(st, C_NOTES);
    e->antonym = txt(st, C_ANTONYM);
    e->synonym = txt(st, C_SYNONYM);
    e->variant = txt(st, C_VARIANT);
    e->inflections_html = txt(st, C_INFLECTIONS_HTML);
    e->freq_data = txt(st, C_FREQ_DATA);
    e->plus_case = txt(st, C_PLUS_CASE);
    e->origin = txt(st, C_ORIGIN);
    e->inflections = txt(st, C_INFLECTIONS);
    e->inflections_api_ca_eva_iti = txt(st, C_INFL_API);
    e->inflections_sinhala = txt(st, C_INFL_SINHALA);
    e->inflections_devanagari = txt(st, C_INFL_DEVANAGARI);
    e->inflections_thai = txt(st, C_INFL_THAI);
    e->neg = txt(st, C_NEG);
    e->verb = txt(st, C_VERB);
    e->trans = txt(st, C_TRANS);
    e->var_phonetic = txt(st, C_VAR_PHONETIC);
    e->var_text = txt(st, C_VAR_TEXT);
    e->cognate = txt(st, C_COGNATE);
    e->link = txt(st, C_LINK);
    e->non_ia = txt(st, C_NON_IA);
    e->stem = txt(st, C_STEM);
    e->pattern = txt(st, C_PATTERN);
    /* make_lemma_trad_clean: stem + terminacion del patron, sin ! ni * */
    static char trad[512];
    trad[0] = '\0';
    if (!strchr(e->stem, '!')) {
        for (int i = 0; TRAD_TABLA[i][0]; i++) {
            if (strcmp(e->pattern, TRAD_TABLA[i][0]) == 0) {
                char bruto[512];
                snprintf(bruto, sizeof bruto, "%s%s", e->stem, TRAD_TABLA[i][1]);
                size_t k = 0;
                for (const char *q = bruto; *q; q++)
                    if (*q != '!' && *q != '*') trad[k++] = *q;
                trad[k] = '\0';
                break;
            }
        }
    }
    e->lemma_trad_clean = trad;
}


/* Cuerpo aproximado: pendiente de portar la plantilla principal. */
static sqlite3 *DB_GLOBAL;
static char FECHA_HOY[32];
static ConjuntoTexto *AUD_M1, *AUD_M2, *AUD_F1;
static int buscar_raiz(sqlite3 *db, const char *root_key, Raiz *rt,
                       char almacen[7][512]);

static void construir_cuerpo(Buffer *b, const Entrada *e, const Contexto *ctx) {
    /* Orden de la plantilla: resumen, botones, [sutta_info], gramatica,
     * ejemplos, flexiones, y las secciones de carga diferida. */
    buf_add(b, "<body>\n");
    construir_resumen(b, e);
    Sutta *su = sutta_cargar(DB_GLOBAL, e->lemma_1);
    construir_botones(b, e, ctx, su);
    buf_add(b, "\n");                   /* blanco tras la caja de botones */
    if (su) construir_sutta_info(b, su, e->id, e->lemma_1, FECHA_HOY);
    buf_add(b, "\n");                   /* blanco tras el bloque sutta_info */
    sutta_liberar(su);

    Raiz raiz; char alm[7][512];
    const Raiz *prt = NULL;
    if (*e->root_key && buscar_raiz(DB_GLOBAL, e->root_key, &raiz, alm)) prt = &raiz;
    char lim[256]; lemma_clean(e->lemma_1, lim, sizeof lim);
    Audio au = { conjunto_contiene(AUD_M1, lim), conjunto_contiene(AUD_M2, lim),
                 conjunto_contiene(AUD_F1, lim) };

    construir_gramatica(b, e, prt, &au, FECHA_HOY);
    construir_ejemplos(b, e, FECHA_HOY);
    construir_flexiones(b, e, FECHA_HOY);
    construir_secciones_carga(b, e, ctx);
    buf_add(b, "</body></html>");
}

static ConjuntoTexto *cargar_conjunto_db(sqlite3 *db, const char *clave) {
    sqlite3_stmt *st = NULL;
    ConjuntoTexto *c = NULL;
    if (sqlite3_prepare_v2(db, "SELECT value FROM db_info WHERE key = ?", -1, &st, NULL) == SQLITE_OK) {
        sqlite3_bind_text(st, 1, clave, -1, SQLITE_STATIC);
        if (sqlite3_step(st) == SQLITE_ROW)
            c = conjunto_desde_json((const char *)sqlite3_column_text(st, 0));
        sqlite3_finalize(st);
    }
    return c ? c : conjunto_nuevo(64);
}

static ConjuntoTexto *cargar_audio_set(const char *ruta_tsv) {
    ConjuntoTexto *c = conjunto_nuevo(1 << 15);
    AUD_M1 = conjunto_nuevo(1 << 15);
    AUD_M2 = conjunto_nuevo(1 << 15);
    AUD_F1 = conjunto_nuevo(1 << 15);
    FILE *f = fopen(ruta_tsv, "r");
    if (!f) return c;                    /* sin indice de audio, ningun boton */
    char linea[4096];
    int primera = 1;
    while (fgets(linea, sizeof linea, f)) {
        if (primera) { primera = 0; continue; }   /* cabecera del TSV */
        char *campos[4] = {0};
        int n = 0;
        for (char *t = strtok(linea, "\t\r\n"); t && n < 4; t = strtok(NULL, "\t\r\n"))
            campos[n++] = t;
        if (n < 4) continue;
        if (strcmp(campos[1], "1") == 0) conjunto_add(AUD_M1, campos[0]);
        if (strcmp(campos[2], "1") == 0) conjunto_add(AUD_M2, campos[0]);
        if (strcmp(campos[3], "1") == 0) conjunto_add(AUD_F1, campos[0]);
        if (strcmp(campos[1], "1") == 0 || strcmp(campos[2], "1") == 0 ||
            strcmp(campos[3], "1") == 0)
            conjunto_add(c, campos[0]);
    }
    fclose(f);
    return c;
}

static ConjuntoTexto *cargar_sutta_info_set(sqlite3 *db) {
    ConjuntoTexto *c = conjunto_nuevo(1 << 14);
    sqlite3_stmt *st = NULL;
    if (sqlite3_prepare_v2(db, "SELECT dpd_sutta, dpd_sutta_var FROM sutta_info", -1, &st, NULL) == SQLITE_OK) {
        while (sqlite3_step(st) == SQLITE_ROW) {
            const unsigned char *a = sqlite3_column_text(st, 0);
            if (a) conjunto_add(c, (const char *)a);
            const unsigned char *v = sqlite3_column_text(st, 1);
            if (v && *v) {
                char *copia = strdup((const char *)v), *g = NULL;
                for (char *t = strtok_r(copia, ";", &g); t; t = strtok_r(NULL, ";", &g)) {
                    while (*t == ' ') t++;
                    if (*t) conjunto_add(c, t);
                }
                free(copia);
            }
        }
        sqlite3_finalize(st);
    }
    return c;
}

static int buscar_raiz(sqlite3 *db, const char *root_key, Raiz *rt,
                       char almacen[7][512]) {
    static sqlite3_stmt *st = NULL;
    if (!st && sqlite3_prepare_v2(db,
            "SELECT root_has_verb, root_group, root_meaning, root_in_comps,"
            " sanskrit_root, sanskrit_root_class, sanskrit_root_meaning"
            " FROM dpd_roots WHERE root = ?", -1, &st, NULL) != SQLITE_OK)
        return 0;
    sqlite3_reset(st);
    sqlite3_bind_text(st, 1, root_key, -1, SQLITE_TRANSIENT);
    if (sqlite3_step(st) != SQLITE_ROW) return 0;
    for (int i = 0; i < 7; i++) {
        const unsigned char *v = sqlite3_column_text(st, i);
        snprintf(almacen[i], 512, "%s", v ? (const char *)v : "");
    }
    rt->root_has_verb = almacen[0];
    rt->root_group = almacen[1];
    rt->root_meaning = almacen[2];
    rt->root_in_comps = almacen[3];
    rt->sanskrit_root = almacen[4];
    rt->sanskrit_root_class = almacen[5];
    rt->sanskrit_root_meaning = almacen[6];
    return 1;
}

static long rss_pico_kb(void) {
    FILE *f = fopen("/proc/self/status", "r");
    if (!f) return -1;
    char linea[256]; long kb = -1;
    while (fgets(linea, sizeof linea, f))
        if (strncmp(linea, "VmHWM:", 6) == 0) { sscanf(linea + 6, "%ld", &kb); break; }
    fclose(f);
    return kb;
}

static void fecha_hoy(char *destino, size_t n) {
    time_t t = time(NULL);
    struct tm tm;
    localtime_r(&t, &tm);
    strftime(destino, n, "%Y-%m-%d", &tm);
}

int main(int argc, char **argv) {
    int exportar = (argc > 1 && strcmp(argv[1], "exportar") == 0);
    int volcarotros = (argc > 1 && strcmp(argv[1], "volcarotros") == 0);
    int volcado = (argc > 1 && (strcmp(argv[1], "volcado") == 0 ||
                                strcmp(argv[1], "volcadomin") == 0));
    MINIFICAR = (argc > 1 && strcmp(argv[1], "volcadomin") == 0) || exportar ||
                volcarotros ||
                getenv("PILOTO_MINIFICAR") != NULL;
    const char *ruta_db = (volcado || exportar || volcarotros) ? argv[2]
                                                : (argc > 1 ? argv[1] : "dpd.db");
    const char *dir = (volcado || exportar || volcarotros) ? argv[3] : NULL;
    const char *ruta_ids = volcado ? argv[4] : NULL;
    int n = exportar ? (argc > 4 ? atoi(argv[4]) : 0)
                     : volcado ? 0 : (argc > 2 ? atoi(argv[2]) : 2000);

    if (volcado && argc < 5) {
        fprintf(stderr, "uso: piloto volcado|volcadomin <db> <dir> <ids.txt>\n");
        return 2;
    }
    if (exportar && argc < 4) {
        fprintf(stderr, "uso: piloto exportar <db> <base_salida> [limite]\n");
        return 2;
    }

    cabecera_init("identity/css/dpd-variables.css");
    /* config_test("dictionary", "show_tbw", "yes"), como _worker_init */
    SHOW_TBW = 0;
    {
        FILE *cf = fopen("config.ini", "r");
        if (cf) {
            char linea[256];
            while (fgets(linea, sizeof linea, cf))
                if (strstr(linea, "show_tbw") && strstr(linea, "yes")) SHOW_TBW = 1;
            fclose(cf);
        }
    }

    sqlite3 *db = NULL;
    if (sqlite3_open_v2(ruta_db, &db, SQLITE_OPEN_READONLY, NULL) != SQLITE_OK) {
        fprintf(stderr, "no se pudo abrir %s\n", ruta_db);
        return 1;
    }
    DB_GLOBAL = db;
    ConjuntoTexto *idioms = cargar_conjunto_db(db, "idioms_set");
    ConjuntoTexto *cfs = cargar_conjunto_db(db, "cf_set");
    ConjuntoTexto *audio = cargar_audio_set("audio/db/dpd_audio_index.tsv");
    ConjuntoTexto *suttas = cargar_sutta_info_set(db);
    sutta_alias_cargar(db);
    ui_cargar("scripts/build/webapp_ui_es.tsv");
    espanol_cargar("dpd_es.db");
    otros_init("identity/css/dpd-variables.css");
    speech_marks_cargar("tools/speech_marks.json");
    Contexto ctx = { idioms, cfs, audio, suttas, NULL, NULL };

    char fecha[32];
    fecha_hoy(fecha, sizeof fecha);
    snprintf(FECHA_HOY, sizeof FECHA_HOY, "%s", fecha);

    char sql[2048];
    if (volcado)
        snprintf(sql, sizeof sql, "SELECT %s FROM dpd_headwords WHERE id = ?", COLUMNAS);
    else if (exportar && n == 0)
        snprintf(sql, sizeof sql, "SELECT %s FROM dpd_headwords ORDER BY lemma_1", COLUMNAS);
    else
        snprintf(sql, sizeof sql, "SELECT %s FROM dpd_headwords ORDER BY lemma_1 LIMIT ?", COLUMNAS);

    sqlite3_stmt *st = NULL;
    if (sqlite3_prepare_v2(db, sql, -1, &st, NULL) != SQLITE_OK) {
        fprintf(stderr, "sql: %s\n", sqlite3_errmsg(db));
        return 1;
    }

    if (volcarotros) {
        size_t nr = volcar_raices(DB_GLOBAL, dir, fecha, FECHA_HOY);
        size_t ne = volcar_epd(DB_GLOBAL, dir, 200);
        Diccionario *da = dict_nuevo(512);
        size_t na = exportar_ayuda(da, css_reducido("secondary"));
        for (size_t i = 0; i < dict_cuantos(da); i++) {
            const char *palabra, *datos; size_t largo;
            dict_articulo(da, i, &palabra, &datos, &largo);
            char nombre[512];
            size_t k = 0;
            for (const char *p = palabra; *p && k + 1 < sizeof nombre; p++)
                nombre[k++] = (*p == '/' || *p == ' ') ? '_' : *p;
            nombre[k] = '\0';
            char ruta[1024];
            snprintf(ruta, sizeof ruta, "%s/ayuda_%s.html", dir, nombre);
            FILE *fa = fopen(ruta, "wb");
            if (fa) { fwrite(datos, 1, largo, fa); fclose(fa); }
        }
        dict_liberar(da);
        printf("raíces: %zu\nepd: %zu (volcadas 200)\nayuda: %zu\n", nr, ne, na);
    } else if (exportar) {
        /* mismos valores que prepare_export_to_goldendict_mdict con la capa
         * española activa: es un diccionario distinto, no una recompilación */
        InfoDicc info = {
            .bookname = "Diccionario Pāḷi Digital",
            .author = "Bodhirasa",
            .description =
                "\n    <p>Digital Pāḷi Dictionary, de Bodhirasa</p>\n"
                "    <p>Capa en español. Para más información, visita\n"
                "    <a href=\"https://digitalpalidictionary.github.io\">\n"
                "    el sitio del Digital Pāḷi Dictionary</a></p>\n    ",
            .website = "https://digitalpalidictionary.github.io/",
            .source_lang = "pi",
            .target_lang = "es",
            .date = getenv("PILOTO_FECHA"),
        };
        sqlite3_bind_int(st, 1, n);
        Diccionario *dicc = dict_nuevo(90000);
        struct timespec t0, t1;
        clock_gettime(CLOCK_MONOTONIC, &t0);
        int i = 0;
        while ((n == 0 || i < n) && sqlite3_step(st) == SQLITE_ROW) {
            Entrada e; cargar_entrada(&e, st);
            Buffer b; buf_init(&b, 16384);
            construir_cabecera(&b, &e, fecha, idioms, cfs);
            construir_cuerpo(&b, &e, &ctx);
            minificar_entrada(&b);
            Sutta *su = sutta_cargar(DB_GLOBAL, e.lemma_1);
            size_t n_sin = 0;
            char **sins = construir_sinonimos(&e, su, &n_sin);
            sutta_liberar(su);
            dict_add(dicc, e.lemma_1, b.datos, b.largo, sins, n_sin);
            i++;
        }
        clock_gettime(CLOCK_MONOTONIC, &t1);
        double seg = (double)(t1.tv_sec - t0.tv_sec) +
                     (double)(t1.tv_nsec - t0.tv_nsec) / 1e9;
        printf("entradas:            %d\n", i);
        printf("renderizado:         %.3f s\n", seg);
        /* las otras entradas del diccionario, como en main.py cuando no hay
         * límite de datos: raíces y EPD */
        if (n == 0) {
            size_t n_raices = exportar_raices(DB_GLOBAL, dicc, fecha, FECHA_HOY);
            size_t n_epd = exportar_epd(DB_GLOBAL, dicc);
            size_t n_ayuda = exportar_ayuda(dicc, css_reducido("secondary"));
            printf("raíces:              %zu\n", n_raices);
            printf("epd:                 %zu\n", n_epd);
            printf("ayuda:               %zu\n", n_ayuda);
        }
        dict_escribir(dicc, dir, &info);
        printf("escrito:             %s.{ifo,idx,dict,syn}\n", dir);
        printf("RSS pico:            %.1f MB\n", (double)rss_pico_kb() / 1024.0);
        dict_liberar(dicc);
    } else if (volcado) {
        FILE *fids = fopen(ruta_ids, "r");
        if (!fids) { fprintf(stderr, "no se pudo leer %s\n", ruta_ids); return 1; }
        char linea[64];
        int escritas = 0;
        while (fgets(linea, sizeof linea, fids)) {
            int id = atoi(linea);
            if (id <= 0) continue;
            sqlite3_reset(st);
            sqlite3_bind_int(st, 1, id);
            if (sqlite3_step(st) != SQLITE_ROW) continue;
            Entrada e; cargar_entrada(&e, st);
            Buffer b; buf_init(&b, 16384);
            construir_cabecera(&b, &e, fecha, idioms, cfs);
            construir_cuerpo(&b, &e, &ctx);
            minificar_entrada(&b);
            char ruta[1024];
            snprintf(ruta, sizeof ruta, "%s/%d.html", dir, id);
            FILE *f = fopen(ruta, "w");
            if (f) { fwrite(b.datos, 1, b.largo, f); fclose(f); escritas++; }
            free(b.datos);
        }
        fclose(fids);
        printf("volcadas %d entradas en %s\n", escritas, dir);
    } else {
        sqlite3_bind_int(st, 1, n);
        Buffer *entradas = calloc((size_t)n, sizeof *entradas);
        struct timespec t0, t1;
        clock_gettime(CLOCK_MONOTONIC, &t0);
        int i = 0; long long bytes = 0;
        while (i < n && sqlite3_step(st) == SQLITE_ROW) {
            Entrada e; cargar_entrada(&e, st);
            buf_init(&entradas[i], 16384);
            construir_cabecera(&entradas[i], &e, fecha, idioms, cfs);
            construir_cuerpo(&entradas[i], &e, &ctx);
            minificar_entrada(&entradas[i]);
            bytes += (long long)entradas[i].largo;
            i++;
        }
        clock_gettime(CLOCK_MONOTONIC, &t1);
        double seg = (double)(t1.tv_sec - t0.tv_sec) + (double)(t1.tv_nsec - t0.tv_nsec) / 1e9;
        printf("entradas generadas:  %d\n", i);
        printf("tiempo:              %.3f s   (%.3f ms por entrada)\n", seg, 1000.0 * seg / (i ? i : 1));
        printf("HTML producido:      %.1f MB  (%.1f KB por entrada)\n",
               (double)bytes / 1048576.0, (double)bytes / 1024.0 / (i ? i : 1));
        printf("RSS pico:            %.1f MB\n", (double)rss_pico_kb() / 1024.0);
        for (int k = 0; k < i; k++) free(entradas[k].datos);
        free(entradas);
    }

    sqlite3_finalize(st);
    sqlite3_close(db);
    return 0;
}
