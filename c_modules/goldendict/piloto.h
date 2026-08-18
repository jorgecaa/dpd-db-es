#ifndef PILOTO_H
#define PILOTO_H

#include <stddef.h>

/* ---------- buffer de cadena que crece ---------- */

typedef struct {
    char  *datos;
    size_t largo;
    size_t capacidad;
} Buffer;

void buf_init(Buffer *b, size_t inicial);
void buf_reservar(Buffer *b, size_t extra);
void buf_add(Buffer *b, const char *s);
void buf_add_n(Buffer *b, const char *s, size_t n);
/* Igual que squash_whitespaces de tools/utils.py */
void buf_add_squashed(Buffer *b, const char *s);
size_t largo_utf8(const char *s);

/* ---------- conjunto de cadenas ---------- */

typedef struct ConjuntoTexto ConjuntoTexto;
void conjunto_liberar(ConjuntoTexto *c);

/* ---------- lista de cadenas ---------- */

typedef struct {
    char **items;
    size_t n, capacidad;
} Lista;

void lista_texto_init(Lista *l);
void lista_texto_add(Lista *l, const char *s);
void lista_texto_add_unico(Lista *l, ConjuntoTexto *vistos, const char *s);
void lista_texto_liberar(Lista *l);

ConjuntoTexto *conjunto_nuevo(size_t capacidad);
void conjunto_add(ConjuntoTexto *c, const char *s);
int  conjunto_contiene(const ConjuntoTexto *c, const char *s);
/* Carga un array JSON de cadenas: ["a","b",...] */
ConjuntoTexto *conjunto_desde_json(const char *json);

/* ---------- una entrada del diccionario ---------- */

typedef struct {
    int         id;
    const char *lemma_1;
    const char *pos;
    const char *grammar;
    const char *meaning_1;
    const char *meaning_lit;
    const char *meaning_2;
    const char *construction;
    const char *derivative;
    const char *suffix;
    const char *phonetic;
    const char *compound_type;
    const char *compound_construction;
    const char *sanskrit;
    const char *root_key;
    const char *root_sign;
    const char *root_base;
    char        root_family_key[256];  /* hibrida: root_key + ' ' + family_root */
    const char *family_root;
    const char *family_word;
    const char *family_compound;
    const char *family_idioms;
    const char *family_set;
    const char *example_1;
    const char *sutta_1;
    const char *source_1;
    const char *example_2;
    const char *sutta_2;
    const char *source_2;
    const char *commentary;
    const char *notes;
    const char *antonym;
    const char *synonym;
    const char *variant;
    const char *inflections_html;
    const char *freq_data;
    const char *plus_case;
    const char *origin;
    const char *neg;
    const char *verb;
    const char *trans;
    const char *var_phonetic;
    const char *var_text;
    const char *cognate;
    const char *link;
    const char *non_ia;
    const char *stem;
    const char *pattern;
    const char *lemma_trad_clean;   /* calculado en el cargador */
    const char *inflections;
    const char *inflections_api_ca_eva_iti;
    const char *inflections_sinhala;
    const char *inflections_devanagari;
    const char *inflections_thai;
} Entrada;

typedef struct {
    const char *root_has_verb;
    const char *root_group;
    const char *root_meaning;
    const char *root_in_comps;
    const char *sanskrit_root;
    const char *sanskrit_root_class;
    const char *sanskrit_root_meaning;
} Raiz;

typedef struct { int male1, male2, female1; } Audio;

/* sutta_info (sutta.c + sutta_gen.c generado) */
typedef struct Sutta Sutta;
extern int SHOW_TBW;
struct sqlite3;
void sutta_alias_cargar(struct sqlite3 *db);
Sutta *sutta_cargar(struct sqlite3 *db, const char *lemma_1);
void sutta_liberar(Sutta *su);
void construir_sutta_info(Buffer *b, const Sutta *su, int id,
                          const char *lemma_1, const char *hoy);
int sutta_es_samyutta(const Sutta *su);
int sutta_es_vagga(const Sutta *su);
int sutta_es_nipata(const Sutta *su);
int sutta_es_pannasaka(const Sutta *su);
char **sutta_codes_list(const Sutta *su, size_t *cuantos);

/* ---------- sinónimos (sinonimos.c) ---------- */

void speech_marks_cargar(const char *ruta);
char **construir_sinonimos(const Entrada *e, const Sutta *su, size_t *cuantos);

/* ---------- StarDict (stardict.c) ---------- */

typedef struct {
    const char *bookname;
    const char *author;
    const char *description;
    const char *website;
    const char *source_lang;
    const char *target_lang;
    const char *date;      /* vacío: se pone la marca de tiempo de ahora */
} InfoDicc;

typedef struct Diccionario Diccionario;

Diccionario *dict_nuevo(size_t capacidad);
void dict_add(Diccionario *d, const char *palabra, char *definicion,
              size_t definicion_n, char **sinonimos, size_t n_sinonimos);
size_t dict_cuantos(const Diccionario *d);
void dict_escribir(Diccionario *d, const char *base, const InfoDicc *info);
void dict_liberar(Diccionario *d);

/* ---------- raíces y EPD (otros.c) ---------- */

void otros_init(const char *ruta_css_variables);
size_t exportar_raices(struct sqlite3 *db, Diccionario *dicc, const char *fecha,
                       const char *hoy);
size_t exportar_epd(struct sqlite3 *db, Diccionario *dicc);
size_t volcar_raices(struct sqlite3 *db, const char *dir, const char *fecha,
                     const char *hoy);
size_t volcar_epd(struct sqlite3 *db, const char *dir, size_t limite);

/* ---------- ayuda (ayuda.c) ---------- */

size_t exportar_ayuda(Diccionario *dicc, const char *css_secondary);
const char *css_reducido(const char *estilo);
void dict_articulo(const Diccionario *d, size_t i, const char **palabra,
                   const char **definicion, size_t *largo);

/* Minifica un buffer entero; no hace nada si el modo está apagado. */
extern int MINIFICAR;
void minificar_buffer(Buffer *b);

/* ---------- contexto compartido ---------- */

typedef struct {
    const ConjuntoTexto *idioms_set;
    const ConjuntoTexto *cf_set;
    const ConjuntoTexto *audio_set;
    const ConjuntoTexto *sutta_info_set;
    const Raiz *raiz_actual;
    const Audio *audio_actual;
} Contexto;

/* ---------- cuerpo (paso 3) ---------- */

void ui_cargar(const char *ruta_tsv);
const char *T(const char *en);
void construir_botones(Buffer *b, const Entrada *e, const Contexto *ctx,
                       const Sutta *su);
void construir_flexiones(Buffer *b, const Entrada *e, const char *hoy);
void espanol_cargar(const char *ruta_sidecar);
void construir_resumen(Buffer *b, const Entrada *e);
const char *superponer_raiz(const char *root, const char *ingles);
const char *superponer_sanscrito(const char *ingles);
void construction_summary(const Entrada *e, const char *meaning_1_en, Buffer *out);
void construir_gramatica(Buffer *b, const Entrada *e, const Raiz *rt,
                         const Audio *audio, const char *hoy);
void construir_ejemplos(Buffer *b, const Entrada *e, const char *hoy);
void construir_secciones_carga(Buffer *b, const Entrada *e, const Contexto *ctx);

/* ---------- cabecera (paso 2) ---------- */

void cabecera_init(const char *ruta_css_variables);
void construir_cabecera(Buffer *b, const Entrada *e, const char *fecha,
                        const ConjuntoTexto *idioms_set,
                        const ConjuntoTexto *cf_set);
void lemma_clean(const char *lemma, char *destino, size_t tam);

#endif
