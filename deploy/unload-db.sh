#!/usr/bin/env bash
# unload-db.sh — devuelve dpd.db al disco y libera la copia en RAM.
#
# Lo ejecuta dpd-webapp.service como ExecStopPost, y por eso corre también
# cuando el arranque ha fallado: tiene que ser idempotente y no dar error si
# no hay nada que deshacer. Rutas, las mismas que preload-db.sh.
#
# Nunca borra un dpd.db que sea un archivo de verdad: sólo retira el enlace.
set -euo pipefail

REPO_DIR="/home/jorge/Code/dpd-db"
DB_DISK="${REPO_DIR}/dpd.db"
DB_BACKUP="${REPO_DIR}/dpd.db.disk"
DB_RAM="/dev/shm/dpd.db"

log() { echo "[unload-db] $*"; }

if [[ -L "$DB_DISK" ]]; then
  if [[ -f "$DB_BACKUP" ]]; then
    rm -f "$DB_DISK"
    mv "$DB_BACKUP" "$DB_DISK"
    log "dpd.db restaurado desde el respaldo"
  else
    # el respaldo no está: la copia en RAM es el único original que queda,
    # así que se baja a disco en vez de tirarla.
    if [[ -f "$DB_RAM" && -s "$DB_RAM" ]]; then
      log "no hay respaldo; bajando la copia de RAM a disco"
      cp "$DB_RAM" "${DB_DISK}.recuperado"
      rm -f "$DB_DISK"
      mv "${DB_DISK}.recuperado" "$DB_DISK"
    else
      log "AVISO: enlace sin respaldo ni copia en RAM utilizable; se deja como está"
      exit 0
    fi
  fi
elif [[ -f "$DB_BACKUP" && ! -e "$DB_DISK" ]]; then
  # enlace ya retirado por otro, pero el respaldo sigue fuera de sitio
  mv "$DB_BACKUP" "$DB_DISK"
  log "dpd.db restaurado desde el respaldo"
fi

rm -f "$DB_RAM" "${DB_RAM}.parcial"
log "RAM liberada"
