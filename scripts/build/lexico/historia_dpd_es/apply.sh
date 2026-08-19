#!/usr/bin/env bash
# Instala la capa española sobre el checkout de dpd-db que hay en vendor/.
#
#   ./apply.sh              instala archivos nuevos + parches
#   ./apply.sh --sidecar    además reconstruye dpd_es.db desde el JS traducido
#   ./apply.sh --revert     deshace los parches (los archivos nuevos se quedan)
#
# Es idempotente: si un parche ya está aplicado, lo detecta y no lo repite.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DPD_DB="$RAIZ/vendor/dpd-db"
JS_ES="$RAIZ/vendor/dpd-dictionary-es/js/dpd_ebts_es.js"

log() { echo "[dpd-es] $*"; }
die() { echo "[dpd-es] ERROR: $*" >&2; exit 1; }

# en un submódulo .git es un archivo, no un directorio
[ -e "$DPD_DB/.git" ] || die "falta el submódulo vendor/dpd-db (git submodule update --init)"

REVERT=no
SIDECAR=no
for arg in "$@"; do
    case "$arg" in
        --revert)  REVERT=yes ;;
        --sidecar) SIDECAR=yes ;;
        *) die "opción desconocida: $arg" ;;
    esac
done

# ── archivos nuevos: se copian tal cual, no hay conflicto posible ────────────
if [ "$REVERT" = no ]; then
    log "copiando archivos nativos"
    install -Dm644 "$RAIZ/src/tools/spanish.py" "$DPD_DB/tools/spanish.py"
    install -Dm644 "$RAIZ/src/scripts/build/build_spanish_sidecar.py" \
        "$DPD_DB/scripts/build/build_spanish_sidecar.py"
    install -Dm644 "$RAIZ/src/scripts/build/sanskrit_meanings_es.tsv" \
        "$DPD_DB/scripts/build/sanskrit_meanings_es.tsv"
    install -Dm644 "$RAIZ/src/scripts/build/webapp_ui_es.tsv" \
        "$DPD_DB/scripts/build/webapp_ui_es.tsv"
    install -Dm644 "$RAIZ/src/scripts/build/compounds_es.tsv" \
        "$DPD_DB/scripts/build/compounds_es.tsv"
    # entradas de build_spanish_glossary.py: sin ellas regenera el glosario sin
    # nuestras acepciones propias y sin las correcciones al corpus
    install -Dm644 "$RAIZ/src/scripts/build/senses_es.tsv" \
        "$DPD_DB/scripts/build/senses_es.tsv"
    install -Dm644 "$RAIZ/src/scripts/build/glosario_es_overrides.tsv" \
        "$DPD_DB/scripts/build/glosario_es_overrides.tsv"
    install -Dm644 "$RAIZ/src/scripts/build/material_es_correcciones.tsv" \
        "$DPD_DB/scripts/build/material_es_correcciones.tsv"
    install -Dm755 "$RAIZ/src/scripts/build/build_spanish_glossary.py" \
        "$DPD_DB/scripts/build/build_spanish_glossary.py"
fi

# ── parches sobre archivos de upstream ──────────────────────────────────────
for parche in "$RAIZ"/patches/*.patch; do
    nombre="$(basename "$parche")"
    if [ "$REVERT" = yes ]; then
        if git -C "$DPD_DB" apply --reverse --check "$parche" 2>/dev/null; then
            git -C "$DPD_DB" apply --reverse "$parche"
            log "revertido  $nombre"
        else
            log "no aplicado $nombre (nada que revertir)"
        fi
    elif git -C "$DPD_DB" apply --reverse --check "$parche" 2>/dev/null; then
        log "ya aplicado $nombre"
    elif git -C "$DPD_DB" apply --check "$parche" 2>/dev/null; then
        git -C "$DPD_DB" apply "$parche"
        log "aplicado   $nombre"
    else
        die "$nombre no aplica limpio: upstream ha tocado esas líneas, hay que rehacerlo"
    fi
done

[ "$REVERT" = yes ] && { log "listo (revertido)"; exit 0; }

# ── interruptor en config.ini ───────────────────────────────────────────────
CONFIG="$DPD_DB/config.ini"
if ! grep -q '^\[spanish\]' "$CONFIG" 2>/dev/null; then
    log "añadiendo [spanish] a config.ini"
    cat "$RAIZ/config/spanish.ini" >> "$CONFIG"
fi

# ── sidecar ────────────────────────────────────────────────────────────────
if [ "$SIDECAR" = yes ]; then
    [ -f "$JS_ES" ] || die "falta $JS_ES (git submodule update --init)"
    log "construyendo dpd_es.db"
    (cd "$DPD_DB" && uv run python scripts/build/build_spanish_sidecar.py --source "$JS_ES")
fi

log "listo"
