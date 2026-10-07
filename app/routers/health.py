"""
Health endpoint.

GET /health             - cheap liveness probe (always the same shape).
GET /health?verbose=1   - adds component status: vector store size, ST-LINK
                          port, Ollama reachability. Component checks are kept
                          off the default path so monitoring stays fast.
"""

from fastapi import APIRouter, Depends, Request

from agent import llm, ports
from app.deps import get_retriever
from app.schemas import HealthResponse
from rag import Retriever

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(
    request: Request,
    verbose: bool = False,
    retriever: Retriever = Depends(get_retriever),
) -> HealthResponse:
    if not verbose:
        return HealthResponse(status="healthy")

    available_models = llm.list_available_models()
    stlink_port = ports.detect_stlink_port()
    return HealthResponse(
        status="healthy",
        components={
            "vector_store_documents": retriever.store.count(),
            "ollama": {
                "reachable": bool(available_models),
                "models": available_models,
            },
            "st_link_port": stlink_port,
            "chat_log": "enabled" if getattr(request.app.state, "chatlog", None) else "disabled",
        },
    )
