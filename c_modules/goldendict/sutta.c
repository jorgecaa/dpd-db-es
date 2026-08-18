/* Sub-paso 3f: la seccion sutta_info.
 *
 * El HTML lo emite sutta_gen.c (generado desde la plantilla ya traducida);
 * este archivo aporta lo que la plantilla asume hecho: el cargador de la
 * tabla sutta_info (resolviendo alias), las 18 propiedades calculadas del
 * modelo SuttaInfo y los ayudantes de cadena que usan.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "sqlite3.h"
#include "piloto.h"

int SHOW_TBW = 1;   /* config.ini [dictionary] show_tbw */

/* ---------- estructura con columnas y calculadas ya resueltas ---------- */

typedef struct Sutta {
    /* columnas */
    char *dpd_sutta, *dpd_sutta_var, *dpd_code, *book_code;
    char *cst_code, *cst_nikaya, *cst_book, *cst_section, *cst_vagga,
         *cst_sutta, *cst_paranum, *cst_m_page, *cst_v_page, *cst_p_page,
         *cst_t_page, *cst_file;
    char *sc_code, *sc_book, *sc_vagga, *sc_sutta, *sc_eng_sutta, *sc_blurb,
         *sc_file_path;
    char *dpr_link;
    char *bjt_sutta_code, *bjt_web_code, *bjt_filename, *bjt_book_id,
         *bjt_page_num, *bjt_pitaka, *bjt_nikaya, *bjt_major_section,
         *bjt_book, *bjt_minor_section, *bjt_vagga, *bjt_sutta;
    char *dv_pts, *dv_main_theme, *dv_subtopic, *dv_summary, *dv_similes,
         *dv_key_excerpt1, *dv_key_excerpt2, *dv_stage, *dv_training,
         *dv_aspect, *dv_teacher, *dv_audience, *dv_method, *dv_length,
         *dv_prominence, *dv_nikayas_parallels, *dv_agamas_parallels,
         *dv_taisho_parallels, *dv_sanskrit_parallels, *dv_vinaya_parallels,
         *dv_others_parallels, *dv_partial_parallels_na,
         *dv_partial_parallels_all, *dv_suggested_suttas;
    /* calculadas (cadenas vacias cuando en Python serian None) */
    char *sc_card_link, *sc_pali_link, *sc_eng_link, *sc_vagga_link,
         *sc_github, *sc_express_link, *dhamma_gift, *tbw, *tbw_legacy,
         *sc_voice_link, *s_4nt_link, *tpp_org, *bjt_github_link,
         *bjt_tipitaka_lk_link, *bjt_open_tipitaka_lk_link;
    char *has_tpr;              /* "1" o "" — la plantilla solo lo consulta */
    char *dv_exists;            /* idem */
    char *dv_parallels_exists;  /* idem */
} Sutta;

/* ---------- ayudantes ---------- */

static int vacio_no(const char *s) { return s && *s; }

static char *fmt(const char *formato, ...);
#include <stdarg.h>
static char *fmt(const char *formato, ...) {
    va_list ap;
    va_start(ap, formato);
    char *out = NULL;
    if (vasprintf(&out, formato, ap) < 0) { fprintf(stderr, "sin memoria\n"); exit(1); }
    va_end(ap);
    return out;
}

static const char *minusculas(const char *s) {
    static char alm[4][512];
    static int turno = 0;
    char *d = alm[turno = (turno + 1) % 4];
    size_t i = 0;
    for (; s[i] && i + 1 < 512; i++)
        d[i] = (char)tolower((unsigned char)s[i]);
    d[i] = '\0';
    return d;
}

static const char *reemplaza(const char *s, const char *de, const char *a) {
    static char alm[512];
    const char *p = strstr(s, de);
    if (!p) return s;
    snprintf(alm, sizeof alm, "%.*s%s%s", (int)(p - s), s, a, p + strlen(de));
    return alm;
}

/* slug: NFD + quitar marcas Mn + minusculas. En el alfabeto pali eso es una
 * tabla corta de diacriticos -> ascii. */
static const char *DIACRITICOS[][2] = {
    {"ā","a"},{"ī","i"},{"ū","u"},{"ṃ","m"},{"ṁ","m"},{"ṅ","n"},{"ñ","n"},
    {"ṭ","t"},{"ḍ","d"},{"ṇ","n"},{"ḷ","l"},
    {"Ā","a"},{"Ī","i"},{"Ū","u"},{"Ṃ","m"},{"Ṅ","n"},{"Ñ","n"},
    {"Ṭ","t"},{"Ḍ","d"},{"Ṇ","n"},{"Ḷ","l"},
    {NULL,NULL}
};

static void slug(const char *s, char *destino, size_t tam) {
    size_t k = 0;
    while (*s && k + 4 < tam) {
        int puesto = 0;
        for (int i = 0; DIACRITICOS[i][0]; i++) {
            size_t n = strlen(DIACRITICOS[i][0]);
            if (strncmp(s, DIACRITICOS[i][0], n) == 0) {
                destino[k++] = DIACRITICOS[i][1][0];
                s += n; puesto = 1; break;
            }
        }
        if (!puesto) { destino[k++] = (char)tolower((unsigned char)*s); s++; }
    }
    destino[k] = '\0';
}

static int termina_en(const char *s, const char *sufijo) {
    size_t n = strlen(s), m = strlen(sufijo);
    return n >= m && strcmp(s + n - m, sufijo) == 0;
}

/* ---------- is_* ---------- */

static int su_is_samyutta(const Sutta *su) {
    if (!vacio_no(su->dpd_sutta) || !vacio_no(su->dpd_code)) return 0;
    if (strchr(su->dpd_code, '.') || strchr(su->dpd_code, '-')) return 0;
    /* re.sub(r" \d+$", "", dpd_sutta) */
    char base[512];
    snprintf(base, sizeof base, "%s", su->dpd_sutta);
    size_t n = strlen(base);
    size_t i = n;
    while (i > 0 && base[i - 1] >= '0' && base[i - 1] <= '9') i--;
    if (i < n && i > 0 && base[i - 1] == ' ') base[i - 1] = '\0';
    return termina_en(base, "saṃyutta");
}

static int su_is_nipata(const Sutta *su) {
    if (!vacio_no(su->dpd_sutta) || !vacio_no(su->dpd_code)) return 0;
    if (strchr(su->dpd_code, '.') || strchr(su->dpd_code, '-')) return 0;
    return strstr(su->dpd_sutta, "nipāta") != NULL ||
           (vacio_no(su->dpd_sutta_var) && strstr(su->dpd_sutta_var, "nipāta"));
}

static int su_is_vagga(const Sutta *su) {
    const char *nombres[2] = { su->dpd_sutta, su->dpd_sutta_var };
    for (int i = 0; i < 2; i++)
        if (vacio_no(nombres[i]) &&
            (strstr(nombres[i], "vagga") || strstr(nombres[i], "vaggo")))
            return 1;
    return strchr(su->dpd_code, '-') != NULL &&
           (vacio_no(su->cst_vagga) || vacio_no(su->sc_vagga) || vacio_no(su->bjt_vagga));
}

static int su_is_pannasaka(const Sutta *su) {
    const char *nombres[2] = { su->dpd_sutta, su->dpd_sutta_var };
    for (int i = 0; i < 2; i++)
        if (vacio_no(nombres[i]) &&
            (strstr(nombres[i], "paṇṇāsapāḷi") || strstr(nombres[i], "paṇṇāsaka")))
            return 1;
    return 0;
}

/* ---------- el bloque generado ---------- */

#include "sutta_gen.c"

/* ---------- calculadas ---------- */

/* re.sub(r"\d+\.*-*\d*", "", sc_code) */
static char *sc_book_code_de(const char *sc_code) {
    char out[128];
    size_t k = 0;
    const char *p = sc_code;
    while (*p && k + 1 < sizeof out) {
        if (*p >= '0' && *p <= '9') {
            while (*p >= '0' && *p <= '9') p++;
            while (*p == '.') p++;
            while (*p == '-') p++;
            while (*p >= '0' && *p <= '9') p++;
        } else {
            out[k++] = *p++;
        }
    }
    out[k] = '\0';
    return strdup(out);
}

static int en_tpr(const char *dpd_code) {
    if (!vacio_no(dpd_code)) return 0;
    char code[128];
    snprintf(code, sizeof code, "%s", minusculas(dpd_code));
    /* strip() — los codigos no llevan espacios, pero por fidelidad: */
    for (int i = 0; TPR_CODES[i]; i++)
        if (strcmp(code, TPR_CODES[i]) == 0) return 1;
    char *punto = strchr(code, '.');
    if (punto) {
        *punto = '\0';
        for (int i = 0; TPR_CODES[i]; i++)
            if (strcmp(code, TPR_CODES[i]) == 0) return 1;
    }
    return 0;
}

static const char *TBW_LIBROS[] = {"DN","MN","SN","AN","KHP","DHP","UD","ITI",
                                   "SNP","TH","THI",NULL};

static char *tbw_de(const Sutta *su, const char *base_iti, const char *base) {
    char *sbc = sc_book_code_de(su->sc_code);
    char *out = NULL;
    if (vacio_no(su->sc_code) && *sbc) {
        int ok = 0;
        for (int i = 0; TBW_LIBROS[i]; i++)
            if (strcmp(su->book_code, TBW_LIBROS[i]) == 0) { ok = 1; break; }
        if (ok) {
            if (strcmp(sbc, "iti") == 0) out = strdup(base_iti);
            else out = fmt("%s/%s/%s.html", base, minusculas(sbc), minusculas(su->sc_code));
        }
    }
    free(sbc);
    return out ? out : strdup("");
}

static char *sc_vagga_link_de(const Sutta *su) {
    char *sbc = sc_book_code_de(su->sc_code);
    if (!*sbc) { free(sbc); return strdup(""); }
    char book[64];
    snprintf(book, sizeof book, "%s", minusculas(sbc));
    free(sbc);

    if (strcmp(book, "dhp") == 0)
        return fmt("https://suttacentral.net/%s", minusculas(su->dpd_code));

    if (strcmp(book, "sn") == 0 && su_is_samyutta(su)) {
        int n = 0;
        if ((sscanf(su->dpd_code, "SN%d", &n) == 1 ||
             sscanf(su->dpd_code, "sn%d", &n) == 1) && n >= 1) {
            /* el codigo debe ser exactamente SN<digitos> */
            const char *resto = su->dpd_code + 2;
            int solo_digitos = *resto != '\0';
            for (const char *p = resto; *p; p++)
                if (*p < '0' || *p > '9') { solo_digitos = 0; break; }
            if (solo_digitos) {
                const char *slug_sn =
                    n < 12 ? "sagathavaggasamyutta" :
                    n < 22 ? "nidanavaggasamyutta" :
                    n < 35 ? "khandhavaggasamyutta" :
                    n < 45 ? "salayatanavaggasamyutta" :
                    n < 57 ? "mahavaggasamyutta" : NULL;
                if (slug_sn)
                    return fmt("https://suttacentral.net/pitaka/sutta/linked/sn/sn-%s/sn%d",
                               slug_sn, n);
            }
        }
        return strdup("");
    }

    char s[256];
    if (strcmp(book, "sn") == 0 && termina_en(su->dpd_sutta, "saṃyuttapāḷi")) {
        char nombre[256];
        snprintf(nombre, sizeof nombre, "%s", su->dpd_sutta);
        nombre[strlen(nombre) - strlen("pāḷi")] = '\0';
        slug(nombre, s, sizeof s);
        return fmt("https://suttacentral.net/pitaka/sutta/linked/sn/sn-%s", s);
    }
    if (strcmp(book, "mn") == 0 && termina_en(su->dpd_sutta, "paṇṇāsapāḷi")) {
        char nombre[256];
        snprintf(nombre, sizeof nombre, "%s", su->dpd_sutta);
        nombre[strlen(nombre) - strlen("pāḷi")] = '\0';
        slug(nombre, s, sizeof s);
        return fmt("https://suttacentral.net/pitaka/sutta/middle/mn/mn-%s", s);
    }
    if (strcmp(book, "dn") == 0 && termina_en(su->dpd_sutta, "vaggapāḷi")) {
        char nombre[256];
        snprintf(nombre, sizeof nombre, "%s", su->dpd_sutta);
        nombre[strlen(nombre) - strlen("pāḷi")] = '\0';
        slug(nombre, s, sizeof s);
        return fmt("https://suttacentral.net/dn-%s", s);
    }
    if (strcmp(book, "an") == 0 && su_is_nipata(su)) {
        int n = 0;
        if ((sscanf(su->dpd_code, "AN%d", &n) == 1 ||
             sscanf(su->dpd_code, "an%d", &n) == 1)) {
            const char *resto = su->dpd_code + 2;
            int solo_digitos = *resto != '\0';
            for (const char *p = resto; *p; p++)
                if (*p < '0' || *p > '9') { solo_digitos = 0; break; }
            if (solo_digitos)
                return fmt("https://suttacentral.net/pitaka/sutta/numbered/an/an%d", n);
        }
        return strdup("");
    }

    if (!vacio_no(su->sc_vagga)) return strdup("");

    /* re.sub(r"^\d+\.\s*", "", sc_vagga) */
    const char *v = su->sc_vagga;
    const char *p = v;
    while (*p >= '0' && *p <= '9') p++;
    if (p > v && *p == '.') {
        p++;
        while (*p == ' ' || *p == '\t') p++;
        v = p;
    }
    slug(v, s, sizeof s);

    /* prefijo: ^([A-Za-z]+)(\d+)\.(\d+) sobre dpd_code, si no el book */
    char prefijo[64];
    const char *q = su->dpd_code;
    size_t letras = 0;
    while (q[letras] && isalpha((unsigned char)q[letras])) letras++;
    size_t digitos = letras;
    while (q[digitos] && isdigit((unsigned char)q[digitos])) digitos++;
    int con_seccion = letras > 0 && digitos > letras && q[digitos] == '.' &&
                      isdigit((unsigned char)q[digitos + 1]);
    if (con_seccion) {
        snprintf(prefijo, sizeof prefijo, "%.*s", (int)digitos, q);
        for (char *r = prefijo; *r; r++) *r = (char)tolower((unsigned char)*r);
    } else {
        snprintf(prefijo, sizeof prefijo, "%s", book);
    }
    return fmt("https://suttacentral.net/%s-%s", prefijo, s);
}

static char *s_4nt_link_de(const Sutta *su) {
    if (!vacio_no(su->sc_code)) return strdup("");
    char *sbc_bruto = sc_book_code_de(su->sc_code);
    if (!*sbc_bruto) { free(sbc_bruto); return strdup(""); }
    /* rstrip('-') + lower */
    size_t n = strlen(sbc_bruto);
    while (n && sbc_bruto[n - 1] == '-') sbc_bruto[--n] = '\0';
    char book[64];
    snprintf(book, sizeof book, "%s", minusculas(sbc_bruto));
    free(sbc_bruto);
    char code[128];
    snprintf(code, sizeof code, "%s", minusculas(su->sc_code));

    if (strcmp(book, "dn") == 0 || strcmp(book, "mn") == 0)
        return fmt("https://s.4nt.org/%s/%s/index.html", book, code);

    const char *ancla = code;
    for (int i = 0; S4NT_OVERRIDES[i][0]; i++)
        if (strcmp(su->sc_code, S4NT_OVERRIDES[i][0]) == 0) {
            ancla = S4NT_OVERRIDES[i][1];
            break;
        }

    if (strcmp(book, "sn") == 0 || strcmp(book, "an") == 0) {
        size_t bl = strlen(book);
        if (strncmp(code, book, bl) != 0 || !isdigit((unsigned char)code[bl]))
            return strdup("");
        int num = atoi(code + bl);
        char solo[64];
        snprintf(solo, sizeof solo, "%s%d", book, num);
        if (strcmp(code, solo) == 0)
            return fmt("https://s.4nt.org/%s/%s/index.html", book, solo);
        return fmt("https://s.4nt.org/%s/%s/index.html#%s", book, solo, ancla);
    }

    for (int i = 0; S4NT_KN[i]; i++)
        if (strcmp(book, S4NT_KN[i]) == 0)
            return fmt("https://s.4nt.org/kn/%s/index.html#%s", book, ancla);

    return strdup("");
}

static void calcular(Sutta *su) {
    if (vacio_no(su->sc_code)) {
        su->sc_card_link = fmt("https://suttacentral.net/%s", su->sc_code);
        su->sc_pali_link = fmt("https://suttacentral.net/%s/pli/ms", su->sc_code);
        su->sc_eng_link = fmt("https://suttacentral.net/%s/en/sujato", su->sc_code);
        su->sc_express_link = fmt("https://suttacentral.express/%s/en/sujato",
                                  minusculas(su->sc_code));
        su->dhamma_gift = fmt("https://f.dhamma.gift/read/?q=%s", su->sc_code);
        su->sc_voice_link = fmt("https://www.sc-voice.net/#/sutta/%s/en/sujato",
                                minusculas(su->sc_code));
        su->sc_github = fmt(
            "https://github.com/suttacentral/sc-data/blob/main/%s", su->sc_file_path);
    } else {
        su->sc_card_link = strdup("");
        su->sc_pali_link = strdup("");
        su->sc_eng_link = strdup("");
        su->sc_express_link = strdup("");
        su->dhamma_gift = strdup("");
        su->sc_voice_link = strdup("");
        su->sc_github = strdup("");
    }
    su->tbw = tbw_de(su, "https://thebuddhaswords.net/it/it.html",
                     "https://thebuddhaswords.net");
    su->tbw_legacy = tbw_de(su, "https://f.dhamma.gift/bw/it/it.html",
                            "https://f.dhamma.gift/bw");
    su->sc_vagga_link = sc_vagga_link_de(su);
    su->s_4nt_link = s_4nt_link_de(su);

    if (vacio_no(su->cst_code)) {
        /* re.sub(r"romn\/|\.xml", "", cst_file) */
        char limpio[256];
        size_t k = 0;
        for (const char *p = su->cst_file; *p && k + 1 < sizeof limpio; ) {
            if (strncmp(p, "romn/", 5) == 0) { p += 5; continue; }
            if (strncmp(p, ".xml", 4) == 0) { p += 4; continue; }
            limpio[k++] = *p++;
        }
        limpio[k] = '\0';
        su->tpp_org = fmt("https://tipitakapali.org/book/%s#para%s",
                          limpio, su->cst_paranum);
    } else {
        su->tpp_org = strdup("");
    }

    su->bjt_github_link = vacio_no(su->bjt_filename)
        ? fmt("https://github.com/pathnirvana/tipitaka.lk/blob/master/public/static/text/%s.json",
              su->bjt_filename)
        : strdup("");
    su->bjt_tipitaka_lk_link = vacio_no(su->bjt_web_code)
        ? fmt("https://tipitaka.lk/%s", su->bjt_web_code)
        : strdup("");
    su->bjt_open_tipitaka_lk_link = vacio_no(su->bjt_web_code)
        ? fmt("https://open.tipitaka.lk/latn/%s", su->bjt_web_code)
        : strdup("");

    su->has_tpr = strdup(en_tpr(su->dpd_code) ? "1" : "");

    int dvp = vacio_no(su->dv_nikayas_parallels) || vacio_no(su->dv_agamas_parallels)
        || vacio_no(su->dv_taisho_parallels) || vacio_no(su->dv_sanskrit_parallels)
        || vacio_no(su->dv_vinaya_parallels) || vacio_no(su->dv_others_parallels)
        || vacio_no(su->dv_partial_parallels_na) || vacio_no(su->dv_partial_parallels_all);
    su->dv_parallels_exists = strdup(dvp ? "1" : "");
    int dve = dvp || vacio_no(su->dv_main_theme) || vacio_no(su->dv_subtopic)
        || vacio_no(su->dv_stage) || vacio_no(su->dv_training)
        || vacio_no(su->dv_aspect) || vacio_no(su->dv_teacher)
        || vacio_no(su->dv_audience) || vacio_no(su->dv_method)
        || vacio_no(su->dv_length) || vacio_no(su->dv_prominence)
        || vacio_no(su->dv_summary) || vacio_no(su->dv_key_excerpt1)
        || vacio_no(su->dv_key_excerpt2) || vacio_no(su->dv_similes)
        || vacio_no(su->dv_suggested_suttas);
    su->dv_exists = strdup(dve ? "1" : "");
}

/* ---------- cargador ---------- */

static const char *SQL_SUTTA =
    "SELECT dpd_sutta, dpd_sutta_var, dpd_code, book_code,"
    " cst_code, cst_nikaya, cst_book, cst_section, cst_vagga, cst_sutta,"
    " cst_paranum, cst_m_page, cst_v_page, cst_p_page, cst_t_page, cst_file,"
    " sc_code, sc_book, sc_vagga, sc_sutta, sc_eng_sutta, sc_blurb,"
    " sc_file_path, dpr_link,"
    " bjt_sutta_code, bjt_web_code, bjt_filename, bjt_book_id, bjt_page_num,"
    " bjt_piṭaka, bjt_nikāya, bjt_major_section, bjt_book, bjt_minor_section,"
    " bjt_vagga, bjt_sutta,"
    " dv_pts, dv_main_theme, dv_subtopic, dv_summary, dv_similes,"
    " dv_key_excerpt1, dv_key_excerpt2, dv_stage, dv_training, dv_aspect,"
    " dv_teacher, dv_audience, dv_method, dv_length, dv_prominence,"
    " dv_nikayas_parallels, dv_āgamas_parallels, dv_taisho_parallels,"
    " dv_sanskrit_parallels, dv_vinaya_parallels, dv_others_parallels,"
    " dv_partial_parallels_nā, dv_partial_parallels_all, dv_suggested_suttas"
    " FROM sutta_info WHERE dpd_sutta = ?";

/* alias -> canonico, cargado una vez */
typedef struct Alias { char *nombre, *canonico; struct Alias *sig; } Alias;
static Alias *alias_map[1 << 14];

static size_t huella_al(const char *s) {
    size_t h = 1469598103934665603u;
    for (; *s; s++) { h ^= (unsigned char)*s; h *= 1099511628211u; }
    return h % (1 << 14);
}

void sutta_alias_cargar(sqlite3 *db) {
    sqlite3_stmt *st = NULL;
    if (sqlite3_prepare_v2(db, "SELECT dpd_sutta, dpd_sutta_var FROM sutta_info",
                           -1, &st, NULL) != SQLITE_OK)
        return;
    while (sqlite3_step(st) == SQLITE_ROW) {
        const char *canon = (const char *)sqlite3_column_text(st, 0);
        const char *vars = (const char *)sqlite3_column_text(st, 1);
        if (!canon) continue;
        const char *nombres[2] = { canon, NULL };
        for (int i = 0; i < 1; i++) {
            Alias *a = malloc(sizeof *a);
            a->nombre = strdup(nombres[i]);
            a->canonico = strdup(canon);
            size_t h = huella_al(a->nombre);
            a->sig = alias_map[h];
            alias_map[h] = a;
        }
        if (vars && *vars) {
            char *copia = strdup(vars), *g = NULL;
            for (char *t = strtok_r(copia, ";", &g); t; t = strtok_r(NULL, ";", &g)) {
                while (*t == ' ') t++;
                if (!*t) continue;
                Alias *a = malloc(sizeof *a);
                a->nombre = strdup(t);
                a->canonico = strdup(canon);
                size_t h = huella_al(a->nombre);
                a->sig = alias_map[h];
                alias_map[h] = a;
            }
            free(copia);
        }
    }
    sqlite3_finalize(st);
}

static char *col(sqlite3_stmt *st, int c) {
    const unsigned char *v = sqlite3_column_text(st, c);
    return strdup(v ? (const char *)v : "");
}

/* Devuelve NULL si el lema no tiene sutta_info. Liberar con sutta_liberar. */
Sutta *sutta_cargar(sqlite3 *db, const char *lemma_1) {
    const char *canon = NULL;
    for (Alias *a = alias_map[huella_al(lemma_1)]; a; a = a->sig)
        if (strcmp(a->nombre, lemma_1) == 0) { canon = a->canonico; break; }
    if (!canon) return NULL;

    static sqlite3_stmt *st = NULL;
    if (!st && sqlite3_prepare_v2(db, SQL_SUTTA, -1, &st, NULL) != SQLITE_OK)
        return NULL;
    sqlite3_reset(st);
    sqlite3_bind_text(st, 1, canon, -1, SQLITE_TRANSIENT);
    if (sqlite3_step(st) != SQLITE_ROW) return NULL;

    Sutta *su = calloc(1, sizeof *su);
    int c = 0;
    char **campos[] = {
        &su->dpd_sutta, &su->dpd_sutta_var, &su->dpd_code, &su->book_code,
        &su->cst_code, &su->cst_nikaya, &su->cst_book, &su->cst_section,
        &su->cst_vagga, &su->cst_sutta, &su->cst_paranum, &su->cst_m_page,
        &su->cst_v_page, &su->cst_p_page, &su->cst_t_page, &su->cst_file,
        &su->sc_code, &su->sc_book, &su->sc_vagga, &su->sc_sutta,
        &su->sc_eng_sutta, &su->sc_blurb, &su->sc_file_path, &su->dpr_link,
        &su->bjt_sutta_code, &su->bjt_web_code, &su->bjt_filename,
        &su->bjt_book_id, &su->bjt_page_num, &su->bjt_pitaka, &su->bjt_nikaya,
        &su->bjt_major_section, &su->bjt_book, &su->bjt_minor_section,
        &su->bjt_vagga, &su->bjt_sutta,
        &su->dv_pts, &su->dv_main_theme, &su->dv_subtopic, &su->dv_summary,
        &su->dv_similes, &su->dv_key_excerpt1, &su->dv_key_excerpt2,
        &su->dv_stage, &su->dv_training, &su->dv_aspect, &su->dv_teacher,
        &su->dv_audience, &su->dv_method, &su->dv_length, &su->dv_prominence,
        &su->dv_nikayas_parallels, &su->dv_agamas_parallels,
        &su->dv_taisho_parallels, &su->dv_sanskrit_parallels,
        &su->dv_vinaya_parallels, &su->dv_others_parallels,
        &su->dv_partial_parallels_na, &su->dv_partial_parallels_all,
        &su->dv_suggested_suttas,
        NULL
    };
    for (int i = 0; campos[i]; i++) *campos[i] = col(st, c++);
    calcular(su);
    return su;
}

void sutta_liberar(Sutta *su) {
    if (!su) return;
    char **p = (char **)su;
    size_t n = sizeof(Sutta) / sizeof(char *);
    for (size_t i = 0; i < n; i++) free(p[i]);
    free(su);
}

/* ---------- puntos de entrada para el cuerpo ---------- */

int sutta_es_samyutta(const Sutta *su) { return su_is_samyutta(su); }
int sutta_es_vagga(const Sutta *su) { return su_is_vagga(su); }
int sutta_es_nipata(const Sutta *su) { return su_is_nipata(su); }
int sutta_es_pannasaka(const Sutta *su) { return su_is_pannasaka(su); }

void construir_sutta_info(Buffer *b, const Sutta *su, int id,
                          const char *lemma_1, const char *hoy) {
    char id_str[32];
    snprintf(id_str, sizeof id_str, "%d", id);
    char lemma_[512];
    size_t k = 0;
    for (const char *p = lemma_1; *p && k + 1 < sizeof lemma_; p++)
        lemma_[k++] = (*p == ' ' || *p == '.') ? '_' : *p;
    lemma_[k] = '\0';
    Buffer link; buf_init(&link, 256);
    for (const char *p = lemma_1; *p; p++) {
        if (*p == ' ') buf_add(&link, "%20"); else buf_add_n(&link, p, 1);
    }
    emitir_sutta_info(b, su, id_str, lemma_, link.datos, hoy);
    free(link.datos);
}

/* ---------- sutta_codes_list (tools/sutta_codes.py) ---------- */

/* "SN12.5-8" -> SN12.5 SN12.6 SN12.7 SN12.8; sin punto no expande nada. */
static void expandir_rango(const char *codigo, Lista *destino) {
    const char *punto = strchr(codigo, '.');
    const char *guion = strchr(codigo, '-');
    if (!punto || !guion || guion < punto) return;
    char base[128];
    size_t nb = (size_t)(punto - codigo) + 1;
    if (nb >= sizeof base) return;
    memcpy(base, codigo, nb);
    base[nb] = '\0';
    int primero = atoi(punto + 1);
    int ultimo = atoi(guion + 1);
    for (int n = primero; n <= ultimo; n++) {
        char buf[160];
        snprintf(buf, sizeof buf, "%s%d", base, n);
        lista_texto_add(destino, buf);
    }
}

char **sutta_codes_list(const Sutta *su, size_t *cuantos) {
    Lista l;
    lista_texto_init(&l);
    ConjuntoTexto *vistos = conjunto_nuevo(256);

    lista_texto_add_unico(&l, vistos, su->dpd_code);
    if (strchr(su->dpd_code, '-')) {
        Lista r; lista_texto_init(&r);
        expandir_rango(su->dpd_code, &r);
        for (size_t i = 0; i < r.n; i++) lista_texto_add_unico(&l, vistos, r.items[i]);
        lista_texto_liberar(&r);
    }

    if (!su_is_vagga(su) && !su_is_samyutta(su) && !su_is_nipata(su)) {
        lista_texto_add_unico(&l, vistos, su->sc_code);
        if (strchr(su->sc_code, '-')) {
            Lista r; lista_texto_init(&r);
            expandir_rango(su->sc_code, &r);
            for (size_t i = 0; i < r.n; i++) lista_texto_add_unico(&l, vistos, r.items[i]);
            lista_texto_liberar(&r);
        }
        /* THAG/THIG (prefijo de SuttaCentral) -> TH/THI (prefijo de DPD) */
        static const char *PREFIJOS[][2] = {{"THAG", "TH"}, {"THIG", "THI"}, {NULL, NULL}};
        for (int i = 0; PREFIJOS[i][0]; i++) {
            size_t n = strlen(PREFIJOS[i][0]);
            if (strncmp(su->sc_code, PREFIJOS[i][0], n) != 0) continue;
            char sintetico[160];
            snprintf(sintetico, sizeof sintetico, "%s%s", PREFIJOS[i][1], su->sc_code + n);
            lista_texto_add_unico(&l, vistos, sintetico);
            if (strchr(sintetico, '-')) {
                Lista r; lista_texto_init(&r);
                expandir_rango(sintetico, &r);
                for (size_t k = 0; k < r.n; k++)
                    lista_texto_add_unico(&l, vistos, r.items[k]);
                lista_texto_liberar(&r);
            }
            break;
        }
    }

    conjunto_liberar(vistos);
    *cuantos = l.n;
    return l.items;
}
