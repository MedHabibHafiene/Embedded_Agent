# EmbeddedAgent — Project Summary

**One-liner:** A full-stack AI agent that turns plain-English requests into compiled, verified, and flashed firmware on a real STM32F4-Discovery board — no manual coding required.

## What it does

Type *"blink the blue LED using DMA"* and the agent:

1. **Retrieves** the relevant board facts, HAL references, and tutorials from an indexed documentation corpus (RAG over ChromaDB, sentence-transformers embeddings).
2. **Generates** a complete `main.c` using an LLM via Ollama's OpenAI-compatible API (model-agnostic: Gemma, Llama 3.1, …).
3. **Extracts and repairs** the raw LLM output into clean C, then **validates** it — structure checks, F4 HAL identifier validity, and a "did the code actually implement what was asked?" review — with a feedback retry loop on failure.
4. **Compile-checks** with PlatformIO (stm32cube HAL framework); compiler errors are fed back to the model for one corrective round.
5. **Flashes** the board over ST-LINK (PlatformIO + OpenOCD connect-under-reset hook) — only after explicit user confirmation.

It also has a **documentation chat mode**: ask anything about the STM32F4 (registers, peripherals, HAL) and get answers grounded in the indexed docs, with sources.

## Architecture (backend)

Three cleanly separated layers — the HTTP layer knows nothing about firmware, the agent core knows nothing about HTTP:

- **`agent/`** — orchestrator pipeline (retrieve → generate → validate → build → flash), RAG chat engine, LLM transport, prompt templates, output extraction, validation, PlatformIO builder, ST-LINK port detection, and a hardware-state analyzer that converts generated C into GPIO/clock/peripheral JSON for visualization.
- **`rag/`** — ChromaDB wrapper (local or Chroma Cloud), paragraph-aware chunking, PDF/md/txt loaders, and canonical board facts that are *always* included in retrieved context so a big manual can never outrank board-specific constraints.
- **`scraping/`** — polite, on-demand web scraping (robots.txt-aware, rate-limited, HTML cache) of STM32 tutorial sites (ControllersTech, MicroControllersLab, Memfault Interrupt) to grow the knowledge base. One small module = one new source.
- **`app/`** — FastAPI with dependency-injected singletons, pydantic schemas, persistent JSON job store (survives restarts), and routers: `/api/generate`, `/api/verify`, `/api/flash`, `/api/jobs`, `/api/chat`, `/api/ports`, `/health`.

**Safety gates:** compilation must pass before flashing is offered; flash requires `confirmed: true`; flash failures soft-fail with full PlatformIO logs; errors map cleanly to HTTP codes (503 setup, 502 LLM, 500 build).

**Testing:** offline self-check suite (extraction, validation, chunking, retrieval, persistence — no LLM or board needed) plus an end-to-end suite that generates, verifies, and flashes real firmware per use case (GPIO blink, PWM fade, UART, ADC, DMA…).

## Frontend

React 18 + TypeScript + Vite single-page console:

- **Command editor** with examples, Ctrl+Enter submit, auto-confirm toggle, and inline Generate / Flash buttons.
- **Source Code tab** — syntax-highlighted generated C with copy/download.
- **Hardware tab** — live visualization of the generated firmware: board LEDs (lit/unlit with GPIO modes), user button, clock tree (SYSCLK, PLL M/N/P/Q, AHB/APB prescalers), full GPIO pin table, configured peripherals, and ST-LINK debug info.
- **Build Logs tab** — streaming compile/flash output.
- **Docs Chat tab** — RAG Q&A with session history persisted server-side (MongoDB chat log).
- **Jobs tab** — full job history across restarts; load any previous job and re-flash it.
- **Flash confirmation dialog** with serial-port picker (ST-LINK auto-detection), settings modal (backend URL, theme, notifications), and backend health polling.

## Tech stack

| Layer | Tech |
|---|---|
| LLM | Ollama (OpenAI-compatible API), Gemma / Llama 3.1 |
| Backend | Python 3.10+, FastAPI, Pydantic v2, Uvicorn |
| RAG | ChromaDB (local or Chroma Cloud), sentence-transformers, pypdf |
| Scraping | BeautifulSoup4, robots-aware fetcher, disk cache |
| Embedded | PlatformIO (ststm32 / stm32cube HAL), STM32F407VGT6, OpenOCD, ST-LINK, pyserial |
| Frontend | React 18, TypeScript, Vite, lucide-react, react-syntax-highlighter |
| Storage | JSON job store, MongoDB chat log (optional) |

---

# LinkedIn Post

🚀 What if you could describe firmware in plain English… and watch it run on real silicon minutes later?

I built **EmbeddedAgent** — a full-stack AI agent that turns natural-language commands into compiled, verified, and flashed firmware on an STM32F4-Discovery board. No hand-written C. Just: *"blink the blue LED using DMA"* → working, flashed firmware.

Here's what happens under the hood:

🔍 **RAG-grounded generation** — the LLM (Ollama, Gemma/Llama) doesn't guess pin maps or HAL APIs. Board facts, datasheets, and scraped STM32 tutorials are indexed in ChromaDB and retrieved per request, so task knowledge lives in the docs — not in prompts.

✅ **Self-correcting pipeline** — generated code is extracted, validated (structure, HAL identifier validity, "does it actually do what was asked?"), then compile-checked with PlatformIO. Compiler errors are fed back into the model for a corrective round.

⚡ **Real hardware, real safety** — nothing flashes without a passed compile AND explicit user confirmation. Upload goes through ST-LINK with an OpenOCD connect-under-reset hook.

🖥️ **Full web console (React + TypeScript)** — live hardware visualization of the generated firmware (LED states, clock tree, GPIO table, peripherals), streaming build logs, a documentation chat with sources, and a persistent job history — load any previous build and re-flash it.

🏗️ **Clean architecture** — three separated layers: an agentic core with zero web-framework imports, a retrieval layer, and a FastAPI HTTP layer. Tested with an offline self-check suite plus an end-to-end suite that flashes real firmware for every use case.

My biggest takeaway: giving an LLM the *right context* (retrieval + compiler feedback loops) matters far more than writing clever prompts. That's what turns it from a code generator into an engineer.

Tech: Python · FastAPI · Ollama · ChromaDB · sentence-transformers · PlatformIO · STM32Cube HAL · OpenOCD · React 18 · TypeScript · Vite

#EmbeddedSystems #STM32 #AI #LLM #RAG #FastAPI #PlatformIO #Firmware #GenerativeAI #EdgeAI #Python #React
