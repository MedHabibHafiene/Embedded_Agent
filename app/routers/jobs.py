"""
Job inspection endpoints.

GET /api/jobs        - list all jobs (newest first), without the code payload.
GET /api/jobs/{id}   - full detail for one job (code, hardware state, logs).
"""

from fastapi import APIRouter, Depends, HTTPException

from app.deps import get_jobs
from app.jobs import JobStore
from app.schemas import JobDetail, JobSummary

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", response_model=list[JobSummary])
def list_jobs(jobs: JobStore = Depends(get_jobs)) -> list[JobSummary]:
    return [JobSummary(**_summary(job)) for job in jobs.list_jobs()]


@router.get("/{job_id}", response_model=JobDetail)
def get_job(job_id: str, jobs: JobStore = Depends(get_jobs)) -> JobDetail:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job ID not found")
    return JobDetail(**job)


def _summary(job: dict) -> dict:
    return {key: job.get(key) for key in JobSummary.model_fields}
