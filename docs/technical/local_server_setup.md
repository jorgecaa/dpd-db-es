# Local Server Setup

This guide provides step-by-step instructions on how to set up the Digital Pāḷi Dictionary (DPD) database for use as a local server. This is useful for running the web application locally or providing an API for other dictionary software like GoldenDict or DictTango.

## Prerequisites

- **uv**: This project uses [astral uv](https://github.com/astral-sh/uv) for dependency management and Python version control. Install it if you haven't already:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- **Python**: `uv` will automatically manage and install the required Python version for you when you run the setup commands below. There is no need to install Python manually.

## Step-by-Step Instructions

### 1. Download the Repository

Clone the DPD database repository from GitHub (using `--depth 1` for a faster download):

```bash
git clone --depth 1 https://github.com/digitalpalidictionary/dpd-db.git
cd dpd-db
```

### 2. Setup Dependencies

Use `uv` to sync the environment and install all necessary dependencies:

```bash
uv sync
```

### 3. Download the Databases

You need to download three main database files: the primary DPD database, the audio database, and the translations database.

#### 3.1. Primary DPD Database
Download and extract the latest primary database:

```bash
wget -qO- https://github.com/digitalpalidictionary/dpd-db/releases/latest/download/dpd.db.tar.xz | tar -xJ
```

#### 3.2. Audio Database
Download the audio database using the provided script:

```bash
uv run python audio/db_release_download.py
```

#### 3.3. Translations Database
Download the Tipiṭaka translations database:

```bash
uv run python resources/tipitaka_translation_db/download_and_unzip_db.py
```

### 4. Run the Server

Start the local server using `uvicorn`. By default, this will start the server on port 8080.

```bash
uv run uvicorn exporter.webapp.main:app --host 0.0.0.0 --port 8080
```

### 5. Access the Web Application

Once the server is running, you can open your web browser and navigate to:

[http://localhost:8080](http://localhost:8080)

You should now see the DPD web application running locally!

## Serving memory budget

The webapp serves an 89,280-headword dictionary from disk, backed by the kernel page
cache. It is expected to stay inside these numbers, measured 2026-09-18 on one
uvicorn worker with `dpd.db` v0.4.20260728:

| Metric | Budget | Measured |
|---|---|---|
| Anonymous RSS per worker, idle | ≤200 MiB | 124 MiB |
| Anonymous RSS per worker, steady state | ≤260 MiB | 239 MiB (1,000-request soak, flat after the first 400) |
| Start → first served request | ≤10 s | 4 s |
| `/search_json` median | ≤50 ms | 10–15 ms |

The served db is not copied into RAM: `dpd.db` stays a real file and the kernel keeps
the hot pages in reclaimable page cache. File-backed memory is deliberately *not*
counted against the budget — only anonymous memory is, because that is the part the
kernel cannot take back under pressure.

Check the numbers on a running instance at `/status` (dashboard) and `/metrics`
(Prometheus: `dpd_webapp_anon_mib`, `dpd_webapp_cgroup_peak_mib`,
`dpd_webapp_memory_budget_mib`, `dpd_webapp_cache_entries{cache="…"}`). The dashboard
colours are budget-relative: green inside the budget, orange up to 1.5×, red beyond.
When a cgroup limit exists (`memory.max`), that limit *is* the budget; otherwise the
documented steady-state budget above applies.

Every in-process cache must be bounded and listed in the inventory in
`kamma/tech.md` before it lands — an unbounded cache is what turned this into a
memory investigation in the first place.

## Notes

- **Host**: Using `--host 0.0.0.0` allows the server to be accessible from other devices on your local network. Use `--host 127.0.0.1` if you want it only accessible from your own machine.
- **Port**: You can change the port by modifying the `--port` value.
- **Background Execution**: To run the server in the background, you can use `nohup` or `screen`/`tmux`.
