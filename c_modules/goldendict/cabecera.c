/* Paso 2 del piloto: la cabecera de la entrada, reproducida byte a byte.
 *
 * Equivale a dpd_header.jinja (116 lineas, 19 construcciones de control) mas
 * CSSManager.update_style(..., "dpd") mas las propiedades calculadas
 * needs_*_button del modelo.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "piloto.h"

/* ---------- variables CSS, leidas y transformadas una sola vez ---------- */

static char *css_variables = NULL;

/* Reproduce _remove_comments_and_whitespace + _remove_only(["shade"]):
 * quita comentarios, lineas vacias y lineas que contengan "shade". */
static char *transformar_css(const char *bruto) {
    size_t n = strlen(bruto);
    char *sin_com = malloc(n + 1);
    size_t j = 0;
    for (size_t i = 0; i < n;) {
        if (bruto[i] == '/' && i + 1 < n && bruto[i + 1] == '*') {
            const char *cierre = strstr(bruto + i + 2, "*/");
            if (!cierre) break;
            i = (size_t)(cierre - bruto) + 2;
        } else {
            sin_com[j++] = bruto[i++];
        }
    }
    sin_com[j] = '\0';

    Buffer out; buf_init(&out, j + 16);
    char *guardar = NULL;
    for (char *linea = strtok_r(sin_com, "\n", &guardar); linea;
         linea = strtok_r(NULL, "\n", &guardar)) {
        const char *p = linea;
        while (*p == ' ' || *p == '\t' || *p == '\r') p++;
        if (!*p) continue;                      /* linea vacia */
        if (strstr(linea, "shade")) continue;   /* _remove_only(["shade"]) */
        if (out.largo) buf_add(&out, "\n");
        buf_add(&out, linea);
    }
    free(sin_com);
    return out.datos;
}

void cabecera_init(const char *ruta_css_variables) {
    FILE *f = fopen(ruta_css_variables, "rb");
    if (!f) { fprintf(stderr, "no se pudo leer %s\n", ruta_css_variables); exit(1); }
    fseek(f, 0, SEEK_END);
    long tam = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *bruto = malloc((size_t)tam + 1);
    size_t leidos = fread(bruto, 1, (size_t)tam, f);
    bruto[leidos] = '\0';
    fclose(f);
    css_variables = transformar_css(bruto);
    free(bruto);
}

/* ---------- partes del discurso excluidas de la frecuencia ---------- */

static const char *EXCLUIDAS_FREQ[] = {
    "abbrev", "cs", "idiom", "letter", "prefix", "root", "suffix", "ve", NULL
};

static int excluida_de_freq(const char *pos) {
    for (int i = 0; EXCLUIDAS_FREQ[i]; i++)
        if (strcmp(pos, EXCLUIDAS_FREQ[i]) == 0) return 1;
    return 0;
}

/* ---------- utilidades ---------- */

/* lemma_1_ : espacios y puntos pasan a guion bajo */
static void add_lemma_guionbajo(Buffer *b, const char *lemma) {
    for (const char *p = lemma; *p; p++) {
        char c = (*p == ' ' || *p == '.') ? '_' : *p;
        char s[2] = {c, '\0'};
        buf_add(b, s);
    }
}

/* lemma_clean : quita " <digito>..." del final  (re.sub(r" \d.*$", "", lemma)) */
void lemma_clean(const char *lemma, char *destino, size_t tam) {
    size_t n = strlen(lemma);
    size_t corte = n;
    for (size_t i = 0; i + 1 < n; i++) {
        if (lemma[i] == ' ' && lemma[i + 1] >= '0' && lemma[i + 1] <= '9') {
            corte = i;
            break;
        }
    }
    if (corte >= tam) corte = tam - 1;
    memcpy(destino, lemma, corte);
    destino[corte] = '\0';
}

/* Emite una lista separada por sep como  'a', 'b'  */
static void add_lista_citada(Buffer *b, const char *valor, const char *sep,
                             const char *si_vacio) {
    if (!valor || !*valor) {
        if (si_vacio && *si_vacio) {
            buf_add(b, "'"); buf_add(b, si_vacio); buf_add(b, "'");
        }
        return;
    }
    char *copia = strdup(valor);
    char *guardar = NULL;
    int primero = 1;
    for (char *t = strtok_r(copia, sep, &guardar); t; t = strtok_r(NULL, sep, &guardar)) {
        /* Python parte family_set por "; ", asi que el elemento no lleva el
         * espacio inicial; recortarlo mantiene la paridad. */
        while (*t == ' ') t++;
        size_t l = strlen(t);
        while (l && t[l - 1] == ' ') t[--l] = '\0';
        if (!l) continue;
        if (!primero) buf_add(b, ", ");
        buf_add(b, "'"); buf_add(b, t); buf_add(b, "'");
        primero = 0;
    }
    free(copia);
}

static int cuenta_separadores(const char *valor, const char *sep) {
    if (!valor || !*valor) return 0;
    int n = 0;
    char *copia = strdup(valor);
    char *guardar = NULL;
    for (char *t = strtok_r(copia, sep, &guardar); t; t = strtok_r(NULL, sep, &guardar)) n++;
    free(copia);
    return n;
}

/* Extrae el valor crudo de una clave de un JSON plano (el de freq_data).
 * Devuelve punteros al interior de la cadena original. */
static int json_valor(const char *json, const char *clave, const char **ini, size_t *largo) {
    char patron[64];
    snprintf(patron, sizeof patron, "\"%s\":", clave);
    const char *p = strstr(json, patron);
    if (!p) return 0;
    p += strlen(patron);
    while (*p == ' ') p++;
    const char *fin = p;
    if (*p == '[') {
        int prof = 0;
        while (*fin) { if (*fin == '[') prof++; else if (*fin == ']') { prof--; if (!prof) { fin++; break; } } fin++; }
    } else if (*p == '"') {
        fin = p + 1;
        while (*fin && *fin != '"') fin++;
        if (*fin) fin++;
    } else {
        while (*fin && *fin != ',' && *fin != '}') fin++;
    }
    *ini = p;
    *largo = (size_t)(fin - p);
    return 1;
}

static void add_json_crudo(Buffer *b, const char *json, const char *clave, int comillas) {
    const char *ini; size_t largo;
    if (!json_valor(json, clave, &ini, &largo)) return;
    if (comillas && largo >= 2 && *ini == '"') { ini++; largo -= 2; }  /* solo el texto */
    if (comillas) { buf_add_n(b, ini, largo); return; }
    /* El filtro tojson de Jinja usa el separador ", " de json.dumps, mientras
     * que la columna guarda el JSON compacto: hay que reinsertar el espacio. */
    for (size_t k = 0; k < largo; k++) {
        buf_add_n(b, ini + k, 1);
        if (ini[k] == ',') buf_add(b, " ");
    }
}

/* ---------- la cabecera ---------- */

void construir_cabecera(Buffer *b, const Entrada *e, const char *fecha,
                        const ConjuntoTexto *idioms_set,
                        const ConjuntoTexto *cf_set) {
    Buffer h; buf_init(&h, 8192);

    buf_add(&h, "<!DOCTYPE html>\n<html lang=\"en\">\n\n    <head>\n");
    buf_add(&h, "        <meta charset=\"utf-8\">\n        <meta data_key=\"data_");
    add_lemma_guionbajo(&h, e->lemma_1);
    buf_add(&h, "\">\n\n        <title>Digital Pāḷi Dictionary</title>\n\n");

    buf_add(&h, "        <style>\n");
    buf_add(&h, css_variables);
    buf_add(&h, "\n        </style>\n\n");

    buf_add(&h,
        "        <link href=\"dpd-css-and-fonts.css\" rel=\"stylesheet\">\n\n");
    static const char *JS[] = {
        "family_compound_json.js", "family_compound_template.js",
        "family_idiom_json.js", "family_idiom_template.js",
        "family_root_json.js", "family_root_template.js",
        "family_set_json.js", "family_set_template.js",
        "family_word_json.js", "family_word_template.js",
        "feedback_template.js", "frequency_template.js", NULL
    };
    for (int i = 0; JS[i]; i++) {
        buf_add(&h, "        <link href=\""); buf_add(&h, JS[i]);
        buf_add(&h, "\" class=\"load_js\" rel=\"preload\"\n            as=\"script\">\n");
    }
    buf_add(&h, "        <link href=\"main.js\" class=\"load_js\" rel=\"preload\" as=\"script\">\n");

    /* --- el objeto de datos --- */
    buf_add(&h, "\n        <script>\n        var data_");
    add_lemma_guionbajo(&h, e->lemma_1);
    buf_add(&h, " = {\n            \"id\": ");
    char num[32]; snprintf(num, sizeof num, "%d", e->id);
    buf_add(&h, num);
    buf_add(&h, ",\n            \"lemma\": \""); buf_add(&h, e->lemma_1);
    buf_add(&h, "\",\n            \"date\": \""); buf_add(&h, fecha);
    buf_add(&h, "\",\n            \"family_root\": \""); buf_add(&h, e->root_family_key);
    buf_add(&h, "\",\n            \n");

    char limpio[256];
    lemma_clean(e->lemma_1, limpio, sizeof limpio);

    int tiene_significado = e->meaning_1 && *e->meaning_1;
    int es_sandhi = strstr(e->pos, "sandhi") != NULL;
    int es_idiom = strstr(e->pos, "idiom") != NULL;

    /* any(item in cf_set for item in family_compound_list)
     *   or (lemma_clean in cf_set and not family_compound)          */
    int en_cf = 0;
    if (e->family_compound && *e->family_compound) {
        char *copia = strdup(e->family_compound), *g = NULL;
        for (char *t = strtok_r(copia, " ", &g); t; t = strtok_r(NULL, " ", &g))
            if (conjunto_contiene(cf_set, t)) { en_cf = 1; break; }
        free(copia);
    } else {
        en_cf = conjunto_contiene(cf_set, limpio);
    }

    int fc_una = tiene_significado && !strchr(e->family_compound, ' ')
                 && !es_sandhi && !es_idiom && !strchr(e->compound_type, '?')
                 && en_cf;
    int fc_varias = tiene_significado && strchr(e->family_compound, ' ')
                    && !es_sandhi && !es_idiom && largo_utf8(limpio) < 30
                    && en_cf;

    if (fc_una || fc_varias) {
        buf_add(&h, "            \"family_compounds\": [\n                ");
        add_lista_citada(&h, e->family_compound, " ", limpio);
        buf_add(&h, "],\n");
    } else {
        buf_add(&h, "            \n            \"family_compounds\": [],\n            \n");
    }
    buf_add(&h, "\n");

    /* needs_idioms_button: algun idioma de la lista esta en idioms_set, o el
     * lema limpio lo esta cuando la lista viene vacia */
    int idioms = 0;
    if (tiene_significado) {
        if (e->family_idioms && *e->family_idioms) {
            char *copia = strdup(e->family_idioms), *g = NULL;
            for (char *t = strtok_r(copia, " ", &g); t; t = strtok_r(NULL, " ", &g))
                if (conjunto_contiene(idioms_set, t)) { idioms = 1; break; }
            free(copia);
        } else {
            idioms = conjunto_contiene(idioms_set, limpio);
        }
    }
    if (idioms) {
        buf_add(&h, "            \"family_idioms\": [\n                ");
        add_lista_citada(&h, e->family_idioms, " ", limpio);
        buf_add(&h, "],\n");
    } else {
        buf_add(&h, "            \n            \"family_idioms\": [],\n            \n");
    }
    buf_add(&h, "\n");

    if (e->family_word && *e->family_word) {
        buf_add(&h, "            \"family_word\": \""); buf_add(&h, e->family_word);
        buf_add(&h, "\",\n");
    } else {
        buf_add(&h, "            \n");
    }
    buf_add(&h, "\n");

    int n_sets = cuenta_separadores(e->family_set, ";");
    int set_uno = tiene_significado && e->family_set && *e->family_set && n_sets == 1;
    int set_varios = tiene_significado && e->family_set && *e->family_set && n_sets > 1;
    if (set_uno || set_varios) {
        buf_add(&h, "            \"family_sets\": [\n                ");
        add_lista_citada(&h, e->family_set, ";", "");
        buf_add(&h, "],\n");
    } else {
        buf_add(&h, "            \n            \"family_sets\": [],\n            \n");
    }
    buf_add(&h, "\n");

    if (!excluida_de_freq(e->pos) && e->freq_data && *e->freq_data) {
        buf_add(&h, "            \"FreqHeading\": \"");
        add_json_crudo(&h, e->freq_data, "FreqHeading", 1);
        buf_add(&h, "\",\n");
        static const char *SERIES[] = {"CstFreq","CstGrad","BjtFreq","BjtGrad",
                                       "SyaFreq","SyaGrad","ScFreq","ScGrad", NULL};
        for (int i = 0; SERIES[i]; i++) {
            buf_add(&h, "            \""); buf_add(&h, SERIES[i]); buf_add(&h, "\": ");
            add_json_crudo(&h, e->freq_data, SERIES[i], 0);
            buf_add(&h, SERIES[i + 1] ? ",\n" : "\n");
        }
    } else {
        buf_add(&h, "            \n");
    }

    buf_add(&h, "        };\n    </script>\n\n");
    buf_add(&h,
        "        <script>\n        if (typeof progName == \"undefined\") {\n"
        "            var progName = \"GoldenDict\"\n        }\n    </script>\n\n");
    buf_add(&h,
        "        <script>\n        var els2load = document.getElementsByClassName(\"load_js\");\n"
        "        if (typeof js_already_loaded === \"undefined\") {\n"
        "            var js_already_loaded = true;\n"
        "            var elsArray = Array.from(els2load);\n"
        "            elsArray.forEach(el => {\n"
        "                var s = document.createElement(\"script\");\n"
        "                var elName = el.getAttribute(\"href\");\n"
        "                s.src = elName;\n"
        "                document.head.appendChild(s);\n"
        "            });\n        };\n    </script>\n\n    </head>");

    buf_add_squashed(b, h.datos);
    free(h.datos);
}
