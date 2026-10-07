"""
Dependency providers: routers ask for singletons, FastAPI injects them.

The singletons (retriever, job store, orchestrator) are created once in the
app lifespan and attached to app.state - request handlers never build their
own clients anymore (the old code re-created the Ollama client and ChromaDB
store on every single request).
"""

from typing import Optional

from fastapi import Request

from agent.chat import ChatEngine
from agent.orchestrator import AgentOrchestrator
from app.chatlog import ChatLogStore
from app.jobs import JobStore
from rag import Retriever


def get_orchestrator(request: Request) -> AgentOrchestrator:
    return request.app.state.orchestrator


def get_chat(request: Request) -> ChatEngine:
    return request.app.state.chat


def get_chatlog(request: Request) -> Optional[ChatLogStore]:
    """None when MongoDB is not configured or was unreachable at startup."""
    return getattr(request.app.state, "chatlog", None)


def get_jobs(request: Request) -> JobStore:
    return request.app.state.jobs


def get_retriever(request: Request) -> Retriever:
    return request.app.state.retriever
