"""
Application factory: logging, lifespan singletons, error mapping, routers.

Error mapping (agent exceptions -> HTTP responses):
  - ConfigError     -> 503  (setup problem: missing key, model not pulled)
  - GenerationError -> 502  (LLM failed to produce valid firmware)
  - BuildError      -> 500  (compile/upload failed, full logs included)
  - anything else   -> 500  (with the message, as before)

Run:  python main_api.py      (or)   uvicorn app.main:app --host 0.0.0.0
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agent.errors import (
    AgentError,
    BuildError,
    ConfigError,
    GenerationError,
    LLMUnavailableError,
)
from agent.chat import ChatEngine
from agent.orchestrator import AgentOrchestrator
from app.chatlog import create_store_or_none
from app.jobs import JobStore
from app.routers import build, chat, flash, generate, health, jobs
from config import CORS_ORIGINS
from rag import Retriever

log = logging.getLogger(__name__)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create the shared singletons once, before the first request.

    The Ollama client is created lazily inside the orchestrator, so the
    server still boots (health, job listing, RAG queries work) even when
    Ollama is down or no API key is configured - that failure surfaces as a
    clean 503 on the endpoints that actually need the LLM.
    """
    log.info("Initializing RAG retriever (auto-seeds core facts if empty)...")
    retriever = Retriever()
    app.state.retriever = retriever
    app.state.jobs = JobStore()
    # Optional chat log (MongoDB): None when unconfigured/unreachable -
    # /api/chat then keeps its stateless contract with no persistence.
    app.state.chatlog = create_store_or_none()
    # Mode 2: firmware generation + flashing.
    app.state.orchestrator = AgentOrchestrator(retriever=retriever)
    # Mode 1: documentation chat - shares the same retriever and store.
    app.state.chat = ChatEngine(retriever=retriever)
    log.info("Startup complete.")
    yield


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ConfigError)
    async def _config_error(request: Request, exc: ConfigError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(GenerationError)
    async def _generation_error(request: Request, exc: GenerationError):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(BuildError)
    async def _build_error(request: Request, exc: BuildError):
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc), "logs": exc.logs},
        )

    @app.exception_handler(LLMUnavailableError)
    async def _llm_unavailable(request: Request, exc: LLMUnavailableError):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(AgentError)
    async def _agent_error(request: Request, exc: AgentError):
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    # Catch-all: never answer with Starlette's plain-text "Internal Server
    # Error" - surface the message (as the original API did) and log the
    # traceback for debugging.
    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception):
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500, content={"detail": f"Internal server error: {exc}"}
        )


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="STM32 Agent API",
        version="2.0.0",
        description=(
            "RAG-assisted STM32 firmware generation: retrieve hardware context, "
            "generate HAL C code with an LLM, validate it, then build and flash "
            "it to an STM32F4-Discovery board."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register_error_handlers(app)

    app.include_router(health.router)
    app.include_router(generate.router)
    app.include_router(flash.router)
    app.include_router(jobs.router)
    app.include_router(chat.router)
    app.include_router(build.router)
    return app


app = create_app()
