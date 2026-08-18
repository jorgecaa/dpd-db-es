# Piloto en C del exportador de GoldenDict

Genera el HTML de cada entrada del diccionario — el mismo trabajo que hace
`exporter/goldendict/export_dpd.py` — en C, **byte a byte idéntico** a la salida
de Python, con la capa española aplicada y el mismo minificado.

Escribe además el **formato StarDict** completo (`.ifo`, `.idx`, `.dict`,
`.syn`) y cubre los cuatro tipos de entrada: las del diccionario, las **raíces**,
el **EPD** (inglés → pāḷi) y la **ayuda** (abreviaturas, bibliografía,
agradecimientos).

Es un **piloto**: sirve para medir qué techo tiene esta vía, no para sustituir
al exportador. Lo que **no** hace, y hay que tenerlo presente antes de usar su
salida como si fuera la real:

- **MDict** — otro formato, con su propio escritor (`tools/writemdict/`).
- **Entradas de variantes de escritura** (`export_variant_spelling.py`, 243
  líneas), el quinto tipo que sí incluye la exportación real.
- **`dictzip`** — el exportador comprime después el `.dict` y el `.syn` a
  `.dz` con idzip.
- **Los recursos** (css, js, fuentes) que `add_css`/`add_js`/`add_fonts` meten
  en el glosario. La comparación de paridad tampoco los cubre, porque el script
  de referencia tampoco los añade.

## Por qué existe

El exportador de Python cargaba todo el diccionario en memoria y repartía el
trabajo en un número fijo de lotes, de modo que la parte de cada obrero crecía
con el tamaño del diccionario. El pico medido llegó a **9,3 GB con 32 000
entradas**, lo que proyectaba unos **23,6 GB** para las 89 280 reales: más de lo
que cabe en RAM, y la causa de que el sistema matara procesos a mitad de una
exportación.

Ese problema ya está corregido en Python (paginación por `lemma_1` y lotes de
tamaño fijo). Este piloto responde a la pregunta siguiente: **cuánto de lo que
queda es coste de Python y cuánto es coste del trabajo**.

## Cifras medidas

Diccionario completo, 89 280 entradas, en un solo proceso:

| | Python | Piloto C |
|---|---|---|
| Renderizado, sin minificar | ~160 s en un núcleo | **2,95 s** |
| Con minificado (salida real) | — | **26,7 s** |
| Memoria | ~7,6 GB (proyectado) | **1,39 GB** |

El dato que más importa para decidir: de esos 26,7 s, unos **24 son la
biblioteca de minificado**, que es la misma en ambos lados. Es decir, el C
elimina casi todo el coste que era eliminable, y lo que queda es un suelo común.
La ganancia estructural real es la memoria.

Exportación completa a StarDict, con los cuatro tipos de entrada:

| | |
|---|---|
| Entradas | 170 556 (89 280 + 753 raíces + 79 242 EPD + 1281 de ayuda) |
| Tiempo total | 110 s |
| Memoria | 2,85 GB |
| Salida | 1,3 GB (`.dict` 862 MB, `.syn` 391 MB) |

La memoria sube de 1,4 a 2,85 GB respecto a sólo renderizar porque ahora también
retiene los sinónimos de cada entrada hasta poder ordenarlos.

## Compilar

```bash
make                                  # usa el sqlite3 del sistema
make SQLITE_AMALGAMA=/ruta/sqlite3.c  # o compila la amalgamación dentro
```

Necesita `gcc`, `cargo` y o bien el paquete `sqlite-devel` / `libsqlite3-dev`, o
bien el `sqlite3.c` de la [amalgamación oficial](https://sqlite.org/download.html).
La amalgamación **no** se versiona aquí: son 9 MB y no es código del proyecto.

El binario espera ejecutarse **desde la raíz del proyecto**, porque lee
`dpd.db`, `dpd_es.db`, `config.ini`, `identity/css/dpd-variables.css`,
`audio/db/dpd_audio_index.tsv` y `scripts/build/webapp_ui_es.tsv` por rutas
relativas.

```bash
c_modules/goldendict/piloto dpd.db 89280               # medir el renderizado
c_modules/goldendict/piloto exportar dpd.db SALIDA     # StarDict completo
c_modules/goldendict/piloto volcado dpd.db DIR IDS     # volcar sin minificar
c_modules/goldendict/piloto volcadomin dpd.db DIR IDS
c_modules/goldendict/piloto volcarotros dpd.db DIR     # raíces, EPD y ayuda
```

`exportar` escribe `SALIDA.{ifo,idx,dict,syn}`. Con un límite como cuarto
argumento sólo procesa esas entradas del diccionario y omite raíces, EPD y
ayuda, igual que hace `main.py` cuando hay `data_limit`. `PILOTO_FECHA` fija la
marca de tiempo del `.ifo`, que si no es la de ahora.

## Comprobar la paridad

`make paridad` vuelca la referencia de Python y la compara. A mano:

```bash
uv run python c_modules/goldendict/referencia.py --salida /tmp/ref --n 300 --minificar
c_modules/goldendict/piloto volcadomin dpd.db /tmp/c /tmp/ref/ids.txt
uv run python c_modules/goldendict/comparar.py /tmp/ref /tmp/c
```

La muestra por defecto es deliberadamente variada: cada parte del discurso
interesante, entradas con y sin ejemplos, con raíz, compuestas, y suttas de cada
uno de los 14 libros. Una muestra consecutiva por lema no ejercita las ramas de
la plantilla y da una falsa sensación de paridad.

Verificado sobre **980 entradas, 980 idénticas**, minificado incluido.

### Los otros tipos de entrada y el StarDict

| Qué | Verificación |
|---|---|
| Raíces | **753 / 753** idénticas |
| EPD | **200 / 200** idénticas (de 79 242) |
| Ayuda | **1277 / 1277** idénticas |
| StarDict `.ifo` `.idx` `.dict` `.syn` | **idénticos** sobre 400 y sobre 3000 entradas |

La comparación del StarDict incluye los **426 439 sinónimos** de esas 3000
entradas con su colación exacta, que es la parte delicada: pyglossary ordena por
`(bytes.lower(), bytes)` con el `lower()` de Python sobre bytes, que sólo pliega
A-Z, y SQLite compara esas claves como BLOB.

Las 1281 entradas de ayuda dan 1277 archivos porque cuatro nombres colisionan al
volcarlos (`*`, `CPD`, `pp`, `sandhi` aparecen dos veces); en el StarDict están
las 1281.

## Archivos

| | |
|---|---|
| `main.c` | orquesta: consulta, ensambla, mide |
| `cabecera.c` | `dpd_header.jinja` + `CSSManager` + el objeto de datos |
| `resumen.c` | el párrafo de resumen, y **la capa española** (`dpd_es.db`, cotejo por huella, raíces y sánscrito) |
| `cuerpo.c` | botones, flexiones y secciones de carga diferida |
| `gramatica.c` | la tabla de gramática y los ejemplos |
| `sutta.c` | las 18 propiedades calculadas de `SuttaInfo` y su cargador |
| `comun.c` | buffer que crece, conjunto de cadenas, lista, largo en UTF-8 |
| `sinonimos.c` | los sinónimos de cada entrada: flexiones, niggahitas, contracciones, códigos de sutta |
| `stardict.c` | el formato StarDict y su colación |
| `otros.c` | raíces y EPD, con las reducciones de CSS por estilo |
| `ayuda.c` | abreviaturas, ayuda, bibliografía y agradecimientos |
| `minificador/` | envoltorio en Rust de `minify-html`, **fijado a 0.18.1** |

### Generados — no editar a mano

`sutta_gen.c`, `ipa_tabla.h` y `trad_tabla.h` los produce `make tablas`. Se
versionan para poder compilar sin Python, pero hay que **regenerarlos cuando
cambie su fuente**: la plantilla, `tools/webapp_ui_es.tsv`, el modelo o
`tools/lemma_traditional.py`.

- `sutta_gen.c` — `generar_sutta.py` compila a C las 900 líneas del bloque
  `sutta_info`, aplicándoles antes la misma traducción al español que usa el
  exportador. Ante cualquier construcción de Jinja fuera de su subconjunto
  **falla**, en vez de adivinar.
- `ipa_tabla.h` — `lemma_ipa` sale de aksharamukha, que no es portable a C. Un
  mapeo por carácter acierta sólo el 6,7 %; uno con dígrafos y vocal + `ṃ`
  acierta el **100 % sobre 20 000 lemas**, y el generador lo verifica cada vez.

## Lo que enseñó portar esto

La paridad byte a byte destapó cosas que leer el código no muestra, y todas
habrían pasado por buenas sin comparar:

- `len(lemma_clean) < 30` cuenta **caracteres**; con diacríticos pali `strlen`
  cuenta bytes y cruza el umbral antes, dejando lemas largos sin su botón de
  familias de compuesto.
- Con `trim_blocks` y `lstrip_blocks`, un bloque condicional falso no deja
  sangría **pero sí la línea en blanco que lo sigue**; la plantilla tiene además
  líneas de sólo espacios y un `{% endif %} ` con espacio final que no se
  recorta.
- El filtro `tojson` escribe `[0, 0, 0]` mientras la columna guarda `[0,0,0]`.
- `root_family_key` no es columna sino propiedad híbrida, y `family_set` se
  parte por `"; "`, no por `";"`.
- La fila «TBW Legacy» depende de `show_tbw` en `config.ini`.
- Los TSV de referencia llevan celdas entrecomilladas **con saltos de línea
  dentro**: un lector ingenuo parte una fila en varias y saca 36 entradas de
  ayuda de más.
- El `.ifo` es de una línea por clave, así que pyglossary convierte los saltos
  del campo `description` en `<br>`.
