# Piloto en C del exportador de GoldenDict.
#
#   make            compila ./piloto
#   make tablas     regenera ipa_tabla.h, trad_tabla.h y sutta_gen.c
#   make paridad    vuelca la referencia de Python y la compara con la del C
#   make limpiar
#
# Se ejecuta desde la raíz del proyecto o desde aquí; las rutas de datos que
# usa el binario (dpd.db, identity/css, config.ini) son relativas a la raíz.

CC      ?= gcc
CFLAGS  ?= -O2 -Wall -Wextra -D_GNU_SOURCE
RAIZ    := ../..

FUENTES := main.c comun.c cabecera.c cuerpo.c resumen.c gramatica.c sutta.c \
           sinonimos.c stardict.c otros.c ayuda.c
MINLIB  := minificador/target/release/libminificador.a

# SQLite: por defecto se enlaza el del sistema (paquete sqlite-devel /
# libsqlite3-dev). Si no está, apunta SQLITE_AMALGAMA al sqlite3.c de la
# amalgamación oficial (https://sqlite.org/download.html) y se compila dentro:
#   make SQLITE_AMALGAMA=/ruta/a/sqlite3.c
SQLITE_AMALGAMA ?=
ifeq ($(SQLITE_AMALGAMA),)
  SQLITE_CFLAGS :=
  SQLITE_OBJ    :=
  SQLITE_LIBS   := -lsqlite3
else
  SQLITE_CFLAGS := -I$(dir $(SQLITE_AMALGAMA))
  SQLITE_OBJ    := sqlite3.o
  SQLITE_LIBS   :=
endif

.PHONY: all tablas paridad limpiar

all: piloto

piloto: $(FUENTES) piloto.h sutta_gen.c ipa_tabla.h trad_tabla.h $(SQLITE_OBJ) $(MINLIB)
	$(CC) $(CFLAGS) $(SQLITE_CFLAGS) -o $@ $(FUENTES) $(SQLITE_OBJ) \
		$(MINLIB) $(SQLITE_LIBS) -lm -lpthread -ldl

sqlite3.o: $(SQLITE_AMALGAMA)
	$(CC) -O2 -c -o $@ $< -DSQLITE_OMIT_LOAD_EXTENSION -DSQLITE_THREADSAFE=0

$(MINLIB): minificador/src/lib.rs minificador/Cargo.toml
	cd minificador && cargo build --release

# Los tres archivos generados se derivan de la plantilla, del modelo y de
# aksharamukha; se versionan para poder compilar sin Python, pero se rehacen
# desde aquí cuando cambie cualquiera de sus fuentes.
tablas:
	cd $(RAIZ) && uv run python c_modules/goldendict/generar_tablas.py \
		c_modules/goldendict
	cd $(RAIZ) && uv run python c_modules/goldendict/generar_sutta.py \
		c_modules/goldendict/sutta_gen.c

paridad: piloto
	cd $(RAIZ) && uv run python c_modules/goldendict/referencia.py \
		--salida /tmp/dpd_ref --n 300 --minificar
	cd $(RAIZ) && c_modules/goldendict/piloto volcadomin dpd.db /tmp/dpd_c \
		/tmp/dpd_ref/ids.txt
	cd $(RAIZ) && uv run python c_modules/goldendict/comparar.py \
		/tmp/dpd_ref /tmp/dpd_c

limpiar:
	rm -f piloto sqlite3.o
	cd minificador && cargo clean
