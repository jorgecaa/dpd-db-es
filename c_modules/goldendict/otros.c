/* Las otras entradas del diccionario: raíces y EPD (inglés → pāḷi).
 *
 * Reproduce exporter/goldendict/export_roots.py y export_epd.py, con sus
 * plantillas `dpd_root.jinja` + `root_header.jinja` y `epd.jinja` +
 * `dpd_header_plain.jinja`.
 *
 * Las dos cabeceras usan reducciones distintas de las variables CSS —
 * CSSManager.reduce_style con "root" y con "primary" — así que aquí se
 * reproducen las dos junto a la de "dpd" que ya usa cabecera.c.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sqlite3.h"
#include "piloto.h"

/* ---------- orden pāḷi (tools/pali_sort_key.py) ---------- */

/* letter_to_number: dígrafos primero, para que kh no se lea como k + h */
static const char *ALFABETO[][2] = {
    {"√","00"},{"kh","10"},{"gh","12"},{"ch","16"},{"jh","18"},{"ṭh","21"},
    {"ḍh","23"},{"th","26"},{"dh","28"},{"ph","31"},{"bh","33"},
    {"a","01"},{"ā","02"},{"i","03"},{"ī","04"},{"u","05"},{"ū","06"},
    {"e","07"},{"o","08"},{"k","09"},{"g","11"},{"ṅ","13"},{"c","14"},
    {"j","17"},{"ñ","19"},{"ṭ","20"},{"ḍ","22"},{"ṇ","24"},{"t","25"},
    {"d","27"},{"n","29"},{"p","30"},{"b","32"},{"m","34"},{"y","35"},
    {"r","36"},{"l","37"},{"ḷ","38"},{"v","39"},{"s","40"},{"h","41"},
    {"ṃ","42"},{NULL,NULL}
};

/* Clave numérica comparable, como hace pali_sort_key. */
static char *clave_pali(const char *s) {
    Buffer b;
    buf_init(&b, strlen(s) * 3 + 8);
    while (*s) {
        int puesto = 0;
        for (int i = 0; ALFABETO[i][0]; i++) {
            size_t n = strlen(ALFABETO[i][0]);
            if (strncmp(s, ALFABETO[i][0], n) == 0) {
                buf_add(&b, ALFABETO[i][1]);
                s += n;
                puesto = 1;
                break;
            }
        }
        if (!puesto) { buf_add_n(&b, s, 1); s++; }
    }
    return b.datos;
}

/* ---------- variables CSS por estilo ---------- */

static char *css_root = NULL;      /* _remove_all_except(["--primary","--gray"]) */
static char *css_primary = NULL;   /* _remove_all_except(["--primary"]) */
static char *css_secondary = NULL; /* _remove_all_except(["--secondary"]) */

/* Las variables CSS ya reducidas para un estilo de CSSManager. */
const char *css_reducido(const char *estilo) {
    if (strcmp(estilo, "root") == 0) return css_root;
    if (strcmp(estilo, "primary") == 0) return css_primary;
    if (strcmp(estilo, "secondary") == 0) return css_secondary;
    return "";
}

/* Conserva las líneas con llaves y las que mencionan alguna de las variables,
 * que es lo que hace _remove_all_except. */
static char *reducir_css(const char *bruto, const char **variables) {
    char *copia = strdup(bruto);
    Buffer out;
    buf_init(&out, strlen(bruto) + 16);
    char *guardar = NULL;
    for (char *linea = strtok_r(copia, "\n", &guardar); linea;
         linea = strtok_r(NULL, "\n", &guardar)) {
        int conservar = strchr(linea, '{') || strchr(linea, '}');
        for (int i = 0; !conservar && variables[i]; i++)
            if (strstr(linea, variables[i])) conservar = 1;
        if (!conservar) continue;
        if (out.largo) buf_add(&out, "\n");
        buf_add(&out, linea);
    }
    free(copia);
    return out.datos;
}

void otros_init(const char *ruta_css_variables) {
    FILE *f = fopen(ruta_css_variables, "rb");
    if (!f) { fprintf(stderr, "no se pudo leer %s\n", ruta_css_variables); exit(1); }
    fseek(f, 0, SEEK_END);
    long tam = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *bruto = malloc((size_t)tam + 1);
    size_t leidos = fread(bruto, 1, (size_t)tam, f);
    bruto[leidos] = '\0';
    fclose(f);

    static const char *vars_root[] = {"--primary", "--gray", NULL};
    static const char *vars_primary[] = {"--primary", NULL};
    static const char *vars_secondary[] = {"--secondary", NULL};
    css_root = reducir_css(bruto, vars_root);
    css_primary = reducir_css(bruto, vars_primary);
    css_secondary = reducir_css(bruto, vars_secondary);
    free(bruto);
}

/* ---------- transformaciones del nombre de la raíz ---------- */

static void sustituir_en(Buffer *b, const char *s, const char *de, const char *a) {
    size_t nd = strlen(de);
    for (const char *p = s; *p;) {
        if (strncmp(p, de, nd) == 0) { buf_add(b, a); p += nd; }
        else { buf_add_n(b, p, 1); p++; }
    }
}

/* root_: espacios a guiones bajos */
static char *root_guion(const char *root) {
    Buffer b; buf_init(&b, strlen(root) + 8);
    sustituir_en(&b, root, " ", "_");
    return b.datos;
}

/* root_no_sign_: sin √ y con guiones bajos */
static char *root_no_sign_guion(const char *root) {
    char *con_guion = root_guion(root);
    Buffer b; buf_init(&b, strlen(con_guion) + 8);
    sustituir_en(&b, con_guion, "√", "");
    free(con_guion);
    return b.datos;
}

/* root_link: espacios a %20 */
static char *root_enlace(const char *root) {
    Buffer b; buf_init(&b, strlen(root) * 3 + 8);
    sustituir_en(&b, root, " ", "%20");
    return b.datos;
}

/* root_clean: quita " <dígito>..." del final */
static char *root_limpio(const char *root) {
    char *out = strdup(root);
    size_t n = strlen(out);
    for (size_t i = 0; i + 1 < n; i++)
        if (out[i] == ' ' && out[i + 1] >= '0' && out[i + 1] <= '9') {
            out[i] = '\0';
            break;
        }
    return out;
}

/* ---------- familias de una raíz ---------- */

typedef struct {
    char *root_family;
    char *root_family_;   /* espacios a guiones bajos */
    char *clave;          /* orden pāḷi */
} Familia;

static int cmp_familias(const void *x, const void *y) {
    return strcmp(((const Familia *)x)->clave, ((const Familia *)y)->clave);
}

/* ---------- entradas de raíz ---------- */

void construir_raiz(Buffer *b, sqlite3_stmt *st, const Familia *frs, size_t n_frs,
                    int cuenta, const char *fecha, const char *hoy) {
    const unsigned char *v;
#define COL(i) ((v = sqlite3_column_text(st, i)) ? (const char *)v : "")
    const char *root = COL(0);
    const char *root_has_verb = COL(1);
    const char *root_group = COL(2);
    const char *root_sign = COL(3);
    const char *root_meaning_en = COL(4);
    const char *root_info = COL(5);
    const char *root_matrix = COL(6);
#undef COL
    const char *root_meaning = superponer_raiz(root, root_meaning_en);

    char *limpio = root_limpio(root);
    char *r_ = root_guion(root);
    char *r_ns = root_no_sign_guion(root);
    char *enlace = root_enlace(root);

    /* --- cabecera, que se aplasta como la de las entradas normales --- */
    Buffer h; buf_init(&h, 4096);
    buf_add(&h, "<!DOCTYPE html>\n<html lang=\"en\">\n\n<head>\n"
                "    <meta charset=\"utf-8\">\n    <meta data_key=\"rootdata_");
    buf_add(&h, r_ns);
    buf_add(&h, "\">\n\n    <title>Digital Pāḷi Dictionary Roots</title>\n    <style>\n");
    buf_add(&h, css_root);
    buf_add(&h, "\n    </style>\n\n"
                "    <link href=\"dpd-css-and-fonts.css\" rel=\"stylesheet\">\n\n");
    static const char *JS[] = {
        "family_compound_json.js", "family_compound_template.js",
        "family_idiom_json.js", "family_idiom_template.js",
        "family_root_json.js", "family_root_template.js",
        "family_set_json.js", "family_set_template.js",
        "family_word_json.js", "family_word_template.js",
        "feedback_template.js", "main.js", NULL
    };
    for (int i = 0; JS[i]; i++) {
        buf_add(&h, "    <link href=\""); buf_add(&h, JS[i]);
        buf_add(&h, "\" class=\"load_js\" rel=\"preload\" as=\"script\">\n");
    }
    buf_add(&h, "\n    <script>\n        var rootdata_");
    buf_add(&h, r_ns);
    buf_add(&h, " = {\n            \"root\": \""); buf_add(&h, root);
    buf_add(&h, "\",\n            \"date\": \""); buf_add(&h, fecha);
    buf_add(&h, "\",\n        };\n    </script>\n\n"
                "    <script>\n        var els2load = document.getElementsByClassName(\"load_js\");\n"
                "        if (typeof js_already_loaded === \"undefined\") {\n"
                "            var js_already_loaded = true;\n"
                "            var elsArray = Array.from(els2load);\n"
                "            elsArray.forEach(el => {\n"
                "                var s = document.createElement(\"script\");\n"
                "                var elName = el.getAttribute(\"href\");\n"
                "                s.src = elName;\n"
                "                document.head.appendChild(s);\n"
                "            });\n        };\n    </script>\n\n</head>");
    buf_add_squashed(b, h.datos);
    free(h.datos);

    /* --- cuerpo --- */
    Buffer c; buf_init(&c, 4096);
    buf_add(&c, "<body>\n<div class='dpd'>\n    <p>root. <b>");
    buf_add(&c, limpio);
    buf_add(&c, "</b><sup>"); buf_add(&c, root_has_verb); buf_add(&c, "</sup>");
    buf_add(&c, root_group); buf_add(&c, " "); buf_add(&c, root_sign);
    buf_add(&c, " ("); buf_add(&c, root_meaning); buf_add(&c, ") ");
    char cuenta_txt[32];
    snprintf(cuenta_txt, sizeof cuenta_txt, "%d", cuenta);
    buf_add(&c, cuenta_txt);
    buf_add(&c, "</p> \n</div>\n<div class=\"button-box\">\n");
    buf_add(&c, "    <a class=\"dpd-button\" href=\"#\" data-target=\"root_info_");
    buf_add(&c, r_); buf_add(&c, "\">"); buf_add(&c, T("root info")); buf_add(&c, "</a>\n");
    buf_add(&c, "    <a class=\"dpd-button\" href=\"#\" data-target=\"root_matrix_");
    buf_add(&c, r_); buf_add(&c, "\">"); buf_add(&c, T("root matrix")); buf_add(&c, "</a>\n");
    for (size_t i = 0; i < n_frs; i++) {
        buf_add(&c, "    <a class=\"dpd-button\" href=\"#\"\n        data-target=\"family_root_");
        buf_add(&c, r_); buf_add(&c, "_"); buf_add(&c, frs[i].root_family_);
        buf_add(&c, "\">"); buf_add(&c, frs[i].root_family); buf_add(&c, "</a>\n");
    }
    buf_add(&c, "</div>\n<div id=\"root_info_"); buf_add(&c, r_);
    buf_add(&c, "\" class=\"dpd content hidden\">\n    ");
    buf_add(&c, root_info);
    buf_add(&c, "\n    <p class=\"dpd-footer\">\n        ");
    buf_add(&c, T("Something out of place?"));
    buf_add(&c, " <a class=\"dpd-link\" href=\"https://docs.google.com/forms/d/e/"
                "1FAIpQLSf9boBe7k5tCwq7LdWgBHHGIPVc4ROO5yjVDo1X5LDAxkmGWQ/viewform"
                "?usp=pp_url&entry.438735500=");
    buf_add(&c, enlace);
    buf_add(&c, "&entry.326955045=Root+Info&entry.1433863141=GoldenDict +");
    buf_add(&c, hoy);
    buf_add(&c, "\" target=\"_blank\">"); buf_add(&c, T("Report it here"));
    buf_add(&c, "</a>.\n    </p>\n</div>\n");

    buf_add(&c, "<div id=\"root_matrix_"); buf_add(&c, r_);
    buf_add(&c, "\" class=\"dpd content hidden\">\n    <p>\n");
    if (cuenta <= 1) {
        buf_add(&c, "        <b>"); buf_add(&c, cuenta_txt);
        buf_add(&c, "</b> "); buf_add(&c, T("word is derived from the root"));
        buf_add(&c, " \n");
    } else {
        buf_add(&c, "        <b>"); buf_add(&c, cuenta_txt);
        buf_add(&c, "</b> "); buf_add(&c, T("words are derived from the root"));
        buf_add(&c, " \n");
    }
    buf_add(&c, "        <b>"); buf_add(&c, limpio);
    buf_add(&c, "</b><sup>"); buf_add(&c, root_has_verb); buf_add(&c, "</sup>");
    buf_add(&c, root_group); buf_add(&c, " "); buf_add(&c, root_sign);
    buf_add(&c, " ("); buf_add(&c, root_meaning); buf_add(&c, ")\n    </p>\n    ");
    buf_add(&c, root_matrix);
    buf_add(&c, "\n    <p class=\"dpd-footer\">\n        ");
    buf_add(&c, T("Did you spot a mistake?"));
    buf_add(&c, " <a class=\"dpd-link\" href=\"https://docs.google.com/forms/d/e/"
                "1FAIpQLSf9boBe7k5tCwq7LdWgBHHGIPVc4ROO5yjVDo1X5LDAxkmGWQ/viewform"
                "?usp=pp_url&entry.438735500=");
    buf_add(&c, enlace);
    buf_add(&c, "&entry.326955045=Root+Matrix&entry.1433863141=GoldenDict+");
    buf_add(&c, hoy);
    buf_add(&c, "\" target=\"_blank\">"); buf_add(&c, T("Log it here."));
    buf_add(&c, "</a>\n    </p>\n</div>\n");

    for (size_t i = 0; i < n_frs; i++) {
        buf_add(&c, "    <div id=\"family_root_"); buf_add(&c, r_);
        buf_add(&c, "_"); buf_add(&c, frs[i].root_family_);
        buf_add(&c, "\" class=\"dpd content hidden\">\n        root family loading...\n    </div>\n");
    }
    buf_add(&c, "</body></html>");

    minificar_buffer(&c);
    buf_add_n(b, c.datos, c.largo);
    free(c.datos);

    free(limpio); free(r_); free(r_ns); free(enlace);
}

/* ---------- recorrido de las raíces ---------- */

size_t exportar_raices(sqlite3 *db, Diccionario *dicc, const char *fecha,
                       const char *hoy) {
    /* familias por raíz, ordenadas en orden pāḷi como hace RootsData */
    sqlite3_stmt *stf = NULL;
    if (sqlite3_prepare_v2(db,
            "SELECT root_key, root_family FROM family_root", -1, &stf, NULL) != SQLITE_OK)
        return 0;
    typedef struct Grupo { char *root_key; Familia *frs; size_t n, cap; struct Grupo *sig; } Grupo;
    static Grupo *grupos[1 << 12];
    while (sqlite3_step(stf) == SQLITE_ROW) {
        const char *rk = (const char *)sqlite3_column_text(stf, 0);
        const char *rf = (const char *)sqlite3_column_text(stf, 1);
        if (!rk || !rf) continue;
        size_t h = 0;
        for (const char *p = rk; *p; p++) h = h * 131 + (unsigned char)*p;
        h %= 1 << 12;
        Grupo *g = NULL;
        for (Grupo *q = grupos[h]; q; q = q->sig)
            if (strcmp(q->root_key, rk) == 0) { g = q; break; }
        if (!g) {
            g = malloc(sizeof *g);
            g->root_key = strdup(rk);
            g->cap = 4; g->n = 0;
            g->frs = malloc(g->cap * sizeof *g->frs);
            g->sig = grupos[h];
            grupos[h] = g;
        }
        if (g->n == g->cap) {
            g->cap *= 2;
            g->frs = realloc(g->frs, g->cap * sizeof *g->frs);
        }
        g->frs[g->n].root_family = strdup(rf);
        g->frs[g->n].root_family_ = root_guion(rf);
        g->frs[g->n].clave = clave_pali(rf);
        g->n++;
    }
    sqlite3_finalize(stf);

    sqlite3_stmt *st = NULL;
    if (sqlite3_prepare_v2(db,
            "SELECT root, root_has_verb, root_group, root_sign, root_meaning,"
            " root_info, root_matrix FROM dpd_roots", -1, &st, NULL) != SQLITE_OK)
        return 0;

    /* roots_count_dict: cuántas entradas derivan de cada raíz */
    sqlite3_stmt *stc = NULL;
    sqlite3_prepare_v2(db,
        "SELECT COUNT(*) FROM dpd_headwords WHERE root_key = ?", -1, &stc, NULL);

    size_t escritas = 0;
    while (sqlite3_step(st) == SQLITE_ROW) {
        const char *root = (const char *)sqlite3_column_text(st, 0);
        if (!root) continue;

        size_t h = 0;
        for (const char *p = root; *p; p++) h = h * 131 + (unsigned char)*p;
        h %= 1 << 12;
        Familia *frs = NULL;
        size_t n_frs = 0;
        for (Grupo *q = grupos[h]; q; q = q->sig)
            if (strcmp(q->root_key, root) == 0) { frs = q->frs; n_frs = q->n; break; }
        if (frs) qsort(frs, n_frs, sizeof *frs, cmp_familias);

        sqlite3_reset(stc);
        sqlite3_bind_text(stc, 1, root, -1, SQLITE_TRANSIENT);
        int cuenta = sqlite3_step(stc) == SQLITE_ROW ? sqlite3_column_int(stc, 0) : 0;

        Buffer b; buf_init(&b, 8192);
        construir_raiz(&b, st, frs, n_frs, cuenta, fecha, hoy);

        /* sinónimos: root_clean, la raíz sin √, y las familias, con niggahitas */
        Lista sins; lista_texto_init(&sins);
        ConjuntoTexto *vistos = conjunto_nuevo(64);
        char *limpio = root_limpio(root);
        Buffer sin_signo; buf_init(&sin_signo, 64);
        sustituir_en(&sin_signo, root, "√", "");
        Buffer limpio_sin; buf_init(&limpio_sin, 64);
        sustituir_en(&limpio_sin, limpio, "√", "");
        lista_texto_add_unico(&sins, vistos, limpio);
        lista_texto_add_unico(&sins, vistos, sin_signo.datos);
        lista_texto_add_unico(&sins, vistos, limpio_sin.datos);
        for (size_t i = 0; i < n_frs; i++) {
            lista_texto_add_unico(&sins, vistos, frs[i].root_family);
            Buffer f_sin; buf_init(&f_sin, 64);
            sustituir_en(&f_sin, frs[i].root_family, "√", "");
            lista_texto_add_unico(&sins, vistos, f_sin.datos);
            free(f_sin.datos);
        }
        free(limpio); free(sin_signo.datos); free(limpio_sin.datos);
        conjunto_liberar(vistos);

        dict_add(dicc, root, b.datos, b.largo, sins.items, sins.n);
        escritas++;
    }
    sqlite3_finalize(st);
    sqlite3_finalize(stc);
    return escritas;
}

/* Vuelca cada entrada a un archivo, para comparar con la referencia de
 * Python igual que se hace con las entradas normales. */
static void volcar(const char *dir, const char *prefijo, const char *palabra,
                   const char *datos, size_t n) {
    char nombre[512];
    size_t k = 0;
    for (const char *p = palabra; *p && k + 1 < sizeof nombre; p++)
        nombre[k++] = (*p == '/' || *p == ' ') ? '_' : *p;
    nombre[k] = '\0';
    char ruta[1024];
    snprintf(ruta, sizeof ruta, "%s/%s_%s.html", dir, prefijo, nombre);
    FILE *f = fopen(ruta, "wb");
    if (!f) return;
    fwrite(datos, 1, n, f);
    fclose(f);
}

size_t volcar_raices(sqlite3 *db, const char *dir, const char *fecha,
                     const char *hoy) {
    Diccionario *d = dict_nuevo(1024);
    size_t n = exportar_raices(db, d, fecha, hoy);
    for (size_t i = 0; i < dict_cuantos(d); i++) {
        const char *palabra; const char *datos; size_t largo;
        dict_articulo(d, i, &palabra, &datos, &largo);
        volcar(dir, "raiz", palabra, datos, largo);
    }
    dict_liberar(d);
    return n;
}

size_t volcar_epd(sqlite3 *db, const char *dir, size_t limite) {
    Diccionario *d = dict_nuevo(90000);
    size_t n = exportar_epd(db, d);
    size_t tope = limite && limite < dict_cuantos(d) ? limite : dict_cuantos(d);
    for (size_t i = 0; i < tope; i++) {
        const char *palabra; const char *datos; size_t largo;
        dict_articulo(d, i, &palabra, &datos, &largo);
        volcar(dir, "epd", palabra, datos, largo);
    }
    dict_liberar(d);
    return n;
}

/* ---------- EPD ---------- */

size_t exportar_epd(sqlite3 *db, Diccionario *dicc) {
    sqlite3_stmt *st = NULL;
    if (sqlite3_prepare_v2(db,
            "SELECT lookup_key, epd FROM lookup WHERE epd != ''", -1, &st, NULL)
        != SQLITE_OK)
        return 0;

    /* la cabecera no lleva variables por entrada: se construye una vez */
    Buffer h; buf_init(&h, 2048);
    buf_add(&h, "<!DOCTYPE html>\n<html lang=\"en\">\n    <head>\n"
                "        <meta charset=\"utf-8\">\n"
                "        <link href=\"dpd-css-and-fonts.css\" rel=\"stylesheet\">\n"
                "        <title>Digital Pāḷi Dictionary</title>\n        <style>\n");
    buf_add(&h, css_primary);
    buf_add(&h, "\n        </style>\n    </head>");
    Buffer cabecera; buf_init(&cabecera, 2048);
    buf_add_squashed(&cabecera, h.datos);
    free(h.datos);

    size_t escritas = 0;
    while (sqlite3_step(st) == SQLITE_ROW) {
        const char *clave = (const char *)sqlite3_column_text(st, 0);
        const char *epd = (const char *)sqlite3_column_text(st, 1);
        if (!clave || !epd) continue;

        /* epd_unpack: [["lema","pos","significado"], ...] */
        Buffer cuerpo; buf_init(&cuerpo, 1024);
        buf_add(&cuerpo, "<body>\n<div class ='dpd'><p>");
        Lista campos; lista_texto_init(&campos);
        Buffer texto; buf_init(&texto, 256);
        int en_cadena = 0;
        for (const char *p = epd; *p; p++) {
            if (*p == '"' && (p == epd || p[-1] != '\\')) {
                if (en_cadena) { lista_texto_add(&campos, texto.datos); }
                texto.largo = 0; texto.datos[0] = '\0';
                en_cadena = !en_cadena;
            } else if (en_cadena) {
                if (*p == '\\' && p[1]) p++;
                buf_add_n(&texto, p, 1);
            }
        }
        for (size_t i = 0; i + 2 < campos.n + 1; i += 3) {
            if (i) buf_add(&cuerpo, "<br>");
            buf_add(&cuerpo, "<b class='epd'>"); buf_add(&cuerpo, campos.items[i]);
            buf_add(&cuerpo, "</b> "); buf_add(&cuerpo, campos.items[i + 1]);
            buf_add(&cuerpo, ". "); buf_add(&cuerpo, campos.items[i + 2]);
        }
        buf_add(&cuerpo, "</p></div>\n</body></html>");
        lista_texto_liberar(&campos);
        free(texto.datos);

        minificar_buffer(&cuerpo);

        Buffer entrada; buf_init(&entrada, cabecera.largo + cuerpo.largo + 8);
        buf_add_n(&entrada, cabecera.datos, cabecera.largo);
        buf_add_n(&entrada, cuerpo.datos, cuerpo.largo);
        free(cuerpo.datos);

        dict_add(dicc, clave, entrada.datos, entrada.largo, NULL, 0);
        escritas++;
    }
    free(cabecera.datos);
    sqlite3_finalize(st);
    return escritas;
}
