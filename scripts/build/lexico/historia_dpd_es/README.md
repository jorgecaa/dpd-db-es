# dpd-es

Capa en español para el [Digital Pāḷi Dictionary](https://github.com/digitalpalidictionary/dpd-db).

Este repositorio **no contiene ni el diccionario ni la traducción**: los dos van
enlazados como submódulos a sus repositorios de origen, de modo que cuando ellos
se actualicen, aquí se note. Lo nativo son los scripts que forman el parche.

```
dpd-es/
├── src/                    ← nativo: archivos nuevos que se copian a dpd-db
│   ├── tools/spanish.py
│   ├── scripts/build/build_spanish_sidecar.py
│   ├── scripts/build/sanskrit_meanings_es.tsv
│   ├── scripts/build/webapp_ui_es.tsv
│   ├── scripts/build/build_spanish_glossary.py
│   └── scripts/build/compounds_es.tsv
├── patches/                ← nativo: los seis cambios sobre archivos de upstream
├── config/spanish.ini      ← nativo: el interruptor
├── apply.sh                ← nativo: instala todo lo anterior en vendor/dpd-db
└── vendor/
    ├── dpd-db/             ← submódulo (shallow, 2,2 GB en origen)
    └── dpd-dictionary-es/  ← submódulo: el JS con las 72.698 definiciones
```

## Puesta en marcha

```bash
git clone --recurse-submodules git@github.com:jorgecaa/dpd-es.git
cd dpd-es
./apply.sh --sidecar
```

`apply.sh` es idempotente: reconoce los parches ya aplicados y no los repite.
Con `--revert` los deshace. Si upstream ha tocado las líneas parcheadas, se
detiene y lo dice en vez de dejar el árbol a medias.

Hace falta un `dpd.db` en `vendor/dpd-db/` (descargado de las releases o
construido con `just initial_build_db`).

## Cómo funciona

Las tres columnas de significado de `dpd_headwords`, más `root_meaning` y
`sanskrit_root_meaning` de `dpd_roots`, conservan su nombre en la base de datos,
pero el atributo pasa a ser privado (`_meaning_1`) y el nombre público es un
`hybrid_property` con tres caras:

| Cara | Va a |
|---|---|
| Lectura de instancia (`hw.meaning_1`) | Español, si hay traducción válida |
| Escritura (`hw.meaning_1 = x`) | Siempre la columna inglesa |
| Expresión de clase (`filter(DpdHeadword.meaning_1 == ...)`) | Siempre la columna inglesa |

Así el español llega a todo lo que muestra texto —incluidas las plantillas del
webapp, del PDF y de TPR, que leen `meaning_1` directamente— sin que cambie ni
una consulta SQL ni una escritura.

**Las traducciones viven fuera de `dpd.db`**, en un `dpd_es.db` aparte. No es un
capricho: `dpd.db` lo reemplaza entero el actualizador semanal desde la última
release, y cualquier columna añadida se perdería en la siguiente pasada.

## Salvaguardas

El overlay se abstiene en cuatro situaciones, todas medidas contra la db de mayo
de 2026 (89.050 headwords):

1. **Campo inglés vacío.** No se inventa una acepción donde el diccionario no la
   tiene; si no, 10 palabras cambiaban de estado de completitud.
2. **La acepción ha derivado.** `lemma_1` es una clave estable pero su contenido
   puede reasignarse: `yoga 7` era *(gram) syntactically dependent on* y la
   traducción decía *aptitud; idoneidad*, de otro sentido. El sidecar guarda el
   `pos` y la construcción del momento de traducir y descarta lo que ya no casa.
3. **Lógica que inspecta el texto.** El filtro `(gram)` de `tbw_exporter.py` va
   dentro de `spanish.suppressed()`, igual que el backup a TSV: esos ficheros son
   la fuente versionada del diccionario y nunca deben recibir texto traducido.
4. **Rutas de escritura.** `update_root_in_db()` de `gui2` copia las columnas de
   la raíz por nombre y las vuelve a escribir; sin suprimir el overlay, editar
   una raíz en la GUI habría commiteado el español dentro de la columna inglesa.

Con todas puestas, encender el overlay no mueve ningún recuento:

| Comprobación | Inglés | Con overlay |
|---|---|---|
| `"(gram)" in meaning_1` | 788 | 788 |
| `meaning_1` vacío | 26.782 | 26.782 |
| `degree_of_completion` | 89.050 | 89.050 |

## Cobertura

**Palabras.** De las 72.698 entradas traducidas, 72.227 corresponden a un lema
actual y **64.026 acaban mostrándose en español**. El resto se descarta: 150 por
`pos` distinto, 5.881 por construcción distinta y 2.170 sin cambio efectivo.

**Raíces.** De las 753 de `dpd_roots`, **751 se traducen**. Una se descarta por
huella (`√vell`, traducida sobre *shake, sway* cuando hoy dice *shake, sway,
curl*) y otra no tiene entrada. Aquí la huella es exacta: `dpd_roots.js` guarda
el inglés del que se tradujo, así que se compara el texto literal en vez de
señales indirectas.

**Glosas sánscritas.** De las 751 raíces con `sanskrit_root_meaning`, **742 se
traducen**; las 9 restantes son dato corrupto de origen (`-` y `?`). Esta columna
**no estaba traducida en el material español**, así que se compone de dos
fuentes: 125 frases reutilizadas de las raíces pali cuando el texto inglés
coincide literalmente (*be worth* → *valer*), y 330 traducidas para este
proyecto en `src/scripts/build/sanskrit_meanings_es.tsv`. Ese TSV es el único
contenido lingüístico propio del repositorio; todo lo demás viene de los
submódulos. La clave es la frase inglesa, de modo que una glosa reformulada
upstream deja de casar y se queda en inglés.

El filtro por construcción es deliberadamente conservador y descarta de más: hay
casos de deriva real (`akatayogga` pasó de `na + kata + yogga` a
`na + kata + yoggā + a`) junto a otros en los que el sidecar simplemente no
tenía construcción y hoy sí la hay, cuya traducción probablemente seguía siendo
buena.

La traducción cubre solo el vocabulario del canon temprano, que es lo que exporta
`tbw_exporter.py`; los 16.823 headwords restantes de `dpd.db` nunca estuvieron en
el material de origen.

## Ficha bilingüe

La tabla *Grammar* de la ficha lleva una fila **DPD inglés** entre *Lemma* e
*IPA* con la definición original, de modo que el texto de partida queda siempre
a la vista junto al traducido. La alimenta `meaning_combo_english`, que es
`meaning_combo` evaluado dentro de `spanish.suppressed()`.

## Interfaz

Las cadenas de la interfaz **no se traducen editando las plantillas**, que son lo
que upstream más mueve (`dpd_headword.html`: 55 commits en doce meses). En su
lugar se envuelve el cargador de Jinja: al cargar una plantilla se sustituyen las
frases conocidas por su español, y solo en nodos de texto y atributos visibles —
nunca en marcado, expresiones Jinja ni dentro de `<script>` o `<style>` (la
página de estado incorpora una hoja de estilos y un script que si no se
romperían). El parche son dos líneas en `exporter/webapp/main.py`.

Una frase que upstream reformule deja de casar y se queda en inglés, que es el
modo de fallo correcto: nunca produce texto corrupto, solo texto sin traducir.

Un nodo de texto puede mezclar prosa con etiquetas Jinja (`{% endif %} Did you
spot a mistake…`); en ese caso se parte por las etiquetas y se traducen los
tramos de prosa, en vez de descartar el nodo entero.

Estado: **175 de 318 frases distintas traducidas** — toda la prosa (avisos,
explicaciones, pies de página), los botones de la ficha, los encabezados de la
tabla de gramática, la cabecera, los ajustes y el historial. La única cadena
larga que queda sin traducir es una URL, que no debe traducirse.

Quedan 143 cadenas cortas: nombres propios que se dejan tal cual (Sutta Central,
VRI, Pali Text Society), términos pali (nikāya, vagga, piṭaka), siglas, y
fragmentos partidos por etiquetas (`"which contain"`, `"in them."`) que hay que
traducir mirando la frase completa para que el orden español funcione.

## Glosario de acepciones

La traducción existente conservó la estructura de las definiciones del DPD
—acepciones separadas por `;`—, así que inglés y español se pueden **alinear
acepción por acepción**:

    meaning_1 (en)  "not hard; not rough"
    meaning_1 (es)  "no duro; no áspero"

`build_spanish_glossary.py` explota esa alineación, y la clave incluye **la
categoría gramatical del lema**, no solo el texto inglés: `eating` es *comiendo*
bajo `prp`, *que come* bajo `adj` y *comer* bajo `nt`. Sin ese dato, la gramática
se lee como incoherencia — de hecho explica **el 32 %** de la discrepancia
aparente.

La consulta es de dos niveles: primero por acepción y categoría (106.766 claves,
precisa) y, si falla, por acepción sola cuando su español no varía nunca entre
categorías (93.173 claves). Sin ese segundo nivel la precisión sube pero la
cobertura se hunde: los compuestos rara vez comparten categoría con la entrada
de la que procede la acepción.

Se descartan además los campos donde la traducción **reordenó** las acepciones,
porque el emparejamiento por posición produce ahí pares falsos (`great disciple
of the Buddha` → `Soṇa Kuṭikaṇṇa`). Son 25 de 91.366 campos; el método solo ve
los reordenamientos anclados en un nombre propio repetido literalmente, así que
los no anclados siguen sin detectarse.

El glosario es un artefacto derivado y no se versiona: se regenera del sidecar
más la db. Lo que sí se versiona es lo que produce.

## Compuestos traducidos por reuso

3.667 lemas de familias de compuestos no están en el material español. De ellos,
**342 se traducen sin criterio humano**: todas sus acepciones ya figuran en el
glosario con una única traducción acordada, de modo que la coherencia
terminológica es automática — la misma acepción inglesa recibe siempre el mismo
español.

    abbhantaragata      gone inside; being within   →  ido adentro; estando dentro
    acchinnamūlaka      not cut off at the root     →  no cortado de raíz

Están en `compounds_es.tsv`, se funden en la tabla `meanings_es` al construir el
sidecar y **nunca pisan una traducción humana**. Con ellos, `family_compound`
sube del 83,1 % al **85,1 %**.

Aflojar los criterios daría más, a costa de introducir criterio: admitir la
traducción mayoritaria en las acepciones ambiguas, o no exigir que el campo
literal esté cubierto, subiría a unas 450-640. Las ~2.900 restantes necesitan
traducción nueva.

### Homogeneidad de términos

Las acepciones con reparto se resuelven **por el propio corpus**: gana la
variante que la traducción ya usa mayoritariamente, y en caso de empate dentro
de la acepción, la que sea más frecuente en el conjunto. Así el término queda
homogéneo sin que nadie imponga una preferencia externa. De 5.556 repartos,
**4.418 quedan resueltos**; 1.138 son empates que el corpus tampoco desempata y
se quedan fuera del glosario.

Lo que decide el corpus, medido sobre el texto español completo:

| | corpus | política declarada |
|---|---|---|
| Buddha / Buda | 442 / 74 | coincide |
| sufrimiento / dolor | 190 / 135 | coincide |
| formaciones / construcciones | 24 / 1 | coincide |
| jhāna sin traducir | 21 / 3 | coincide |
| camino / sendero / vía | 381 / 145 / 67 | — |
| comprender / entender | 73 / 29 | — |
| remover / eliminar | 29 / 24 | — |

**Una discrepancia con la política declarada:** el proyecto español dice traducir
`viññāṇa` como *consciencia* «no conciencia», pero entre las entradas cuyo inglés
dice *consciousness* el corpus usa *conciencia* 92 veces y *consciencia* 11. No
se toca: reescribirlo sería modificar traducción humana, y queda anotado aquí
para que se decida aparte.

Nada de esto reescribe una entrada existente. El glosario solo alimenta
traducciones **nuevas**; lo ya traducido se sirve tal cual.

## Procedencia

Cada ficha declara de dónde viene su español, en una fila **Fuente** de la tabla
de gramática, justo encima de *IPA*:

    Lema         nandanavana
    DPD inglés   name of a wood in Tāvatiṃsa heaven; lit. delighting wood
    Fuente       dpd-es
    IPA          /n̪ən̪d̪ən̪əʋən̪ə/

Los tres valores son `dpd-dictionary-es` (la traducción publicada), `dpd-es`
(este proyecto) y `DPD` (sin traducir: se muestra el original inglés). La fila
dice de dónde sale el texto y nada más; informa también cuando **no** hay
traducción, que es justo lo que un símbolo no puede decir.

La procedencia **no se marca dentro del texto** —ni con símbolos—, sino que vive
en una columna `origin` que distingue `material` de `generado`. Además de
alimentar la fila *Fuente*, esa columna **mantiene lo generado fuera del
glosario**, para que este proyecto no cite su propia redacción como precedente
al generar la tanda siguiente.

En las listas de familia, que enumeran decenas de otras palabras, no se indica
procedencia: para saberla se abre la ficha de la palabra.

## Tablas de familias

Las cinco tablas de familias (raíz, palabra, compuesto, conjunto, modismo) se
guardan en `dpd.db` como **HTML renderizado en tiempo de compilación**, así que
sus definiciones nunca pasan por el ORM. En vez de regenerarlas —lo que
escribiría en `dpd.db` y se perdería en la siguiente actualización semanal—, cada
modelo expone `html_es`, que reescribe la celda de significado de cada fila con
la definición del headword vivo. Eso mantiene la huella: una fila cuyo lema no
casa conserva su inglés.

Medido sobre 300 familias y 6.455 filas: **72 % traducidas**. Las demás son lemas
que no están en el material español.

Dos detalles que costaron una pasada cada uno:

- Los lemas del HTML llevan superíndices (`dukkha␠¹`, con espacio fino), así que
  hay que deshacer `superscripter_uni()` antes de buscarlos.
- Sin memoizar, una palabra con muchas familias de compuestos triplicaba la
  latencia de página (1,89 s frente a 0,66 s). Con la caché de lema→definición
  vuelve a 0,69 s en la segunda visita; la primera sigue pagando las consultas.

**El encabezado sigue en inglés** (`42 words belong to the root family √dukkh
(be painful)`): es prosa generada, no dato.

## Limitación conocida

**Editar con el overlay encendido confunde**: si asignas un valor a un campo y lo
vuelves a leer, obtienes el español, no lo que acabas de escribir. El uso
previsto es apagado para editar en `gui2`, encendido para webapp y exportadores.

## Origen del material

- Diccionario y código: [digitalpalidictionary/dpd-db](https://github.com/digitalpalidictionary/dpd-db), de Ven. Bodhirasa. CC BY-NC-SA 4.0.
- Traducción al español: [grrek/dpd-dictionary-es](https://github.com/grrek/dpd-dictionary-es), coordinada por Paññābhūmi (dhamma-pb.org). Misma licencia.

El JS enlazado corresponde al build del 8 de noviembre de 2025 de BW2 (idéntico
byte a byte), no al último. Entre esa fecha y la db actual hay ocho meses de
edición del diccionario, que es justamente lo que detecta la huella.

Parche por Jorge Contreras. CC BY-NC-SA 4.0, como el material del que deriva.
