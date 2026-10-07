# EmbeddedAgent — general-purpose STM32F4 agent

A two-mode agent over your board and its documentation:

- **Mode 1 — chat**: answers anything about the STM32F4 (MCU, peripherals,
  registers, HAL) grounded in the indexed documents.
- **Mode 2 — firmware**: codes anything you ask for as a `main.c` — LED
  blinking, UART, SPI, ADC, DMA... — validates it, then builds and flashes
  it to the STM32F4-Discovery board (`disco_f407vg`, MCU STM32F407VGT6).

The agent is not hard-wired to any specific task: task knowledge (pin maps,
peripheral wiring, HAL gotchas) lives in the documents and is retrieved per
request, not baked into prompts.

## Architecture

Three cleanly separated layers — the HTTP layer knows nothing about firmware
generation, and the agent core knows nothing about HTTP:

```
agent/                    agentic core (no FastAPI imports)
├── orchestrator.py       MODE 2 pipeline: retrieve → generate → validate → build → flash
├── chat.py               MODE 1 engine: documentation-grounded Q&A (RAG chat)
├── llm.py                Ollama/OpenAI transport (client factory, chat, model listing)
├── prompts.py            every prompt template (general: structure only, no task mandates)
├── extraction.py         raw LLM output → clean C source (sentinels, alias repair)
├── validation.py         general correctness: structure, F4 HAL identifier validity,
│                         and "did the code actually implement what was asked?"
├── generator.py          generate → validate → feedback retry loop
├── ports.py              ST-LINK serial port detection/resolution
├── builder.py            PlatformIO: project files, compile, flash, archive
├── analysis.py           generated C → hardware-state JSON (frontend visualization)
└── errors.py             exception hierarchy → mapped to HTTP codes by app/

rag/                      retrieval layer
├── seed_docs.py          canonical board facts (always indexed, always retrieved first)
├── loaders.py            .txt / .md / .pdf loaders (pypdf optional)
├── chunking.py           paragraph-aware chunking with size cap
├── vector_store.py       ChromaDB wrapper (add/query/clear + fingerprints)
├── retriever.py          query → clean context (garbled-chunk filter, seed-first merge)
├── ingest.py             CLI: rebuild index from documentation/ + scraped pages
└── query.py              CLI: debug what a given prompt retrieves

scraping/                 one-time, on-demand web scraping (never auto-runs)
├── http_client.py        polite fetcher: robots.txt, 1 req/s/domain, raw HTML cache
├── extractors.py         generic HTML → text (junk-strip + content-density pick)
├── sources.py            source model - add a site by adding one small module
├── scrapers/             per-site definitions (controllerstech, microcontrollerslab, interrupt)
└── run.py                CLI: python -m scraping.run

app/                      FastAPI backend
├── main.py               app factory, logging, lifespan singletons, error mapping
├── schemas.py           request/response models (API contract)
├── jobs.py              persistent job store (data/jobs/*.json, survives restarts)
├── deps.py               dependency providers (shared singletons, not per-request clients)
└── routers/              health.py, generate.py, build.py, flash.py, jobs.py, chat.py

config.py                 single source of truth for all env-driven settings
.env.example              template for the required .env (copy and adjust)
main_api.py               launcher → uvicorn app.main:app
stm32_agent.py            CLI entry (chat mode: `chat`; generate+flash: "<command>")
stm32_project/            PlatformIO project the generated firmware is staged into
└── scripts/connect_under_reset.py  OpenOCD connect-under-reset upload hook
scripts/selfcheck.py            offline test suite (no Ollama/board needed)
scripts/e2e_flashing_test.py    end-to-end generate→verify→flash use-case suite
```

## Quick start

```bash
# 0. Prereqs: Python 3.10+, PlatformIO CLI (`pip install platformio`),
#    and Ollama running (https://ollama.com) with the model pulled:
#        ollama pull gemma4:cloud        # or: ollama pull llama3.1
#    (":cloud" models also need one `ollama signin`)

# 1. Install and configure
pip install -r requirements.txt
copy .env.example .env                   # set OLLAMA_API_KEY, OLLAMA_MODEL, BASE_URL
#    (RAG_BACKEND/CHROMA_* only for the cloud vector store)

# 2. Backend (serves both modes)
python main_api.py                     # API on 0.0.0.0:8000

# 3. Mode 1 - documentation chat (CLI)
python stm32_agent.py chat

# 4. Mode 2 - CLI (generate; --check compiles without flashing)
python stm32_agent.py "Turn on the red diode continuously" --check
python stm32_agent.py "blink via DMA" --yes --port COM3     # flash, explicit port
```

## RAG workflow

```bash
python -m rag.ingest           # (re)build the index: documentation/ + scraped pages + core facts
python -m rag.ingest --summary # what is currently indexed
python -m rag.query "blink the blue LED using DMA"   # inspect retrieved context
```

- Core board facts (`rag/seed_docs.py`) are auto-seeded into an empty store and
  **all of them are always included** in the retrieved context (fetched by exact
  source filter - a similarity query cannot reliably return them all), so a
  large indexed manual can never outrank the board-specific constraints
  generation depends on.
- Drop PDFs/txt/md files into `documentation/` and re-run `python -m rag.ingest`
  to make them retrievable.

## Scraping (one-time, on-demand)

The knowledge base is extendable by scraping STM32/embedded tutorials. It
**never runs automatically** - not at server startup, not on import - only via
the explicit command:

```bash
python -m scraping.run            # scrape all sources, then rebuild the index
python -m scraping.run --list    # show the registered sources
python -m scraping.run --sources controllerstech,interrupt
python -m scraping.run --force   # re-download pages even if cached
python -m scraping.run --no-ingest
```

Registered sources: **ControllersTech** (STM32 HAL/register tutorials),
**MicroControllersLab** (broad STM32 peripheral examples), **Memfault
Interrupt** (embedded-systems practice), and **STMicroelectronics official**
(registered but currently blocked - see the module docstring). Re-running the
command is cheap: fetched pages are cached under `scraping/raw/`, extracted
text lives in `scraping/output/` and is indexed by `python -m rag.ingest`
like any other document. Add a new site = one small module in
`scraping/scrapers/`.

## Vector store backend (local vs cloud)

`RAG_BACKEND` in `.env` selects the backend - `rag/vector_store.py` is the
only place that knows the difference:

    RAG_BACKEND=cloud   # Chroma Cloud (needs CHROMA_API_KEY/TENANT/DATABASE)
    RAG_BACKEND=local    # persistent ./chroma_db, regenerated by rag.ingest

Current cloud caveat: the free Chroma Cloud tier caps the database at ~300
records, while the full corpus (PDFs + scraped tutorials + core facts) is
~7,000 chunks - a full `python -m rag.ingest` against the cloud fails with a
quota error. Until the quota is raised (request link is in the error
message), the cloud store runs with just the core seed facts; flipping
`RAG_BACKEND=local` and re-running `python -m rag.ingest` restores the full
index locally in a couple of minutes.

## API

Two modes, two endpoint groups:

| Mode | Method | Path                 | Purpose                                  |
|------|--------|----------------------|------------------------------------------|
| 1    | POST   | `/api/chat`          | Ask the documentation: `{message, history?}` → `{answer, sources}`. Stateless — the client passes conversation history back in (oldest first). |
| 2    | POST   | `/api/generate`      | Generate firmware for any STM32F4 request: `{prompt, verify?}` → `{id, code, hardware_state, build}`. With `verify` (default true) the code is compile-checked with PlatformIO and, if the compiler rejects it, the errors are fed back to the model for one corrective round. **No flashing ever happens here.** |
| 2    | POST   | `/api/verify`        | Compile-check a stored job: `{id}` → `{status, logs}` (no hardware touched). |
| 2    | POST   | `/api/flash`         | Compile + flash a generated job: `{id, confirmed, port?}` → `{status, logs}`. The port is optional — omit it and PlatformIO auto-detects. |
| 2    | GET    | `/api/ports`         | List the serial ports currently visible, so the client can offer a port picker. |
| 2    | POST   | `/generate-and-flash`| Legacy one-shot full pipeline             |
| —    | GET    | `/api/jobs`          | List jobs (survive server restarts)       |
| —    | GET    | `/api/jobs/{id}`     | Job detail incl. code, hardware state, logs |
| —    | GET    | `/health`            | Liveness probe; `?verbose=1` adds component status |

Job statuses: `generated` → `verified` / `compile_failed` (compile check) →
`flashing` → `flashed` / `failed`.

Upload ports are never guessed: an explicit `port` on the flash request or
CLI `--port` wins, then `PLATFORMIO_UPLOAD_PORT` (env/.env), and with neither
set, `platformio.ini` is written without an `upload_port` line so PlatformIO
auto-detects the board.

Error mapping: setup problems → `503` (ConfigError), LLM unreachable/invalid output →
`502` (LLMUnavailableError / GenerationError), build failures → `500` with full
PlatformIO logs (BuildError), flash build-failures keep the soft-fail `200 +
{status: "error"}` contract.

## Checks

```bash
python scripts/selfcheck.py          # offline suite: extraction, validation, chunking,
                                     # retrieval, job persistence, parser (no Ollama/board needed)

# End-to-end against the running API + attached board (generates, compile-
# verifies and flashes one firmware per use case; results land in
# scripts/e2e_flashing_results.log):
python scripts/e2e_flashing_test.py                    # all use cases
python scripts/e2e_flashing_test.py gpio_blink pwm_fade  # selected only
```
