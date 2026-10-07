"""
Chat log persistence: every documentation-chat turn survives restarts.

Sessions live in MongoDB (one document per conversation, messages appended
as the chat goes), so coming back to the chat later restores the full
history - previously the server was stateless and the browser tab held the
only copy of the conversation.

This is deliberately an OPTIONAL dependency of the chat mode: when
MONGODB_URI is unset or Atlas is unreachable at startup, app.main simply
does not create the store and /api/chat keeps its original stateless
contract (the client resends the history it still has).
"""

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from config import MONGODB_DB, MONGODB_URI

log = logging.getLogger(__name__)

SESSIONS_COLLECTION = "chat_sessions"
MAX_TITLE_LENGTH = 60


class ChatLogStore:
    """One MongoDB collection of chat sessions ("chatlogs")."""

    def __init__(
        self,
        uri: Optional[str] = MONGODB_URI,
        database: str = MONGODB_DB,
        timeout_ms: int = 5000,
    ):
        if not uri:
            raise ValueError("MONGODB_URI is not configured")
        from pymongo import MongoClient
        from pymongo.errors import PyMongoError

        # Fail fast: Atlas being down must not stall server startup - the
        # caller catches this and falls back to stateless chat.
        self._client = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms)
        try:
            self._client.admin.command("ping")
        except PyMongoError as e:
            raise ConnectionError(f"MongoDB is unreachable: {e}") from e
        self._sessions = self._client[database][SESSIONS_COLLECTION]
        self._sessions.create_index("updated_at")

    # ------------------------------------------------------------ sessions
    def create_session(self, first_message: str) -> dict:
        """Start a new session, titled after the first user message."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        session = {
            "id": str(uuid.uuid4()),
            "title": self._title_from(first_message),
            "messages": [],
            "created_at": now,
            "updated_at": now,
        }
        self._sessions.insert_one(session)
        log.info("Created chat session %s (%r)", session["id"], session["title"])
        return session

    def append_turn(
        self,
        session_id: str,
        question: str,
        answer: str,
        sources: list[str],
    ) -> Optional[dict]:
        """Append one user/assistant exchange to an existing session."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        result = self._sessions.find_one_and_update(
            {"id": session_id},
            {
                "$push": {
                    "messages": {
                        "$each": [
                            {"role": "user", "content": question, "ts": now},
                            {
                                "role": "assistant",
                                "content": answer,
                                "sources": sources,
                                "ts": now,
                            },
                        ]
                    }
                },
                "$set": {"updated_at": now},
            },
            return_document=True,
        )
        if result is None:
            log.warning("append_turn: no chat session %s", session_id)
            return None
        result.pop("_id", None)
        return result

    def get_session(self, session_id: str) -> Optional[dict]:
        doc = self._sessions.find_one({"id": session_id}, {"_id": 0})
        return doc

    def list_sessions(self) -> list[dict]:
        """Newest-first summaries; message bodies are left out of the list."""
        cursor = self._sessions.find(
            {},
            {"_id": 0, "id": 1, "title": 1, "created_at": 1, "updated_at": 1,
             "message_count": {"$size": "$messages"}},
        ).sort("updated_at", -1)
        return list(cursor)

    def delete_session(self, session_id: str) -> bool:
        return self._sessions.delete_one({"id": session_id}).deleted_count == 1

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _title_from(message: str) -> str:
        """Compact one-line title derived from the first question."""
        title = re.sub(r"\s+", " ", message).strip()
        if len(title) > MAX_TITLE_LENGTH:
            title = title[: MAX_TITLE_LENGTH - 1].rstrip() + "…"
        return title or "Untitled chat"


def create_store_or_none() -> Optional[ChatLogStore]:
    """Build the store if MongoDB is configured and reachable, else None.

    Chat persistence is a convenience, not a hard requirement - a startup
    failure here logs loudly and the server keeps serving stateless chat.
    """
    try:
        return ChatLogStore()
    except (ValueError, ConnectionError) as e:
        log.warning("Chat log disabled (%s) - chat runs without persistence.", e)
        return None
