/* Paso 3 del piloto: el cuerpo de la entrada.
 * Sub-paso 3a: la caja de botones (13,7 % del cuerpo, presente en todas las
 * entradas). Incluye la traduccion de las etiquetas, porque el exportador de
 * esta maquina lleva aplicada la capa española.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "piloto.h"

/* ---------- tabla de traduccion de la interfaz ---------- */

typedef struct Par { char *en; char *es; struct Par *sig; } Par;
static Par *ui[1024];

static size_t huella_ui(const char *s) {
    size_t h = 1469598103934665603u;
    for (; *s; s++) { h ^= (unsigned char)*s; h *= 1099511628211u; }
    return h % 1024;
}

void ui_cargar(const char *ruta_tsv) {
    FILE *f = fopen(ruta_tsv, "r");
    if (!f) return;                     /* sin tabla, se queda en ingles */
    char linea[4096];
    while (fgets(linea, sizeof linea, f)) {
        char *tab = strchr(linea, '\t');
        if (!tab) continue;
        *tab = '\0';
        char *es = tab + 1;
        char *nl = strpbrk(es, "\r\n");
        if (nl) *nl = '\0';
        if (!*linea || !*es) continue;
        size_t i = huella_ui(linea);
        Par *p = malloc(sizeof *p);
        p->en = strdup(linea); p->es = strdup(es); p->sig = ui[i];
        ui[i] = p;
    }
    fclose(f);
}

/* Traduce una etiqueta de interfaz; si no esta en la tabla, la deja igual. */
const char *T(const char *en) {
    for (Par *p = ui[huella_ui(en)]; p; p = p->sig)
        if (strcmp(p->en, en) == 0) return p->es;
    return en;
}

/* ---------- conjuntos de partes del discurso ---------- */

static const char *CONJUGACIONES[] = {
    "aor","cond","fut","imp","imperf","opt","perf","pr", NULL };
static const char *DECLINACIONES[] = {
    "adj","card","cs","fem","letter","masc","nt","ordin","pp","pron","prp",
    "ptp","root","ve", NULL };

static int en_lista(const char *s, const char **lista) {
    for (int i = 0; lista[i]; i++) if (strcmp(s, lista[i]) == 0) return 1;
    return 0;
}

/* ---------- la caja de botones ---------- */

static void boton(Buffer *b, const char *destino, const char *lemma_,
                  const char *etiqueta) {
    buf_add(b, "    <a class=\"dpd-button\" href=\"#\" data-target=\"");
    buf_add(b, destino); buf_add(b, "_"); buf_add(b, lemma_);
    buf_add(b, "\">"); buf_add(b, T(etiqueta)); buf_add(b, "</a>\n");
}

void construir_botones(Buffer *b, const Entrada *e, const Contexto *ctx,
                       const Sutta *su) {
    char limpio[256];
    lemma_clean(e->lemma_1, limpio, sizeof limpio);

    /* lemma_1_ : espacios y puntos a guion bajo */
    char lemma_[512];
    size_t k = 0;
    for (const char *p = e->lemma_1; *p && k + 1 < sizeof lemma_; p++)
        lemma_[k++] = (*p == ' ' || *p == '.') ? '_' : *p;
    lemma_[k] = '\0';

    int hay_significado = e->meaning_1 && *e->meaning_1;

    buf_add(b, "<div class=\"button-box\">\n");

    if (conjunto_contiene(ctx->audio_set, limpio)) {
        buf_add(b, "    <a class=\"dpd-button play\" onclick=\"playAudio('");
        buf_add(b, limpio);
        buf_add(b, "', this)\" title=\"");
        buf_add(b, T("Play Audio"));
        buf_add(b, "\"><svg viewBox=\"0 0 24 24\" width=\"16px\" height=\"16px\""
                   " fill=\"currentColor\" stroke=\"currentColor\""
                   " stroke-width=\"0\"><path d=\"M8 5v14l11-7z\"></path></svg></a>\n");
    }

    if (su) {
        buf_add(b, "    <a class=\"dpd-button\" href=\"#\" data-target=\"sutta_info_");
        buf_add(b, lemma_); buf_add(b, "\">");
        if (sutta_es_samyutta(su)) buf_add(b, T("saṃyutta"));
        else if (sutta_es_pannasaka(su)) buf_add(b, T("paṇṇāsaka"));
        else if (sutta_es_vagga(su)) buf_add(b, T("vagga"));
        else if (sutta_es_nipata(su)) buf_add(b, T("nipāta"));
        else buf_add(b, T("sutta"));
        buf_add(b, "</a>\n");
    }

    if (hay_significado) boton(b, "grammar", lemma_, "grammar");

    int ej1 = e->example_1 && *e->example_1;
    int ej2 = e->example_2 && *e->example_2;
    if (hay_significado && ej1 && !ej2) boton(b, "example", lemma_, "example");
    if (hay_significado && ej1 && ej2)  boton(b, "examples", lemma_, "examples");

    if (en_lista(e->pos, CONJUGACIONES)) boton(b, "conjugation", lemma_, "conjugation");
    if (en_lista(e->pos, DECLINACIONES)) boton(b, "declension", lemma_, "declension");

    if (e->family_root && *e->family_root) boton(b, "family_root", lemma_, "root family");
    if (e->family_word && *e->family_word) boton(b, "family_word", lemma_, "word family");

    /* familias de compuestos: misma condicion que en la cabecera */
    int es_sandhi = strstr(e->pos, "sandhi") != NULL;
    int es_idiom = strstr(e->pos, "idiom") != NULL;
    int en_cf = 0;
    if (e->family_compound && *e->family_compound) {
        char *copia = strdup(e->family_compound), *g = NULL;
        for (char *t = strtok_r(copia, " ", &g); t; t = strtok_r(NULL, " ", &g))
            if (conjunto_contiene(ctx->cf_set, t)) { en_cf = 1; break; }
        free(copia);
    } else {
        en_cf = conjunto_contiene(ctx->cf_set, limpio);
    }
    int fc_una = hay_significado && !strchr(e->family_compound, ' ')
                 && !es_sandhi && !es_idiom
                 && !strchr(e->compound_type, '?') && en_cf;
    int fc_varias = hay_significado && strchr(e->family_compound, ' ')
                    && !es_sandhi && !es_idiom && largo_utf8(limpio) < 30 && en_cf;
    if (fc_una)        boton(b, "family_compound", lemma_, "compound family");
    else if (fc_varias) boton(b, "family_compound", lemma_, "compound families");

    int idioms = 0;
    if (hay_significado) {
        if (e->family_idioms && *e->family_idioms) {
            char *copia = strdup(e->family_idioms), *g = NULL;
            for (char *t = strtok_r(copia, " ", &g); t; t = strtok_r(NULL, " ", &g))
                if (conjunto_contiene(ctx->idioms_set, t)) { idioms = 1; break; }
            free(copia);
        } else {
            idioms = conjunto_contiene(ctx->idioms_set, limpio);
        }
    }
    if (idioms) boton(b, "family_idiom", lemma_, "idioms");

    int n_sets = 0;
    if (e->family_set && *e->family_set) {
        char *copia = strdup(e->family_set), *g = NULL;
        for (char *t = strtok_r(copia, ";", &g); t; t = strtok_r(NULL, ";", &g)) n_sets++;
        free(copia);
    }
    if (hay_significado && n_sets == 1) boton(b, "family_set", lemma_, "set");
    else if (hay_significado && n_sets > 1) boton(b, "family_set", lemma_, "sets");

    static const char *EXCLUIDAS[] = {"abbrev","cs","idiom","letter","prefix",
                                      "root","suffix","ve", NULL};
    if (!en_lista(e->pos, EXCLUIDAS)) boton(b, "frequency", lemma_, "frequency");

    boton(b, "feedback", lemma_, "feedback");

    buf_add(b, "</div>\n");
}

/* ---------- sub-paso 3b: declinacion / conjugacion (43 % de los bytes) ---------- */

static void lemma_link(const char *lemma, Buffer *b) {
    for (const char *p = lemma; *p; p++) {
        if (*p == ' ') buf_add(b, "%20");
        else buf_add_n(b, p, 1);
    }
}

void construir_flexiones(Buffer *b, const Entrada *e, const char *hoy) {
    int decl = en_lista(e->pos, DECLINACIONES);
    int conj = en_lista(e->pos, CONJUGACIONES);
    if (!decl && !conj) { buf_add(b, "\n"); return; }

    char lemma_[512];
    size_t k = 0;
    for (const char *p = e->lemma_1; *p && k + 1 < sizeof lemma_; p++)
        lemma_[k++] = (*p == ' ' || *p == '.') ? '_' : *p;
    lemma_[k] = '\0';

    buf_add(b, decl ? "<div id=\"declension_" : "    <div id=\"conjugation_");
    buf_add(b, lemma_);
    buf_add(b, "\" class=\"dpd content hidden\">\n        ");
    buf_add(b, e->inflections_html);

    int hay_gris = strstr(e->inflections_html, "gray") != NULL;
    if (hay_gris) {
        buf_add(b, "\n        <p>\n            ");
        buf_add(b, T("Inflections not found in any Pāḷi corpus, or within processed sandhi compounds are"));
        buf_add(b, " <span class='gray'>");
        buf_add(b, T("grayed out"));
        buf_add(b, "</span>");
        buf_add(b, T(". They might still occur within compounds."));
        buf_add(b, "\n        </p>\n        <p class='dpd-footer'>\n                ");
    } else {
        buf_add(b, "\n            <p>\n                ");
    }

    buf_add(b, T(decl
        ? "Did you spot a mistake in the declension table? Something missing?"
        : "Did you spot a mistake in the conjugation table? Something missing?"));
    buf_add(b, " <a class=\"dpd-link\"\n                    href=\"https://docs.google.com/forms/d/e/");
    buf_add(b, decl ? "1FAIpQLSfKUBx-icfRCWmhHqUwzX60BVQE21s_NERNfU2VvbjEfE371A"
                    : "1FAIpQLSdAL2PzavyrtXgGmtNrZAMyh3hV6g3fU0chxhWFxunQEZtH0g");
    buf_add(b, "/viewform?usp=pp_url&entry.");
    buf_add(b, decl ? "1370304754" : "1932605469");
    buf_add(b, "=");
    char num[32]; snprintf(num, sizeof num, "%d", e->id);
    buf_add(b, num);
    buf_add(b, "%20");
    lemma_link(e->lemma_1, b);
    buf_add(b, "&entry.1433863141=GoldenDict+");
    buf_add(b, hoy);
    buf_add(b, "\"\n                    target=\"_blank\">");
    buf_add(b, T("Report it here."));
    buf_add(b, "</a>\n            </p>\n        </div>\n\n");
}

/* ---------- sub-paso 3e: las secciones de carga diferida ---------- */

static void seccion_carga(Buffer *b, const char *id, const char *lemma_,
                          const char *texto) {
    buf_add(b, "<div id=\""); buf_add(b, id); buf_add(b, "_");
    buf_add(b, lemma_);
    buf_add(b, "\" class=\"dpd content hidden\">\n    ");
    buf_add(b, texto);          /* estos textos no se traducen */
    buf_add(b, "\n</div>\n\n");
}

void construir_secciones_carga(Buffer *b, const Entrada *e, const Contexto *ctx) {
    char limpio[256], lemma_[512];
    lemma_clean(e->lemma_1, limpio, sizeof limpio);
    size_t k = 0;
    for (const char *p = e->lemma_1; *p && k + 1 < sizeof lemma_; p++)
        lemma_[k++] = (*p == ' ' || *p == '.') ? '_' : *p;
    lemma_[k] = '\0';

    int hay_significado = e->meaning_1 && *e->meaning_1;

    if (*e->family_root) seccion_carga(b, "family_root", lemma_, "root family loading...");
    else buf_add(b, "\n");

    if (*e->family_word) seccion_carga(b, "family_word", lemma_, "family word loading...");
    else buf_add(b, "\n");

    int es_sandhi = strstr(e->pos, "sandhi") != NULL;
    int es_idiom = strstr(e->pos, "idiom") != NULL;
    int en_cf = 0;
    if (e->family_compound && *e->family_compound) {
        char *copia = strdup(e->family_compound), *g = NULL;
        for (char *t = strtok_r(copia, " ", &g); t; t = strtok_r(NULL, " ", &g))
            if (conjunto_contiene(ctx->cf_set, t)) { en_cf = 1; break; }
        free(copia);
    } else {
        en_cf = conjunto_contiene(ctx->cf_set, limpio);
    }
    int fc = (hay_significado && !es_sandhi && !es_idiom && en_cf) &&
             ((!strchr(e->family_compound, ' ') && !strchr(e->compound_type, '?')) ||
              (strchr(e->family_compound, ' ') && largo_utf8(limpio) < 30));
    if (fc) seccion_carga(b, "family_compound", lemma_, "compound families loading...");
    else buf_add(b, "\n");

    int idioms = 0;
    if (hay_significado) {
        if (e->family_idioms && *e->family_idioms) {
            char *copia = strdup(e->family_idioms), *g = NULL;
            for (char *t = strtok_r(copia, " ", &g); t; t = strtok_r(NULL, " ", &g))
                if (conjunto_contiene(ctx->idioms_set, t)) { idioms = 1; break; }
            free(copia);
        } else {
            idioms = conjunto_contiene(ctx->idioms_set, limpio);
        }
    }
    if (idioms) seccion_carga(b, "family_idiom", lemma_, "idioms loading...");
    else buf_add(b, "\n");

    int n_sets = 0;
    if (e->family_set && *e->family_set) {
        char *copia = strdup(e->family_set), *g = NULL;
        for (char *t = strtok_r(copia, ";", &g); t; t = strtok_r(NULL, ";", &g)) n_sets++;
        free(copia);
    }
    if (hay_significado && n_sets >= 1) seccion_carga(b, "family_set", lemma_, "sets loading...");
    else buf_add(b, "\n");

    static const char *EXCL[] = {"abbrev","cs","idiom","letter","prefix",
                                 "root","suffix","ve", NULL};
    if (!en_lista(e->pos, EXCL)) seccion_carga(b, "frequency", lemma_, "frequency loading...");
    else buf_add(b, "\n");

    /* feedback no lleva linea en blanco: la plantilla lo pega a </body> */
    buf_add(b, "<div id=\"feedback_"); buf_add(b, lemma_);
    buf_add(b, "\" class=\"dpd content hidden\">\n    feedback loading...\n</div>\n");
}
