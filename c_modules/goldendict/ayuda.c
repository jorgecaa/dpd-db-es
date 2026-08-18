/* Las entradas de ayuda: abreviaturas, abreviaturas de otros diccionarios,
 * ayuda, bibliografía y agradecimientos.
 *
 * Reproduce exporter/goldendict/export_help.py con sus plantillas
 * `help_abbrev.jinja`, `help_abbrev_other.jinja` y `help_help.jinja`. Todas
 * comparten la cabecera plana con el estilo "secondary", que se construye una
 * sola vez porque no lleva variables por entrada.
 *
 * La bibliografía y los agradecimientos no usan plantilla: Python concatena su
 * html a mano, así que aquí se hace igual.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "piloto.h"

/* ---------- lectura de TSV con cabecera ---------- */

typedef struct {
    Lista campos;      /* nombres de columna */
    Lista celdas;      /* filas * columnas, en orden */
    size_t n_filas;
} Tsv;

static void tsv_liberar(Tsv *t) {
    lista_texto_liberar(&t->campos);
    lista_texto_liberar(&t->celdas);
}

/* Lector con las reglas del módulo csv de Python: las celdas pueden ir entre
 * comillas, "" es una comilla literal, y dentro de las comillas caben saltos
 * de línea — help.tsv los usa, así que un lector ingenuo parte una fila en
 * varias y saca entradas de más. */
static int tsv_leer(const char *ruta, Tsv *t) {
    FILE *f = fopen(ruta, "rb");
    if (!f) return 0;
    fseek(f, 0, SEEK_END);
    long tam = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *datos = malloc((size_t)tam + 1);
    size_t leidos = fread(datos, 1, (size_t)tam, f);
    datos[leidos] = '\0';
    fclose(f);

    lista_texto_init(&t->campos);
    lista_texto_init(&t->celdas);
    t->n_filas = 0;

    Buffer celda;
    buf_init(&celda, 256);
    int primera_fila = 1;
    int entre_comillas = 0;
    int celda_iniciada = 0;   /* para no tomar la línea final vacía como fila */
    size_t en_fila = 0;

    for (const char *p = datos;; p++) {
        if (entre_comillas) {
            if (*p == '\0') break;
            if (*p == '"') {
                if (p[1] == '"') { buf_add_n(&celda, p, 1); p++; }
                else entre_comillas = 0;
            } else {
                buf_add_n(&celda, p, 1);   /* incluidos los saltos de línea */
            }
            continue;
        }
        if (*p == '"' && !celda.largo) { entre_comillas = 1; celda_iniciada = 1; continue; }
        if (*p == '\t' || *p == '\n' || *p == '\0') {
            int fin_de_fila = (*p != '\t');
            if (celda_iniciada || celda.largo || !fin_de_fila) {
                if (primera_fila) lista_texto_add(&t->campos, celda.datos);
                else lista_texto_add(&t->celdas, celda.datos);
                en_fila++;
            }
            celda.largo = 0;
            celda.datos[0] = '\0';
            celda_iniciada = 0;
            if (fin_de_fila) {
                if (en_fila) {
                    if (!primera_fila) {
                        /* completa si el TSV recorta columnas vacías finales */
                        while (en_fila < t->campos.n) {
                            lista_texto_add(&t->celdas, "");
                            en_fila++;
                        }
                        t->n_filas++;
                    } else {
                        primera_fila = 0;
                    }
                }
                en_fila = 0;
            }
            if (*p == '\0') break;
        } else if (*p != '\r') {
            buf_add_n(&celda, p, 1);
            celda_iniciada = 1;
        }
    }
    free(celda.datos);
    free(datos);
    return 1;
}

static const char *tsv_celda(const Tsv *t, size_t fila, const char *columna) {
    for (size_t c = 0; c < t->campos.n; c++)
        if (strcmp(t->campos.items[c], columna) == 0) {
            size_t i = fila * t->campos.n + c;
            return i < t->celdas.n ? t->celdas.items[i] : "";
        }
    return "";
}

/* ---------- cabecera "secondary", una sola vez ---------- */

static Buffer cabecera_ayuda;
static int cabecera_lista = 0;

static void preparar_cabecera(const char *css_secondary) {
    Buffer h;
    buf_init(&h, 2048);
    buf_add(&h, "<!DOCTYPE html>\n<html lang=\"en\">\n    <head>\n"
                "        <meta charset=\"utf-8\">\n"
                "        <link href=\"dpd-css-and-fonts.css\" rel=\"stylesheet\">\n"
                "        <title>Digital Pāḷi Dictionary</title>\n        <style>\n");
    buf_add(&h, css_secondary);
    buf_add(&h, "\n        </style>\n    </head>");
    buf_init(&cabecera_ayuda, 2048);
    buf_add_squashed(&cabecera_ayuda, h.datos);
    free(h.datos);
    cabecera_lista = 1;
}

static void entrada_con_cabecera(Diccionario *dicc, const char *palabra,
                                 Buffer *cuerpo, char **sinonimos, size_t n_sin) {
    minificar_buffer(cuerpo);
    Buffer completa;
    buf_init(&completa, cabecera_ayuda.largo + cuerpo->largo + 8);
    buf_add_n(&completa, cabecera_ayuda.datos, cabecera_ayuda.largo);
    buf_add_n(&completa, cuerpo->datos, cuerpo->largo);
    free(cuerpo->datos);
    dict_add(dicc, palabra, completa.datos, completa.largo, sinonimos, n_sin);
}

/* ---------- filas de las tablas de ayuda ---------- */

static void fila_ayuda(Buffer *b, const char *th, const char *td) {
    buf_add(b, "        <tr>\n            <th>");
    buf_add(b, T(th));
    buf_add(b, "</th>\n            <td>");
    buf_add(b, td);
    buf_add(b, "</td>\n        </tr>\n");
}

/* ---------- abreviaturas ---------- */

static size_t abreviaturas(Diccionario *dicc, const char *ruta) {
    Tsv t;
    if (!tsv_leer(ruta, &t)) return 0;
    for (size_t f = 0; f < t.n_filas; f++) {
        const char *abrev = tsv_celda(&t, f, "abbrev");
        Buffer c;
        buf_init(&c, 1024);
        buf_add(&c, "<body>\n<div class=\"tertiary\">\n    <table class=\"help\">\n");
        buf_add(&c, "        <tr>\n            <th>");
        buf_add(&c, T("Abbreviation"));
        buf_add(&c, "</th>\n            <td><b>");
        buf_add(&c, abrev);
        buf_add(&c, "</b></td>\n        </tr>\n");
        fila_ayuda(&c, "Meaning", tsv_celda(&t, f, "meaning"));
        const char *pali = tsv_celda(&t, f, "pāli");
        if (*pali) fila_ayuda(&c, "Pāḷi", pali);
        const char *ejemplo = tsv_celda(&t, f, "example");
        if (*ejemplo) fila_ayuda(&c, "Example", ejemplo);
        const char *info = tsv_celda(&t, f, "explanation");
        if (*info) fila_ayuda(&c, "Information", info);
        buf_add(&c, "    </table>\n</div>\n</body></html>");
        entrada_con_cabecera(dicc, abrev, &c, NULL, 0);
    }
    size_t n = t.n_filas;
    tsv_liberar(&t);
    return n;
}

/* ---------- abreviaturas de otros diccionarios ---------- */

static size_t abreviaturas_otras(Diccionario *dicc, const char *ruta) {
    Tsv t;
    if (!tsv_leer(ruta, &t)) return 0;

    /* agrupadas por abreviatura, conservando el orden de aparición */
    Lista claves;
    lista_texto_init(&claves);
    ConjuntoTexto *vistas = conjunto_nuevo(1024);
    for (size_t f = 0; f < t.n_filas; f++) {
        const char *k = tsv_celda(&t, f, "abbreviation");
        if (!*k) continue;
        lista_texto_add_unico(&claves, vistas, k);
    }

    for (size_t i = 0; i < claves.n; i++) {
        Buffer c;
        buf_init(&c, 1024);
        buf_add(&c, "<body>\n<div class=\"tertiary\">\n    <table class=\"help\">\n");
        buf_add(&c, "        <tr>\n            <th>");
        buf_add(&c, T("Abbreviation"));
        buf_add(&c, "</th>\n            <td><b>");
        buf_add(&c, claves.items[i]);
        buf_add(&c, "</b></td>\n        </tr>\n");
        for (size_t f = 0; f < t.n_filas; f++) {
            if (strcmp(tsv_celda(&t, f, "abbreviation"), claves.items[i]) != 0) continue;
            buf_add(&c, "        <tr>\n            <th>");
            buf_add(&c, tsv_celda(&t, f, "source"));
            buf_add(&c, "</th>\n            <td>");
            buf_add(&c, tsv_celda(&t, f, "meaning"));
            const char *notas = tsv_celda(&t, f, "notes");
            if (*notas) {
                buf_add(&c, " <span class=\"gray\">");
                buf_add(&c, notas);
                buf_add(&c, "</span>");
            }
            buf_add(&c, "</td>\n        </tr>\n");
        }
        buf_add(&c, "    </table>\n</div>\n</body></html>");
        entrada_con_cabecera(dicc, claves.items[i], &c, NULL, 0);
    }
    size_t n = claves.n;
    lista_texto_liberar(&claves);
    conjunto_liberar(vistas);
    tsv_liberar(&t);
    return n;
}

/* ---------- ayuda ---------- */

static size_t ayudas(Diccionario *dicc, const char *ruta) {
    Tsv t;
    if (!tsv_leer(ruta, &t)) return 0;
    for (size_t f = 0; f < t.n_filas; f++) {
        const char *palabra = tsv_celda(&t, f, "help");
        Buffer c;
        buf_init(&c, 1024);
        buf_add(&c, "<body>\n<div class=\"tertiary\">\n    <table class=\"help\">\n");
        buf_add(&c, "        <tr>\n            <th>");
        buf_add(&c, T("Help"));
        buf_add(&c, "</th>\n            <td><b>");
        buf_add(&c, palabra);
        buf_add(&c, "</b></td>\n        </tr>\n");
        fila_ayuda(&c, "Meaning", tsv_celda(&t, f, "meaning"));
        buf_add(&c, "    </table>\n</div>\n</body></html>");
        entrada_con_cabecera(dicc, palabra, &c, NULL, 0);
    }
    size_t n = t.n_filas;
    tsv_liberar(&t);
    return n;
}

/* ---------- bibliografía ---------- */

static size_t bibliografia(Diccionario *dicc, const char *ruta) {
    Tsv t;
    if (!tsv_leer(ruta, &t)) return 0;
    Buffer c;
    buf_init(&c, 8192);
    buf_add(&c, "<body><div class='tertiary'><h2>");
    buf_add(&c, T("Bibliography"));
    buf_add(&c, "</h2>");
    int lista_abierta = 0;
    for (size_t f = 0; f < t.n_filas; f++) {
        const char *categoria = tsv_celda(&t, f, "category");
        if (*categoria) {
            if (lista_abierta) buf_add(&c, "</ul>");
            buf_add(&c, "<h3 class='dpd'>");
            buf_add(&c, categoria);
            buf_add(&c, "</h3><ul>");
            lista_abierta = 1;
        }
        const char *apellido = tsv_celda(&t, f, "surname");
        if (*apellido) { buf_add(&c, "<li><b>"); buf_add(&c, apellido); buf_add(&c, "</b>"); }
        const char *nombre = tsv_celda(&t, f, "firstname");
        if (*nombre) { buf_add(&c, ", "); buf_add(&c, nombre); }
        const char *anyo = tsv_celda(&t, f, "year");
        if (*anyo) { buf_add(&c, ", "); buf_add(&c, anyo); }
        const char *titulo = tsv_celda(&t, f, "title");
        if (*titulo) { buf_add(&c, ". <i>"); buf_add(&c, titulo); buf_add(&c, "</i>"); }
        const char *ciudad = tsv_celda(&t, f, "city");
        const char *editorial = tsv_celda(&t, f, "publisher");
        if (*ciudad && *editorial) {
            buf_add(&c, ", "); buf_add(&c, ciudad);
            buf_add(&c, ": "); buf_add(&c, editorial);
        } else if (!*ciudad && *editorial) {
            buf_add(&c, ", "); buf_add(&c, editorial);
        }
        const char *sitio = tsv_celda(&t, f, "site");
        if (*sitio) {
            buf_add(&c, ", "); buf_add(&c, T("accessed through"));
            buf_add(&c, " <a href='"); buf_add(&c, sitio);
            buf_add(&c, "'  target='_blank'>"); buf_add(&c, sitio); buf_add(&c, "</a>");
        }
        if (*apellido) buf_add(&c, "</li>");
    }
    if (lista_abierta) buf_add(&c, "</ul>");
    buf_add(&c, "</div></body></html>");

    Lista sins;
    lista_texto_init(&sins);
    lista_texto_add(&sins, "dpd bibliography");
    lista_texto_add(&sins, "bibliography");
    lista_texto_add(&sins, "bib");
    entrada_con_cabecera(dicc, "bibliography", &c, sins.items, sins.n);
    tsv_liberar(&t);
    return 1;
}

/* ---------- agradecimientos ---------- */

static size_t agradecimientos(Diccionario *dicc, const char *ruta) {
    Tsv t;
    if (!tsv_leer(ruta, &t)) return 0;
    Buffer c;
    buf_init(&c, 8192);
    buf_add(&c, "<body><div class='tertiary'>");
    int lista_abierta = 0;
    for (size_t f = 0; f < t.n_filas; f++) {
        const char *categoria = tsv_celda(&t, f, "category");
        const char *que = tsv_celda(&t, f, "what");
        if (*categoria) {
            if (lista_abierta) buf_add(&c, "</ul>");
            buf_add(&c, "<h2>"); buf_add(&c, categoria); buf_add(&c, "</h2>");
            buf_add(&c, "<p>"); buf_add(&c, que); buf_add(&c, "</p>");
            buf_add(&c, "<ul>");
            lista_abierta = 1;
        }
        const char *quien = tsv_celda(&t, f, "who");
        if (*quien) { buf_add(&c, "<li><b>"); buf_add(&c, quien); buf_add(&c, "</b>"); }
        const char *donde = tsv_celda(&t, f, "where");
        if (*donde) { buf_add(&c, " "); buf_add(&c, donde); }
        if (*que && !*categoria) { buf_add(&c, " "); buf_add(&c, que); }
        if (*quien) buf_add(&c, "</li>");
    }
    if (lista_abierta) buf_add(&c, "</ul>");
    buf_add(&c, "</div></body></html>");

    Lista sins;
    lista_texto_init(&sins);
    lista_texto_add(&sins, "dpd thanks");
    lista_texto_add(&sins, "thankyou");
    lista_texto_add(&sins, "thanks");
    lista_texto_add(&sins, "anumodana");
    entrada_con_cabecera(dicc, "thanks", &c, sins.items, sins.n);
    tsv_liberar(&t);
    return 1;
}

/* ---------- punto de entrada ---------- */

size_t exportar_ayuda(Diccionario *dicc, const char *css_secondary) {
    if (!cabecera_lista) preparar_cabecera(css_secondary);
    /* mismo orden que generate_help_html */
    size_t n = 0;
    n += abreviaturas(dicc, "shared_data/reference/abbreviations.tsv");
    n += ayudas(dicc, "shared_data/reference/help.tsv");
    n += bibliografia(dicc, "shared_data/reference/bibliography.tsv");
    n += agradecimientos(dicc, "shared_data/reference/thanks.tsv");
    n += abreviaturas_otras(dicc, "shared_data/reference/abbreviations_other.tsv");
    return n;
}
