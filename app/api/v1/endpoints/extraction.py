from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Response, UploadFile, status

from app.deps import ExtractionServiceDep
from app.schemas.extraction import CreateJobResponse, ErrorDetail, ExtractionJobDetail, JobListResponse

router = APIRouter()


@router.post(
    "/jobs",
    response_model=CreateJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create an extraction job",
    response_description="Queued extraction job with encrypted source storage.",
    responses={
        400: {"description": "Invalid file type.", "model": ErrorDetail},
        500: {"description": "Extraction pipeline error.", "model": ErrorDetail},
    },
)
async def create_job(
    background_tasks: BackgroundTasks,
    service: ExtractionServiceDep,
    file: UploadFile | None = File(None),
    vault_file_id: str | None = Form(None),
) -> CreateJobResponse:
    if bool(file) == bool(vault_file_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Send either a PDF upload or a vault_file_id.",
        )

    if vault_file_id:
        job = await service.create_job_from_vault_file(vault_file_id)
    else:
        filename = file.filename or "upload.pdf"
        is_pdf = file.content_type == "application/pdf" or filename.lower().endswith(".pdf")
        if not is_pdf:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid file type. Only PDF files are accepted.",
            )
        job = await service.create_job(filename=filename, file_content=await file.read())

    if job.status == "queued":
        background_tasks.add_task(service.process_job, job.id)
    return CreateJobResponse(job=job)


@router.get(
    "/jobs",
    response_model=JobListResponse,
    summary="List extraction jobs",
)
def list_jobs(service: ExtractionServiceDep) -> JobListResponse:
    return JobListResponse(jobs=service.list_jobs())


@router.get(
    "/jobs/{job_id}",
    response_model=ExtractionJobDetail,
    summary="Get extraction job detail",
)
def get_job(job_id: str, service: ExtractionServiceDep) -> ExtractionJobDetail:
    return service.get_job(job_id)


@router.get(
    "/jobs/{job_id}/source",
    summary="Download the encrypted source PDF as an inline preview",
    responses={404: {"description": "Job not found.", "model": ErrorDetail}},
)
def get_source_document(job_id: str, service: ExtractionServiceDep) -> Response:
    filename, payload = service.get_source_document(job_id)
    headers = {"Content-Disposition": f'inline; filename="{filename}"'}
    return Response(content=payload, media_type="application/pdf", headers=headers)


@router.get(
    "/jobs/{job_id}/download",
    summary="Download the generated Word-compatible .doc artifact",
    responses={404: {"description": "Job not found.", "model": ErrorDetail}},
)
def get_export_document(job_id: str, service: ExtractionServiceDep) -> Response:
    filename, payload = service.get_export_document(job_id)
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(content=payload, media_type="application/msword", headers=headers)


@router.delete(
    "/jobs/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an extraction job and its encrypted artifacts",
    responses={404: {"description": "Job not found.", "model": ErrorDetail}},
)
def delete_job(job_id: str, service: ExtractionServiceDep) -> Response:
    service.delete_job(job_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
