"""
Generation endpoints.

POST /api/generate        - generate firmware only (pure, no hardware access).
                             Blocks until the LLM finishes; response contract
                             is identical to the original API.
POST /generate-and-flash  - legacy full pipeline: generate, build, flash.

Both handlers are plain `def` (not `async def`) on purpose: the work is
blocking (LLM call, subprocess) and Starlette runs sync handlers in its
threadpool, so the event loop - and health checks - stay responsive during a
multi-minute generation.
"""

import logging

from fastapi import APIRouter, Depends

from agent.errors import GenerationError
from agent.orchestrator import AgentOrchestrator
from app.deps import get_jobs, get_orchestrator
from app.jobs import JobStore
from app.schemas import (
    BuildCheck,
    CommandRequest,
    GenerateAndFlashResponse,
    GenerateRequest,
    GenerateResponse,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["generation"])


@router.post("/api/generate", response_model=GenerateResponse)
def generate_firmware_endpoint(
    request: GenerateRequest,
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
    jobs: JobStore = Depends(get_jobs),
) -> GenerateResponse:
    if request.verify:
        # Self-testing flow: static validation + PlatformIO compile check,
        # with one corrective LLM round if the compiler rejects the code.
        # Upload never runs - safe with no board attached.
        result = orchestrator.generate_verified(request.prompt)
        status = "verified" if result.build_status == "success" else "compile_failed"
    else:
        result = orchestrator.generate(request.prompt)
        status = "generated"

    job = jobs.create(
        command=request.prompt,
        code=result.c_code,
        hardware_state=result.hardware_state,
        context=result.context,
        status=status,
    )
    if result.build_logs is not None:
        jobs.update(job["id"], logs=result.build_logs)
    log.info("Generated firmware for job %s (status: %s)", job["id"], status)
    return GenerateResponse(
        id=job["id"],
        code=result.c_code,
        hardware_state=result.hardware_state,
        build=BuildCheck(status=result.build_status, logs=result.build_logs or "")
        if result.build_status
        else None,
    )


@router.post("/generate-and-flash", response_model=GenerateAndFlashResponse)
def generate_and_flash(
    request: CommandRequest,
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
    jobs: JobStore = Depends(get_jobs),
) -> GenerateAndFlashResponse:
    result = orchestrator.run(request.command, auto_confirm=request.auto_confirm)
    jobs.create(
        command=request.command,
        code=result["c_code"],
        hardware_state=result["hardware_state"],
        context=result.get("context", ""),
        status="flashed",
    )
    return GenerateAndFlashResponse(
        status="success",
        message=f"Successfully processed command: {request.command}",
    )
