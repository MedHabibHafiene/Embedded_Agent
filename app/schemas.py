"""
API contract: every request/response model the backend exposes.

Kept separate from the internal job records (app/jobs.py) so the HTTP
contract can evolve independently of storage.
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

JobStatus = Literal["generated", "verified", "compile_failed", "flashing", "flashed", "failed"]


# ---------------------------------------------------------------- requests
class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Natural-language firmware request")
    verify: bool = Field(
        True,
        description="Compile-check the generated firmware with PlatformIO (no flashing).",
    )


class CommandRequest(BaseModel):
    command: str = Field(..., min_length=1)
    auto_confirm: bool = True


class FlashRequest(BaseModel):
    id: str
    confirmed: bool
    port: Optional[str] = Field(
        None, description="Upload port, e.g. COM3. Omit to let PlatformIO auto-detect."
    )


class VerifyRequest(BaseModel):
    id: str


class ChatMessage(BaseModel):
    """One previous conversation turn, oldest first."""

    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="Question for the documentation chat")
    history: list[ChatMessage] = Field(
        default_factory=list,
        description="Previous turns of this conversation (oldest first). Optional.",
    )
    session_id: Optional[str] = Field(
        None,
        description="Resume this chat session from the MongoDB chat log. "
        "When given, the stored history is used instead of `history`.",
    )


class ChatSessionSummary(BaseModel):
    id: str
    title: str
    message_count: int
    created_at: str
    updated_at: str


class ChatSessionDetail(ChatSessionSummary):
    messages: list[dict[str, Any]]


# --------------------------------------------------------------- responses
class GenerateResponse(BaseModel):
    """Contract preserved from the original API (id/code/hardware_state),
    plus the compile-check outcome when verification is requested."""

    id: str
    code: str
    hardware_state: dict[str, Any]
    build: Optional["BuildCheck"] = None


class GenerateAndFlashResponse(BaseModel):
    status: str
    message: str


class FlashResponse(BaseModel):
    """Soft-fail contract preserved: build failures return 200 with
    status='error' and the full PlatformIO logs, not a 500."""

    status: Literal["success", "error"]
    logs: str


class ChatResponse(BaseModel):
    answer: str
    sources: list[str] = Field(default_factory=list)
    session_id: Optional[str] = Field(
        None, description="Chat-log session this turn was stored under (null when persistence is off)."
    )


class BuildCheck(BaseModel):
    """Outcome of a compile-only check (no hardware touched)."""

    status: Literal["success", "error"]
    logs: str


class PortInfo(BaseModel):
    device: Optional[str] = None
    description: str = ""
    hwid: str = ""
    st_link: bool = False


class JobSummary(BaseModel):
    id: str
    command: str
    status: JobStatus
    created_at: str
    updated_at: str
    error: Optional[str] = None


class JobDetail(JobSummary):
    code: str
    hardware_state: dict[str, Any]
    context: str = ""
    logs: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    components: Optional[dict[str, Any]] = None


# GenerateResponse references BuildCheck before its definition above.
GenerateResponse.model_rebuild()
