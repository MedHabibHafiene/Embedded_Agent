"""
Build & hardware endpoints.

POST /api/verify - compile-check a stored job's firmware with PlatformIO.
                     Never uploads - safe with no board attached.
GET  /api/ports  - list the serial ports currently visible on this machine,
                     so the client can offer a port picker for flashing
                     (the port is never guessed server-side).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from agent.errors import BuildError
from agent.orchestrator import AgentOrchestrator
from agent.ports import list_serial_ports
from app.deps import get_jobs, get_orchestrator
from app.jobs import JobStore
from app.schemas import BuildCheck, PortInfo, VerifyRequest

log = logging.getLogger(__name__)

router = APIRouter(tags=["build"])


@router.post("/api/verify", response_model=BuildCheck)
def verify_firmware_endpoint(
    request: VerifyRequest,
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
    jobs: JobStore = Depends(get_jobs),
) -> BuildCheck:
    job = jobs.get(request.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job ID not found")

    try:
        outcome = orchestrator.verify(job["code"], command=job.get("command", ""))
    except BuildError as e:
        log.warning("Compile check failed for job %s", request.id)
        jobs.update(request.id, status="compile_failed", logs=e.logs)
        return BuildCheck(status="error", logs=e.logs)

    jobs.update(request.id, status="verified", logs=outcome.logs)
    log.info("Job %s compiled cleanly (no flash attempted).", request.id)
    return BuildCheck(status="success", logs=outcome.logs)


@router.get("/api/ports", response_model=list[PortInfo])
def list_ports() -> list[PortInfo]:
    return [PortInfo(**port) for port in list_serial_ports()]
