"""
Flash endpoint.

POST /api/flash - compile + upload a previously generated firmware job.

Build failures keep the original soft-fail contract: HTTP 200 with
status='error' and the full PlatformIO logs (the real compiler/linker error
is usually on stdout). The job record tracks flashing/flashed/failed so the
outcome survives restarts.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from agent.errors import BuildError
from agent.orchestrator import AgentOrchestrator
from app.deps import get_jobs, get_orchestrator
from app.jobs import JobStore
from app.schemas import FlashRequest, FlashResponse

log = logging.getLogger(__name__)

router = APIRouter(tags=["flashing"])


@router.post("/api/flash", response_model=FlashResponse)
def flash_firmware_endpoint(
    request: FlashRequest,
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
    jobs: JobStore = Depends(get_jobs),
) -> FlashResponse:
    job = jobs.get(request.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job ID not found")
    if not request.confirmed:
        raise HTTPException(status_code=400, detail="Flash not confirmed")

    jobs.update(request.id, status="flashing")
    try:
        outcome = orchestrator.flash(
            job["code"], command=job.get("command", ""), port=request.port
        )
    except BuildError as e:
        log.warning("Flash failed for job %s: %s", request.id, e)
        jobs.update(request.id, status="failed", logs=e.logs, error=str(e))
        return FlashResponse(status="error", logs=e.logs)

    jobs.update(request.id, status="flashed", logs=outcome.logs)
    log.info("Flashed job %s to the board.", request.id)
    return FlashResponse(status="success", logs=outcome.logs)
