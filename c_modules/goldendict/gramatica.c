/* Sub-paso 3d: la tabla de gramatica (25 % del cuerpo, 245 lineas de plantilla
 * y 62 construcciones de control) y la seccion de ejemplos.
 *
 * La transliteracion IPA usa una tabla generada desde aksharamukha
 * (ipa_tabla.h), verificada al 100 % sobre 20 000 lemas.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "piloto.h"
#include "ipa_tabla.h"

/* ---------- IPA por coincidencia mas larga ---------- */

static void add_ipa(Buffer *b, const char *lemma) {
    const char *p = lemma;
    while (*p) {
        int puesto = 0;
        for (int i = 0; IPA_TABLA[i][0]; i++) {
            size_t n = strlen(IPA_TABLA[i][0]);
            if (strncmp(p, IPA_TABLA[i][0], n) == 0) {
                buf_add(b, IPA_TABLA[i][1]);
                p += n; puesto = 1; break;
            }
        }
        if (!puesto) { buf_add_n(b, p, 1); p++; }
    }
}

/* _NewlineView de data_classes.py: nueve campos muestran los saltos de linea
 * como <br>. */
static void buf_add_nl(Buffer *b, const char *s) {
    if (!s) return;
    for (const char *p = s; *p; p++) {
        if (*p == '\n') buf_add(b, "<br>"); else buf_add_n(b, p, 1);
    }
}

/* ---------- filas de la tabla ---------- */

/* Con trim_blocks/lstrip_blocks un bloque {% if %} falso no deja sangria, pero
 * la linea en blanco que lo sigue en la plantilla si se emite. De ahi que una
 * fila omitida tenga que escribir igualmente su salto. */
static void fila_gram(Buffer *b, const char *etiqueta, const char *valor) {
    if (!valor || !*valor) { buf_add(b, "\n"); return; }
    buf_add(b, "        <tr>\n            <th>");
    buf_add(b, T(etiqueta));
    buf_add(b, "</th>\n            <td>");
    buf_add(b, valor);
    buf_add(b, "</td>\n        </tr>\n\n");
}

/* Fila sin la linea en blanco final. */
static void fila_sin_blanco(Buffer *b, const char *etiqueta, const char *valor) {
    if (!valor || !*valor) return;
    buf_add(b, "        <tr>\n            <th>");
    buf_add(b, T(etiqueta));
    buf_add(b, "</th>\n            <td>");
    buf_add(b, valor);
    buf_add(b, "</td>\n        </tr>\n");
}

static void fila_nl(Buffer *b, const char *etiqueta, const char *valor) {
    if (!valor || !*valor) { buf_add(b, "\n"); return; }
    buf_add(b, "        <tr>\n            <th>");
    buf_add(b, T(etiqueta));
    buf_add(b, "</th>\n            <td>");
    buf_add_nl(b, valor);
    buf_add(b, "</td>\n        </tr>\n\n");
}

static void lemma_guionbajo(const char *lemma, char *destino, size_t tam) {
    size_t k = 0;
    for (const char *p = lemma; *p && k + 1 < tam; p++)
        destino[k++] = (*p == ' ' || *p == '.') ? '_' : *p;
    destino[k] = '\0';
}

static void add_lemma_link(Buffer *b, const char *lemma) {
    for (const char *p = lemma; *p; p++) {
        if (*p == ' ') buf_add(b, "%20"); else buf_add_n(b, p, 1);
    }
}

void construir_gramatica(Buffer *b, const Entrada *e, const Raiz *rt,
                         const Audio *audio, const char *hoy) {
    if (!e->meaning_1 || !*e->meaning_1) { buf_add(b, "\n"); return; }

    char limpio[256], lemma_[512];
    lemma_clean(e->lemma_1, limpio, sizeof limpio);
    lemma_guionbajo(e->lemma_1, lemma_, sizeof lemma_);

    buf_add(b, "<div id=\"grammar_"); buf_add(b, lemma_);
    buf_add(b, "\" class=\"dpd content hidden\">\n    <table class=\"grammar\">\n");

    fila_gram(b, "Lemma", limpio);

    if (e->lemma_trad_clean && *e->lemma_trad_clean &&
        strcmp(e->lemma_trad_clean, limpio) != 0)
        fila_gram(b, "Traditional Lemma", e->lemma_trad_clean);
    else buf_add(b, "\n");

    /* IPA, con sus botones de audio */
    buf_add(b, "        <tr>\n            <th>");
    buf_add(b, T("IPA"));
    buf_add(b, "</th>\n            <td>\n                /");
    add_ipa(b, limpio);
    buf_add(b, "/\n");
    static const char *VOCES[] = {"male1", "male2", "female1", NULL};
    int tiene[3] = { audio ? audio->male1 : 0, audio ? audio->male2 : 0,
                     audio ? audio->female1 : 0 };
    for (int v = 0; VOCES[v]; v++) {
        if (!tiene[v]) continue;
        buf_add(b, "                <a class=\"dpd-button play small\" onclick=\"playAudio('");
        buf_add(b, limpio);
        buf_add(b, "', this, '"); buf_add(b, VOCES[v]);
        buf_add(b, "')\" data-title=\""); buf_add(b, VOCES[v]);
        buf_add(b, "\">\n                    <svg viewBox=\"0 0 24 24\">\n"
                   "                        <path d=\"M8 5v14l11-7z\"></path>\n"
                   "                    </svg>\n                </a>\n");
    }
    buf_add(b, "            </td>\n        </tr>\n\n");

    /* make_grammar_line */
    Buffer g; buf_init(&g, 256);
    buf_add(&g, e->grammar);
    if (*e->neg)   { buf_add(&g, ", "); buf_add(&g, e->neg); }
    if (*e->verb)  { buf_add(&g, ", "); buf_add(&g, e->verb); }
    if (*e->trans) { buf_add(&g, ", "); buf_add(&g, e->trans); }
    if (*e->plus_case) { buf_add(&g, " ("); buf_add(&g, e->plus_case); buf_add(&g, ")"); }
    fila_gram(b, "Grammar", g.datos);
    free(g.datos);

    /* la plantilla encadena este {% endif %} con el {% if root_key %}
     * siguiente, sin linea en blanco entre ambos */
    fila_sin_blanco(b, "Root Family", e->family_root);

    if (*e->root_key && rt) {
        char root_limpio[256];
        snprintf(root_limpio, sizeof root_limpio, "%s", e->root_key);
        for (size_t i = 0; root_limpio[i + 1]; i++)
            if (root_limpio[i] == ' ' && root_limpio[i + 1] >= '0' &&
                root_limpio[i + 1] <= '9') { root_limpio[i] = '\0'; break; }

        buf_add(b, "        <tr>\n            <th>"); buf_add(b, T("Root"));
        buf_add(b, "</th>\n            <td>\n                ");
        buf_add(b, root_limpio);
        buf_add(b, "<sup>"); buf_add(b, rt->root_has_verb); buf_add(b, "</sup>");
        buf_add(b, rt->root_group);
        buf_add(b, "\n                "); buf_add(b, e->root_sign);
        buf_add(b, " ("); buf_add(b, superponer_raiz(e->root_key, rt->root_meaning)); buf_add(b, ")\n");
        buf_add(b, "            </td>\n        </tr>\n");

        if (*rt->root_in_comps) fila_sin_blanco(b, "√ In Sandhi", rt->root_in_comps);
    }
    buf_add(b, "\n");   /* linea en blanco antes del bloque de Base */

    fila_gram(b, "Base", e->root_base);
    fila_nl(b, "Construction", e->construction);

    if (*e->derivative) {
        Buffer d; buf_init(&d, 128);
        buf_add(&d, e->derivative); buf_add(&d, " (");
        buf_add(&d, e->suffix); buf_add(&d, ")");
        fila_gram(b, "Derivative", d.datos);
        free(d.datos);
    } else buf_add(b, "\n");

    fila_nl(b, "Phonetic Change", e->phonetic);

    if (*e->compound_type && !strchr(e->compound_type, '?')) {
        Buffer c; buf_init(&c, 128);
        buf_add(&c, e->compound_type); buf_add(&c, " (");
        buf_add_nl(&c, e->compound_construction); buf_add(&c, ")");
        fila_gram(b, "Compound", c.datos);
        free(c.datos);
    } else buf_add(b, "\n");

    fila_gram(b, "Antonym", e->antonym);
    fila_gram(b, "Synonym", e->synonym);

    /* La plantilla intercala aqui lineas de solo espacios, y un {% endif %}
     * con espacio final que trim_blocks no recorta. Hay que emitirlas. */
    if (*e->variant && !*e->var_phonetic && !*e->var_text)
        fila_sin_blanco(b, "Variant", e->variant);
    buf_add(b, "        \n");
    if (*e->var_phonetic) fila_sin_blanco(b, "Phonetic Variant", e->var_phonetic);
    buf_add(b, " \n");
    buf_add(b, "        \n");
    if (*e->var_text) fila_sin_blanco(b, "Textual Variant", e->var_text);
    buf_add(b, "\n");

    if (*e->commentary && strcmp(e->commentary, "-") != 0)
        fila_nl(b, "Commentary", e->commentary);
    else buf_add(b, "\n");

    fila_nl(b, "Notes", e->notes);
    fila_gram(b, "English Cognate", e->cognate);

    if (*e->link) {
        buf_add(b, "        <tr>\n            <th>"); buf_add(b, T("Web Link"));
        buf_add(b, "</th>\n            <td>\n");
        char *copia = strdup(e->link), *g2 = NULL;
        for (char *t = strtok_r(copia, "\n", &g2); t; t = strtok_r(NULL, "\n", &g2)) {
            buf_add(b, "                <p style=\"margin: 0; padding: 0\">\n"
                       "                    <a href=\"");
            buf_add(b, t); buf_add(b, "\" target=\"_blank\"> "); buf_add(b, t);
            buf_add(b, " </a>\n                </p>\n");
        }
        free(copia);
        buf_add(b, "            </td>\n        </tr>\n\n");
    } else buf_add(b, "\n");

    if (*e->non_ia) {
        Buffer i; buf_init(&i, 128);
        buf_add(&i, "<i>"); buf_add(&i, e->non_ia); buf_add(&i, "</i>");
        fila_gram(b, "Non IA", i.datos); free(i.datos);
    } else buf_add(b, "\n");
    if (*e->sanskrit) {
        Buffer i; buf_init(&i, 128);
        buf_add(&i, "<i>"); buf_add(&i, e->sanskrit); buf_add(&i, "</i>");
        fila_gram(b, "Sanskrit", i.datos); free(i.datos);
    } else buf_add(b, "\n");

    if (*e->root_key && rt && *rt->sanskrit_root && strcmp(rt->sanskrit_root, "-") != 0) {
        buf_add(b, "        <tr>\n            <th>"); buf_add(b, T("Sanskrit Root"));
        buf_add(b, "</th>\n            <td>\n                <i>\n                    ");
        buf_add(b, rt->sanskrit_root); buf_add(b, " "); buf_add(b, rt->sanskrit_root_class);
        buf_add(b, "\n                    ("); buf_add(b, superponer_sanscrito(rt->sanskrit_root_meaning));
        buf_add(b, ")\n                </i>\n            </td>\n        </tr>\n\n");
    } else buf_add(b, "\n");

    buf_add(b, "    </table>\n    <p class=\"dpd-footer\">\n        ");
    buf_add(b, T("Did you spot a mistake?"));
    buf_add(b, "\n        <a\n            class=\"dpd-link\"\n"
               "            href=\"https://docs.google.com/forms/d/e/"
               "1FAIpQLSf9boBe7k5tCwq7LdWgBHHGIPVc4ROO5yjVDo1X5LDAxkmGWQ/viewform"
               "?usp=pp_url&entry.438735500=");
    char num[32]; snprintf(num, sizeof num, "%d", e->id);
    buf_add(b, num); buf_add(b, "%20");
    add_lemma_link(b, e->lemma_1);
    buf_add(b, "&entry.326955045=Grammar&entry.1433863141=GoldenDict+");
    buf_add(b, hoy);
    buf_add(b, "\"\n            target=\"_blank\">");
    buf_add(b, T("Correct it here"));
    buf_add(b, "</a>. <br />");
    buf_add(b, T("Is some word missing from\n        the dictionary?"));
    buf_add(b, "\n        <a\n            class=\"dpd-link\"\n"
               "            href=\"https://docs.google.com/forms/d/e/"
               "1FAIpQLSfResxEUiRCyFITWPkzoQ2HhHEvUS5fyg68Rl28hFH6vhHlaA/viewform"
               "?usp=pp_url&entry.1433863141=GoldenDict+");
    buf_add(b, hoy);
    buf_add(b, "\"\n            target=\"_blank\">");
    buf_add(b, T("Add it here"));
    buf_add(b, "</a>.\n    </p>\n</div>\n\n");
}

/* ---------- la seccion de ejemplos ---------- */

void construir_ejemplos(Buffer *b, const Entrada *e, const char *hoy) {
    int hay = e->meaning_1 && *e->meaning_1 && e->example_1 && *e->example_1;
    if (!hay) { buf_add(b, "\n"); return; }
    int dos = e->example_2 && *e->example_2;

    char lemma_[512];
    lemma_guionbajo(e->lemma_1, lemma_, sizeof lemma_);

    buf_add(b, dos ? "<div id=\"examples_" : "    <div id=\"example_");
    buf_add(b, lemma_);
    buf_add(b, "\" class=\"dpd content hidden\">\n\n");

    buf_add(b, "        <p>"); buf_add_nl(b, e->example_1); buf_add(b, "</p>\n");
    buf_add(b, "        <p class=\"sutta\">\n            ");
    buf_add(b, e->source_1); buf_add(b, " "); buf_add_nl(b, e->sutta_1);
    buf_add(b, "</p>\n\n");

    if (dos) {
        buf_add(b, "        <p>\n            "); buf_add_nl(b, e->example_2);
        buf_add(b, "\n        </p>\n        <p class=\"sutta\">\n            ");
        buf_add(b, e->source_2); buf_add(b, " "); buf_add_nl(b, e->sutta_2);
        buf_add(b, "\n        </p>\n");
    }

    buf_add(b, "        <p class='dpd-footer'>\n            ");
    buf_add(b, T("Can you think of a better example?"));
    buf_add(b, " <a class=\"dpd-link\"\n                href=\"https://docs.google.com/forms/d/e/"
               "1FAIpQLSf9boBe7k5tCwq7LdWgBHHGIPVc4ROO5yjVDo1X5LDAxkmGWQ/viewform"
               "?usp=pp_url&entry.438735500=");
    char num[32]; snprintf(num, sizeof num, "%d", e->id);
    buf_add(b, num); buf_add(b, "%20");
    add_lemma_link(b, e->lemma_1);
    buf_add(b, "&entry.326955045=Examples&entry.1433863141=GoldenDict+");
    buf_add(b, hoy);
    buf_add(b, "\"\n                target=\"_blank\">");
    buf_add(b, T("Add it here."));
    buf_add(b, "</a>\n        </p>\n    </div>\n\n");
}
