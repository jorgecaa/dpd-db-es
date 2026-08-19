# Project folder structure
Where are all the parts of the project located?

## Tree
.
├── archive
├── audio
├── c_modules
│   └── goldendict
├── conductor
├── db
│   ├── app
│   ├── backup_tsv
│   ├── bold_definitions
│   ├── epd
│   ├── families
│   ├── grammar
│   ├── inflections
│   ├── lookup
│   ├── sanskrit
│   ├── suttas
│   └── variants
├── db_tests
│   ├── gui
│   └── single
├── docs
├── exporter
│   ├── analysis
│   ├── anki
│   ├── apple_dictionary
│   ├── chrome_extension
│   ├── deconstructor
│   ├── goldendict
│   ├── grammar_dict
│   ├── kindle
│   ├── kobo
│   ├── mcp
│   ├── mobile
│   ├── pdf
│   ├── share
│   ├── sutta_central
│   ├── tbw
│   ├── tester
│   ├── tpr
│   ├── txt
│   ├── variants
│   ├── webapp
│   └── wxt_extension
├── go_modules
│   ├── deconstructor
│   ├── dpdDb
│   ├── frequency
│   ├── test
│   ├── tools
├── gui2
├── identity
│   ├── css
│   ├── fonts
│   └── logo
├── kamma
│   ├── archive
│   └── threads
├── misc
├── resources
├── scripts
│   ├── add
│   ├── bash
│   ├── build
│   ├── export
│   ├── extractor
│   ├── find
│   ├── fix
│   ├── info
│   ├── onboarding
│   ├── patch
│   ├── project_management
│   ├── repo_maintenance
│   ├── server
│   ├── suttas
│   ├── tutorial
├── shared_data
├── temp
├── tests
└── tools

## Code Structure

There are four main parts to the code:

1. __db__: Create the database and build up the tables of derived data.
2. __db_tests__: Run data integrity tests on the db.
3. __gui2__: Add new words, edit and update the db with a GUI.
4. __exporter__: Compile all the parts and export into various dictionary formats.

## Folder details

- **archive/** Retired code kept for reference only. Excluded from linting and type checking.

- **audio/** Text-to-speech audio generation and release.

- **c_modules/** When even Go is not close enough to the metal — a C pilot of the GoldenDict
  exporter (`goldendict/`, with a Rust minifier in `goldendict/minificador/`). Built with
  `make`, run from the project root. See its `README.md`.

- **conductor/** Product and tech-stack specs — `product.md` (vision, features, audience, release schedule) and `tech-stack.md` (full technology stack and key libraries).

- **db/** All code related to building and populating the various tables and columns of the database.

    - **models.py** SQLAlchemy model of the database.

    - **db_helpers.py** Helper functions to make create the database, get a session, get column names, etc.

    - **app/** Standalone script (not wired into any `just` recipe or the build pipeline) that copies `dpd_headwords` and `lookup` into a slim SQLite file for the external `dpd-app` project.

    - **backup_tsv/** TSV backups of the database source tables

    - **bold_definitions/** Extract bold definitions from CST texts and compile for easy searching.

	- **db/epd/** Compile data for the English to Pāḷi dictionary.

	- **families/** Compile HTML and JSON lists to populate the `family_compound`, `family_idiom`, `family_root`, `family_word` and `family_set` tables in the database, for easy access.

	- **grammar/** Grammar data and processing.

	- **inflections/** Populate the `inflection_templates` table and use that to create HTML inflection tables for every word.

	- **lookup/** Populate the `lookup` table for instant accces to every word in the dictionary.

	- **sanskrit/** Data used for updating the `sanskrit` column in the `dpd_headwords` table.

	- **suttas/** Sutta metadata and processing.

	- **variants/** Variant readings data and processing.

- **db_tests/** Tests to ensure the completeness and accuracy of the Pāḷi database.

	- **gui/** Interactive Flet-based counterpart for running tests and reviewing/approving bulk data corrections (antonyms, family compounds, hyphenations).

	- **single/** Individual, focused audit scripts — one script per linguistic or structural rule.

- **docs/** Helpful project documentation.

	- **project_folder_structure.md** What you're reading right now.

	- **dpd_headwords_table.md** Description of the real and virtual columns in the `dpd_headwords` table.

- **exporter/** Export the DPD database into various formats.

	- **analysis/** Analyse a Pāḷi passage word by word against the db (lookup + headwords), with optional AI translation, and export the results to CSV.

	- **anki/** Push DPD data into Anki decks — update notes directly in a live collection and export a standalone `.apkg`.

	- **apple_dictionary/** Export DPD for Apple Dictionary.

	- **chrome_extension/** Export DPD as a Chrome browser extension.

	- **deconstructor/** Export DPD Deconstructor to GoldenDict and MDict

	- **goldendict/** Export DPD, EPD, Help and Abbreviations to GoldenDict and MDict

	- **grammar_dict/** Export DPD Grammar to GoldenDict and MDict

	- **kindle/** Export a light version of DPD for Kindle.

	- **kobo/** Export a light version of DPD for Kobo eReader.

	- **mcp/** Export DPD as an MCP server.

	- **mobile/** Export a light version of DPD for mobile devices.

	- **pdf/** Export DPD to PDF format.

	- **share/** Output directory, not code — every exporter writes its finished dictionaries, zips and tarballs here (see `share_dir` in `tools/paths.py`).

	- **sutta_central/** Export DPD data for SuttaCentral integration.

	- **tbw/** Export a light version of DPD for integration into [The Buddha's Words website](https://thebuddhaswords.net/mn/mn1.html){target="_blank"}.

	- **tester/** Sandbox for rapid prototyping — builds a one-entry dummy dictionary so layout, component and CSS changes can be checked without a full rebuild. See its `README.md`.

	- **tpr/** Export DPD grammar deconstructor data for integration into Tipitaka Pali Reader app

	- **txt/** Export the whole dictionary as plain text — for screen readers, grep/sed analysis and long-term archival. See its `README.md`.

	- **variants/** Export variant readings data.

	- **webapp/** A web application using the DPD database and FastApi, hosted at [www.dpdict.net](https://www.dpdict.net){target="_blank"}

	- **wxt_extension/** Export DPD as a cross-browser extension using WXT framework.

- **go_modules/** When Python is too slow, write it in GO

	- **deconstructor/** Breaking up a more than a million compound words

	- **dpdDb/** Access the database using GORM

	- **frequency/** Generate frequency tables and data lists for every word

	- **test/** Scratch `main` package for exercising the Go helpers in `tools/`.

	- **tools/** GO packages used across the project

- **gui2/** A GUI for data capture and running database tests

- **identity/** Single source of truth for project styling and branding (css, fonts, logo). `just css` propagates the css to the webapp, exporters and docs — don't edit the copies.

- **kamma/** The project's work-thread system. `workflow.md` describes the task flow, `project.md` and `tech.md` hold the running project notes, `lessons.md` accumulates what went wrong, and one folder per thread lives in `threads/` (completed ones move to `archive/`).

- **misc/** Assorted reference material and one-off data files, not used by any build step.

- **resources/** All external resources used by the project, imported as submodules

- **scripts/** Useful scripts for project maintenance.

	see the [scripts readme](https://github.com/digitalpalidictionary/dpd-db/tree/main/scripts#readme){target="_blank"} for more info.

- **shared_data/** Data used across the project.

- **tests/** Automated tests for the project.

- **tools/** Python modules used across the project.
