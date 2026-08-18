/* Sub-paso 3c: el parrafo de resumen, con la capa española.
 *
 * Reproduce:
 *   - tools/spanish.py: overlay() y fingerprint_matches() sobre dpd_es.db
 *   - tools/meaning_construction.py: make_meaning_combo_html, summarize_construction
 *   - tools/degree_of_completion.py: degree_of_completion
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sqlite3.h"
#include "piloto.h"

/* ---------- la base auxiliar de significados ---------- */

typedef struct Trad {
    char *lemma_1, *meaning_1, *meaning_lit, *meaning_2, *pos, *construction;
    struct Trad *sig;
} Trad;

static Trad *tabla_es[1 << 17];
static int hay_es = 0;

static size_t huella_es(const char *s) {
    size_t h = 1469598103934665603u;
    for (; *s; s++) { h ^= (unsigned char)*s; h *= 1099511628211u; }
    return h % (1 << 17);
}

/* roots_es: la huella es el propio ingles almacenado (overlay_root). */
typedef struct Raz { char *root, *es, *en; struct Raz *sig; } Raz;
static Raz *tabla_raices[1 << 13];
static size_t huella_raiz(const char *s) {
    size_t h = 1469598103934665603u;
    for (; *s; s++) { h ^= (unsigned char)*s; h *= 1099511628211u; }
    return h % (1 << 13);
}
const char *superponer_raiz(const char *root, const char *ingles) {
    if (!root || !*root) return ingles;
    for (Raz *r = tabla_raices[huella_raiz(root)]; r; r = r->sig)
        if (strcmp(r->root, root) == 0)
            return (*r->es && strcmp(r->en, ingles) == 0) ? r->es : ingles;
    return ingles;
}

/* sanskrit_es: se indexa por la propia frase inglesa. */
typedef struct San { char *en, *es; struct San *sig; } San;
static San *tabla_san[1 << 12];
static size_t huella_san(const char *s) {
    size_t h = 1469598103934665603u;
    for (; *s; s++) { h ^= (unsigned char)*s; h *= 1099511628211u; }
    return h % (1 << 12);
}
const char *superponer_sanscrito(const char *ingles) {
    if (!ingles || !*ingles) return ingles;
    for (San *s = tabla_san[huella_san(ingles)]; s; s = s->sig)
        if (strcmp(s->en, ingles) == 0) return *s->es ? s->es : ingles;
    return ingles;
}

static char *dup_col(sqlite3_stmt *st, int c) {
    const unsigned char *v = sqlite3_column_text(st, c);
    return strdup(v ? (const char *)v : "");
}

void espanol_cargar(const char *ruta_sidecar) {
    sqlite3 *db = NULL;
    if (sqlite3_open_v2(ruta_sidecar, &db, SQLITE_OPEN_READONLY, NULL) != SQLITE_OK)
        return;                                  /* sin sidecar: todo en ingles */
    sqlite3_stmt *st = NULL;
    if (sqlite3_prepare_v2(db,
            "SELECT lemma_1, meaning_1, meaning_lit, meaning_2, pos, construction"
            " FROM meanings_es", -1, &st, NULL) == SQLITE_OK) {
        while (sqlite3_step(st) == SQLITE_ROW) {
            Trad *t = malloc(sizeof *t);
            t->lemma_1 = dup_col(st, 0);
            t->meaning_1 = dup_col(st, 1);
            t->meaning_lit = dup_col(st, 2);
            t->meaning_2 = dup_col(st, 3);
            t->pos = dup_col(st, 4);
            t->construction = dup_col(st, 5);
            size_t i = huella_es(t->lemma_1);
            t->sig = tabla_es[i];
            tabla_es[i] = t;
            hay_es = 1;
        }
        sqlite3_finalize(st);
    }
    if (sqlite3_prepare_v2(db,
            "SELECT root, root_meaning, root_meaning_en FROM roots_es",
            -1, &st, NULL) == SQLITE_OK) {
        while (sqlite3_step(st) == SQLITE_ROW) {
            Raz *r = malloc(sizeof *r);
            r->root = dup_col(st, 0);
            r->es = dup_col(st, 1);
            r->en = dup_col(st, 2);
            size_t i = huella_raiz(r->root);
            r->sig = tabla_raices[i];
            tabla_raices[i] = r;
        }
        sqlite3_finalize(st);
    }
    if (sqlite3_prepare_v2(db, "SELECT meaning_en, meaning_es FROM sanskrit_es",
                           -1, &st, NULL) == SQLITE_OK) {
        while (sqlite3_step(st) == SQLITE_ROW) {
            San *s = malloc(sizeof *s);
            s->en = dup_col(st, 0);
            s->es = dup_col(st, 1);
            size_t i = huella_san(s->en);
            s->sig = tabla_san[i];
            tabla_san[i] = s;
        }
        sqlite3_finalize(st);
    }
    sqlite3_close(db);
}

static const Trad *buscar_es(const char *lemma_1) {
    if (!hay_es) return NULL;
    for (Trad *t = tabla_es[huella_es(lemma_1)]; t; t = t->sig)
        if (strcmp(t->lemma_1, lemma_1) == 0) return t;
    return NULL;
}

/* ---------- summarize_construction, con las regex a mano ---------- */

/* re.sub(r"\n.+$", "", s) : corta en el primer salto de linea */
static void corta_en_salto(char *s) {
    char *n = strchr(s, '\n');
    if (n) *n = '\0';
}

/* re.sub("> .[^ ]*? ", "", s) — no voraz: "> " + un caracter + lo minimo
 * hasta el siguiente espacio, incluido. */
static void quita_cambios_foneticos(char *s) {
    char *p = s;
    while ((p = strstr(p, "> ")) != NULL) {
        char *q = p + 2;
        if (!*q) break;
        q++;                                   /* el "." de la regex */
        while (*q && *q != ' ') q++;
        if (*q == ' ') {
            q++;
            memmove(p, q, strlen(q) + 1);
        } else {
            p += 2;
        }
    }
}

/* re.sub(" > .[^ ]*?$", "", s) : cambio fonetico al final */
static void quita_fonetico_final(char *s) {
    char *p = NULL, *q = s;
    while ((q = strstr(q, " > ")) != NULL) { p = q; q += 3; }
    if (!p) return;
    const char *r = p + 3;
    if (!*r) return;
    r++;
    while (*r && *r != ' ') r++;
    if (!*r) *p = '\0';                        /* llegaba hasta el final */
}

static void quita_parentesis(Buffer *out, const char *s) {
    for (; *s; s++) if (*s != '(' && *s != ')') buf_add_n(out, s, 1);
}

/* re.sub(r"^\[.*\] \+| \[.*\] \+| \+ \[.*\]$", "", s) : quita insertos */
static void quita_insertos(char *s) {
    /* " + [...]" al final */
    char *p = NULL, *q = s;
    while ((q = strstr(q, " + [")) != NULL) { p = q; q += 4; }
    if (p && s[strlen(s) - 1] == ']') *p = '\0';
    /* "[...] + " al principio */
    if (s[0] == '[') {
        char *cierre = strstr(s, "] + ");
        if (cierre) memmove(s, cierre + 4, strlen(cierre + 4) + 1);
    }
    /* " [...] +" intermedio */
    char *ini;
    while ((ini = strstr(s, " [")) != NULL) {
        char *fin = strstr(ini, "] +");
        if (!fin) break;
        memmove(ini, fin + 3, strlen(fin + 3) + 1);
    }
}

/* re.sub(r" \d.*$", "", s) */
static void quita_numero_final(char *s) {
    size_t n = strlen(s);
    for (size_t i = 0; i + 1 < n; i++)
        if (s[i] == ' ' && s[i + 1] >= '0' && s[i + 1] <= '9') { s[i] = '\0'; return; }
}

void construction_summary(const Entrada *e, const char *meaning_1_en,
                          Buffer *out) {
    /* construction sin las etiquetas de negrita */
    Buffer c; buf_init(&c, 512);
    for (const char *p = e->construction; *p; ) {
        if (strncmp(p, "<b>", 3) == 0) { p += 3; continue; }
        if (strncmp(p, "</b>", 4) == 0) { p += 4; continue; }
        buf_add_n(&c, p, 1); p++;
    }

    int es_pass = (strcmp(e->origin, "pass1") == 0 || strcmp(e->origin, "pass2") == 0);

    if (!*meaning_1_en && !es_pass) {
        if (*e->root_key) {
            for (const char *p = e->family_root; *p; p++) {
                if (*p == ' ') buf_add(out, " + "); else buf_add_n(out, p, 1);
            }
        } else if (*e->family_word) {
            buf_add(out, e->family_word);
        }
        free(c.datos);
        return;
    }

    if (!*meaning_1_en && !es_pass) { free(c.datos); return; }
    if (!*e->construction) { free(c.datos); return; }

    corta_en_salto(c.datos);
    quita_cambios_foneticos(c.datos);
    quita_fonetico_final(c.datos);
    Buffer sin_par; buf_init(&sin_par, c.largo + 8);
    quita_parentesis(&sin_par, c.datos);
    quita_insertos(sin_par.datos);

    if (!*e->root_base) {
        buf_add(out, sin_par.datos);
    } else {
        /* base_clean = root_base sin " (...)" final */
        char base_clean[512];
        snprintf(base_clean, sizeof base_clean, "%s", e->root_base);
        char *par = strstr(base_clean, " (");
        if (par && base_clean[strlen(base_clean) - 1] == ')') *par = '\0';

        /* base = ultimo token de base_clean */
        const char *base = strrchr(base_clean, ' ');
        base = base ? base + 1 : base_clean;

        /* base_construction = base_clean hasta " > ", y luego sin " >..." */
        char base_con[512];
        snprintf(base_con, sizeof base_con, "%s", base_clean);
        char *mayor = strstr(base_con, " > ");
        if (mayor) {
            /* re.sub("(.+)( > .+?$)", "\\1", ...) : corta en el ULTIMO " > " */
            char *ult = NULL, *q = base_con;
            while ((q = strstr(q, " > ")) != NULL) { ult = q; q += 3; }
            if (ult) *ult = '\0';
        }
        char *mayor2 = strstr(base_con, " >");
        if (mayor2) *mayor2 = '\0';

        char sustituto[1024];
        if (strcmp(e->pos, "fut") != 0) {
            Buffer rs; buf_init(&rs, 128);
            for (const char *p = e->root_sign; *p; p++) {
                if (*p == ' ') buf_add(&rs, " + "); else buf_add_n(&rs, p, 1);
            }
            char root_limpio[256];
            snprintf(root_limpio, sizeof root_limpio, "%s", e->root_key);
            quita_numero_final(root_limpio);
            snprintf(sustituto, sizeof sustituto, "%s + %s", root_limpio, rs.datos);
            free(rs.datos);
        } else {
            snprintf(sustituto, sizeof sustituto, "%s", base_con);
        }

        /* re.sub(base, sustituto, construction) : primera aparicion literal */
        char *pos = strstr(sin_par.datos, base);
        if (pos && *base) {
            buf_add_n(out, sin_par.datos, (size_t)(pos - sin_par.datos));
            buf_add(out, sustituto);
            buf_add(out, pos + strlen(base));
        } else {
            buf_add(out, sin_par.datos);
        }
    }
    free(sin_par.datos);
    free(c.datos);
}

/* ---------- la superposicion española ---------- */

/* fingerprint_matches: la parte del discurso debe coincidir siempre; la
 * construccion solo se compara cuando ambos lados la tienen. */
static int huella_coincide(const Entrada *e, const Trad *t) {
    if (strcmp(t->pos, e->pos) != 0) return 0;
    if (!*t->construction) return 1;
    Buffer cs; buf_init(&cs, 256);
    construction_summary(e, e->meaning_1, &cs);   /* con el ingles, "suppressed" */
    int igual = (strcmp(t->construction, cs.datos) == 0);
    free(cs.datos);
    return igual || !cs.largo;
}

/* overlay(headword, field, english) */
static const char *superponer(const Trad *t,
                              const char *campo, const char *ingles) {
    if (!t || !*ingles) return ingles;
    const char *es = NULL;
    if (strcmp(campo, "meaning_1") == 0) es = t->meaning_1;
    else if (strcmp(campo, "meaning_lit") == 0) es = t->meaning_lit;
    else if (strcmp(campo, "meaning_2") == 0) es = t->meaning_2;
    return (es && *es) ? es : ingles;
}

/* ---------- el parrafo de resumen ---------- */

void construir_resumen(Buffer *b, const Entrada *e) {
    const Trad *t = buscar_es(e->lemma_1);
    if (t && !huella_coincide(e, t)) t = NULL;

    const char *m1 = superponer(t, "meaning_1", e->meaning_1);
    const char *mlit = superponer(t, "meaning_lit", e->meaning_lit);
    const char *m2 = superponer(t, "meaning_2", e->meaning_2);

    buf_add(b, "<div class=\"dpd\">\n    <p>\n        ");
    buf_add(b, e->pos); buf_add(b, ". \n");

    if (*e->plus_case) {
        buf_add(b, "        ("); buf_add(b, e->plus_case); buf_add(b, ") \n");
    }

    /* make_meaning_combo_html */
    buf_add(b, "        ");
    if (*m1) {
        buf_add(b, "<b>"); buf_add(b, m1); buf_add(b, "</b>");
        if (*mlit) { buf_add(b, "; lit. "); buf_add(b, mlit); }
    } else if (strstr(m2, "; lit.")) {
        buf_add(b, m2);
    } else if (*mlit) {
        buf_add(b, m2); buf_add(b, "; lit. "); buf_add(b, mlit);
    } else {
        buf_add(b, m2);
    }
    buf_add(b, " \n");

    Buffer cs; buf_init(&cs, 256);
    construction_summary(e, e->meaning_1, &cs);
    if (cs.largo) {
        buf_add(b, "        ["); buf_add(b, cs.datos); buf_add(b, "]\n");
    }
    free(cs.datos);

    /* degree_of_completion_html */
    buf_add(b, "        <span class=\"gray\">");
    if (*e->meaning_1) buf_add(b, *e->source_1 ? "✔" : "◑");
    else buf_add(b, "✘");
    buf_add(b, "</span> \n    </p>\n</div>\n");
}
