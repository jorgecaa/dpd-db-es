#!/bin/bash

# Restaura este clon hasta dejar el diccionario respondiendo en español.
#
#   git clone https://github.com/jorgecaa/dpd-db-es.git dpd-db
#   cd dpd-db && ./restore.sh
#
# Existe además de `just restore` porque en una máquina recién puesta no hay
# `just` todavía: esto solo necesita bash y git, comprueba lo que falta y lo
# dice antes de fallar a mitad. El trabajo de verdad lo hace
# scripts/onboarding/restore.py, que es idempotente — repetirlo no rehace lo
# que ya está.
#
#   ./restore.sh              descarga dpd.db si falta (2,1 GB)
#   ./restore.sh --skip-db    falla en vez de descargarla

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$RAIZ"

log()  { echo "[restaurar] $*"; }
fatal() { echo "[restaurar] ERROR: $*" >&2; exit 1; }

# --- que estemos donde creemos ------------------------------------------------

[ -f "scripts/onboarding/restore.py" ] || fatal "esto no parece el repositorio dpd-db"
[ -f "resources/dpd-dictionary-es/js/dpd_ebts_es.js" ] || fatal \
  "falta el material español en resources/; el clon está incompleto"

# --- uv -----------------------------------------------------------------------
#
# No lo instalo yo: bajar y ejecutar un instalador remoto sin preguntar es
# justo lo que un script de restauración no debería hacer a espaldas de nadie.

if ! command -v uv >/dev/null 2>&1; then
  echo "[restaurar] ERROR: falta uv, que es quien gestiona el entorno de Python." >&2
  echo "" >&2
  echo "  Instálalo con:" >&2
  echo "    curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  echo "" >&2
  echo "  o con el gestor de paquetes de tu sistema, y vuelve a ejecutar esto." >&2
  exit 1
fi

# --- espacio ------------------------------------------------------------------
#
# dpd.db ocupa 2,1 GB y se descarga comprimida: quedarse sin disco a mitad deja
# un archivo truncado que luego parece corrupto sin decir por qué.

if [ "${1:-}" != "--skip-db" ] && [ ! -f "dpd.db" ]; then
  libre_kb="$(df -Pk . | awk 'NR==2 {print $4}')"
  if [ "$libre_kb" -lt 5000000 ]; then
    fatal "quedan $((libre_kb / 1024 / 1024)) GB libres y hacen falta unos 5 para dpd.db"
  fi
fi

log "restaurando desde $RAIZ"
exec uv run python scripts/onboarding/restore.py "$@"
