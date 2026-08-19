#!/usr/bin/env bash
# Monta el entorno de análisis del español, aparte del proyecto de upstream.
#
#   ./analisis/preparar.sh
#
# Deja tres cosas en analisis/, todas fuera del control de versiones porque son
# descargas de terceros o derivados suyos:
#
#   .venv/          spaCy con el modelo es_core_news_sm
#   es.dic, es.aff  diccionario español de LibreOffice, para hunspell
#   lexico_es.json  categoría léxica de cada lema, derivada de Apertium
#
# Es idempotente: lo que ya está no se vuelve a bajar.
#
# Para qué sirve cada pieza, que no es intercambiable:
#
#   - hunspell dice si una palabra española EXISTE. Es lo único que caza las
#     erratas («accion», «murio», «ensenanza»).
#   - El léxico de Apertium dice qué CATEGORÍAS puede tener un lema. Es lo único
#     que distingue «destellante» (solo adjetivo) de «consonante» (también
#     sustantivo), y por tanto lo único que detecta un adjetivo puesto donde el
#     inglés nombra una acción.
#   - spaCy etiqueta en contexto. Medido sobre 20 glosas conocidas acierta 15,
#     peor que el léxico (16), porque una glosa de diccionario no es texto
#     corrido y porque el español nominaliza adjetivos sin más: con artículo
#     delante, «el errante» es un sustantivo de pleno derecho. Sirve para
#     analizar frases, no para juzgar palabras sueltas.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$RAIZ/.venv"
METADIX="https://raw.githubusercontent.com/apertium/apertium-spa/master/apertium-spa.spa.metadix"
DICC="https://raw.githubusercontent.com/LibreOffice/dictionaries/master/es"

log() { echo "[análisis] $*"; }
die() { echo "[análisis] ERROR: $*" >&2; exit 1; }

command -v hunspell >/dev/null || die "falta hunspell (dnf install hunspell)"

# El modelo se instala por URL en vez de con `spacy download`: esa orden pasa
# por la CLI de spaCy, que arrastra click, y con Python 3.14 —el del sistema—
# revienta por esa dependencia. Se fija 3.13, que es la versión donde spaCy 3.8
# está probado; uv la descarga si no está.
MODELO="https://github.com/explosion/spacy-models/releases/download/es_core_news_sm-3.8.0/es_core_news_sm-3.8.0-py3-none-any.whl"

if [ ! -d "$VENV" ]; then
    log "creando entorno virtual (Python 3.13)"
    if command -v uv >/dev/null; then
        uv venv --python 3.13 "$VENV" >/dev/null
        log "instalando spaCy y el modelo español"
        uv pip install --quiet --python "$VENV/bin/python" spacy "es_core_news_sm@$MODELO"
    else
        python3 -m venv "$VENV"
        "$VENV/bin/pip" install --quiet spacy "es_core_news_sm@$MODELO"
    fi
else
    log "entorno virtual ya montado"
fi

for parte in dic aff; do
    if [ ! -s "$RAIZ/es.$parte" ]; then
        log "descargando diccionario de LibreOffice (es.$parte)"
        curl -sfL -o "$RAIZ/es.$parte" "$DICC/es_ES.$parte" || die "no se pudo descargar es.$parte"
    fi
done

if [ ! -s "$RAIZ/lexico_es.json" ]; then
    log "descargando el léxico de Apertium"
    curl -sfL -o "$RAIZ/spa.metadix" "$METADIX" || die "no se pudo descargar el léxico"
    log "extrayendo categorías léxicas"
    "$VENV/bin/python" "$RAIZ/construir_lexico.py"
    rm -f "$RAIZ/spa.metadix"
else
    log "léxico ya construido"
fi

log "listo. Comprobación:"
"$VENV/bin/python" "$RAIZ/lexico.py" destellante consonante resplandor accion
