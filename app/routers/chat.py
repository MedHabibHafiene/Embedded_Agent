"""
Chat mode endpoints - mode 1 of the agent.

POST /api/chat                    - answer a question grounded in the indexed
                                    STM32 documentation. When a chat log store
                                    is configured, every turn is persisted: send
                                    session_id to resume, omit it to start a new
                                    session (its id comes back in the response).
GET  /api/chat/sessions           - list stored sessions, newest first.
GET  /api/chat/sessions/{id}      - full session with every message.
DELETE /api/chat/sessions/{id}    - remove a session.

Without MongoDB the POST endpoint degrades to the original stateless
contract: the client resends whatever history it wants considered.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from agent.chat import ChatEngine
from app.chatlog import ChatLogStore
from app.deps import get_chat, get_chatlog
from app.schemas import (
    ChatRequest,
    ChatResponse,
    ChatSessionDetail,
    ChatSessionSummary,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    engine: ChatEngine = Depends(get_chat),
    store: Optional[ChatLogStore] = Depends(get_chatlog),
) -> ChatResponse:
    history = [message.model_dump() for message in request.history]
    session_id = None

    if store is not None:
        if request.session_id:
            session = store.get_session(request.session_id)
            if session is None:
                raise HTTPException(status_code=404, detail="Unknown chat session")
            # The stored log is the source of truth for a resumed session.
            history = [
                {"role": m["role"], "content": m["content"]}
                for m in session["messages"]
            ]
        else:
            session_id = store.create_session(request.message)["id"]
        session_id = session_id or request.session_id

    result = engine.answer(request.message, history=history)

    if store is not None and session_id is not None:
        store.append_turn(session_id, request.message, result.answer, result.sources)

    return ChatResponse(
        answer=result.answer, sources=result.sources, session_id=session_id
    )


@router.get("/sessions", response_model=list[ChatSessionSummary])
def list_sessions(
    store: Optional[ChatLogStore] = Depends(get_chatlog),
) -> list[ChatSessionSummary]:
    if store is None:
        raise HTTPException(
            status_code=503, detail="Chat log is not configured (MongoDB disabled)"
        )
    return [ChatSessionSummary(**s) for s in store.list_sessions()]


@router.get("/sessions/{session_id}", response_model=ChatSessionDetail)
def get_session(
    session_id: str,
    store: Optional[ChatLogStore] = Depends(get_chatlog),
) -> ChatSessionDetail:
    if store is None:
        raise HTTPException(
            status_code=503, detail="Chat log is not configured (MongoDB disabled)"
        )
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown chat session")
    return ChatSessionDetail(
        message_count=len(session["messages"]),
        **{k: v for k, v in session.items() if k != "message_count"},
    )


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(
    session_id: str,
    store: Optional[ChatLogStore] = Depends(get_chatlog),
) -> None:
    if store is None:
        raise HTTPException(
            status_code=503, detail="Chat log is not configured (MongoDB disabled)"
        )
    if not store.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Unknown chat session")
