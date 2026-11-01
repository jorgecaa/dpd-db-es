#!/usr/bin/env bash
# preload-db.sh — DESACTIVADO el 2026-09-18. No hace nada a propósito.
#
# La db se sirve desde disco: dpd.db es un archivo real y /dev/shm/dpd.db no se
# crea. Motivo: la copia en RAM ocupaba 2,18 GB de memoria no reclamable y
# añadía ~40 s a cada arranque, mientras el kernel ya mantiene en page cache
# —y puede recuperar bajo presión— las páginas que se consultan.
#
# dpd-webapp.service sigue llamando a este script como ExecStartPre (ignora su
# salida, pero aborta el arranque si devuelve algo distinto de 0), así que el
# no-op tiene que salir con 0. Quitar esa línea de la unidad es equivalente y
# más limpio, pero requiere root.
#
# El script original, que copiaba a RAM y conmutaba dpd.db a un enlace, está en
# preload-db.sh.orig, al lado. Para revertir:
#
#     mv deploy/preload-db.sh.orig deploy/preload-db.sh
#
# y reiniciar el servicio (la unidad no ha cambiado).
exit 0
