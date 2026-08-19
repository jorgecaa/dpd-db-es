# CLAUDE.md

Capa en español para el Digital Pāḷi Dictionary. El **README.md explica el diseño**
(overlay por `hybrid_property`, salvaguardas, huella anti-deriva, glosario,
procedencia, cobertura medida). Léelo antes de tocar nada; aquí solo va lo
operativo que no está allí.

## Regla de oro: dónde se edita

Lo versionado es `src/`, `patches/`, `config/` y `apply.sh`. **`vendor/dpd-db/`
es un checkout ajeno donde `apply.sh` escribe**: editar ahí y parar es perder el
trabajo en la siguiente actualización del submódulo.

- Archivo nativo (`tools/spanish.py`, los `scripts/build/*`): se edita en `src/`
  y se instala con `./apply.sh`.
- Cambio sobre un archivo de upstream: se edita en `vendor/dpd-db/` para probar
  y **se regenera el parche** —`git -C vendor/dpd-db diff -- <ruta> > patches/000N-….patch`—
  antes de commitear. Los ocho parches tocan `db/models.py`,
  `db/backup_tsv/backup_dpd_headwords_and_roots.py`, `exporter/tbw/tbw_exporter.py`,
  `gui2/database_manager.py`, `exporter/webapp/main.py` y
  `exporter/webapp/templates/dpd_headword.html`.
- Nada de commitear dentro de `vendor/`: son submódulos de terceros.

## Comandos

```bash
./apply.sh                 # copia archivos nativos + aplica parches (idempotente)
./apply.sh --sidecar       # además reconstruye dpd_es.db desde el JS traducido
./apply.sh --revert        # deshace los parches

cd vendor/dpd-db
uv run python scripts/build/build_spanish_sidecar.py --source ../dpd-dictionary-es/js/dpd_ebts_es.js
uv run python scripts/build/build_spanish_glossary.py --glossary dpd_es_glossary.tsv
uv run python scripts/build/normalize_senses.py --dry-run    # ortografía de lo propio
just webapp                # uvicorn en :8080, para ver las fichas
```

Hace falta un `dpd.db` en `vendor/dpd-db/` (release o `just initial_build_db`);
**ahora mismo no está**, así que el glosario y el webapp no arrancan hasta ponerlo.
Todo se ejecuta desde `vendor/dpd-db` con `uv run`, porque los scripts importan
`tools.*` y `db.*` de upstream. Ruff con la config de upstream; este repo no tiene
suite propia de pruebas.

`apply.sh` **no instala `normalize_senses.py`**: es una utilidad del repositorio,
se corre copiándola a mano si hace falta.

## Artefactos generados (no se versionan)

`dpd_es.db` y `dpd_es_glossary.tsv` se regeneran siempre desde el JS del submódulo
más `dpd.db`. Lo que sí se versiona es lo que producen: los TSV de `src/scripts/build/`.

| Archivo | Qué es |
|---|---|
| `senses_es.tsv` | acepciones traducidas para este proyecto (no están en el material) |
| `compounds_es.tsv` | lemas generados por reuso del glosario, sin criterio humano |
| `glosario_es_overrides.tsv` | correcciones a mano de claves del glosario, con su porqué |
| `material_es_correcciones.tsv` | correcciones al material publicado, con huella del texto sustituido |
| `sanskrit_meanings_es.tsv` | glosas sánscritas, clave = frase inglesa |
| `webapp_ui_es.tsv` | cadenas de interfaz, clave = texto exacto de la plantilla |

Toda corrección al material ajeno pasa por `material_es_correcciones.tsv` **con la
huella del español que reemplaza**: si upstream cambia ese texto, la corrección se
descarta sola en vez de pisarlo en silencio. Nunca editar el submódulo de traducción.

## Invariantes que no se rompen

- El overlay es **de solo lectura**: escrituras y consultas SQL van siempre a la
  columna inglesa. Cualquier ruta que escriba en la db va envuelta en
  `spanish.suppressed()` (backup TSV, `tbw_exporter`, `gui2`).
- Fallar deja el inglés, nunca texto corrupto: si la huella (`pos` + construcción,
  o el inglés literal en raíces) no casa, no se traduce.
- Lo generado (`origin = generado`) **no entra en el glosario**: el proyecto no
  cita su propia redacción como precedente.
- Los recuentos de `README.md` son medidos. Si un cambio los mueve, se vuelven a
  medir y se actualiza el README en el mismo commit.

## Idioma

- Código, docstrings y comentarios de `src/`: **inglés**, como upstream.
- Cabeceras de los TSV, `analisis/`, README y mensajes de commit: **español**.
- Commits: `tema: frase en minúscula` («interfaz: …», «material: …»), diciendo el
  criterio, no el archivo tocado.

## analisis/

Entorno aparte (`analisis/preparar.sh`, idempotente) para revisar el español:
hunspell dice si una palabra existe, el léxico de Apertium qué categorías admite,
spaCy etiqueta en contexto. Sus descargas y el `.venv` están fuera del control de
versiones. Las tres piezas no son intercambiables — el porqué está en la cabecera
de `preparar.sh`.
